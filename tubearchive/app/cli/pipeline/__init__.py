"""파이프라인 관련 함수 및 데이터 클래스.

트랜스코딩·병합·DB 저장·후처리(BGM, 자막, 아카이브, 백업 등)
파이프라인 구성 요소를 담는다.

``main.py`` 에서 re-export 되므로 외부 임포트 경로는 변경 없이
``tubearchive.app.cli.main`` 을 그대로 사용할 수 있다.
"""

from __future__ import annotations

import fcntl
import logging
import shutil
from pathlib import Path

from tubearchive.app.cli.context import PipelineContext

# Re-export submodule symbols so ``tubearchive.app.cli.pipeline.<name>`` keeps
# exposing every public/private name it did before the package split.
from tubearchive.app.cli.pipeline.archive import (
    _archive_originals,
    _prompt_archive_delete_confirmation,
    _run_backup,
)
from tubearchive.app.cli.pipeline.io_utils import (
    _emit_progress,
    _get_media_duration,
    _has_audio_stream,
    _to_video_file,
    check_output_disk_space,
    get_output_filename,
    get_temp_dir,
)
from tubearchive.app.cli.pipeline.persistence import (
    _link_merge_job_to_project,
    _mark_transcoding_jobs_merged,
    save_merge_job_to_db,
)
from tubearchive.app.cli.pipeline.postprocess import (
    _apply_bgm_mixing,
    _apply_post_merge_loudnorm,
    _apply_subtitle_burn,
    _detect_silence_only,
    _generate_thumbnails,
    _generate_timelapse,
    _print_quality_report,
)
from tubearchive.app.cli.pipeline.single_file import (
    handle_single_file_upload,
)
from tubearchive.app.cli.pipeline.transcode import (
    TranscodeOptions,
    TranscodeResult,
    _can_skip_transcoding,
    _collect_clip_info,
    _make_watermark_text,
    _run_skip_transcoding,
    _transcode_parallel,
    _transcode_sequential,
    _transcode_single,
)
from tubearchive.app.cli.validators import ValidatedArgs
from tubearchive.config import HooksConfig
from tubearchive.domain.media.audio_sync import ExternalAudioSegment
from tubearchive.domain.media.grouper import (
    FileSequenceGroup,
    compute_fade_map,
    group_sequences,
    reorder_with_groups,
)
from tubearchive.domain.media.hooks import HookContext, run_hooks
from tubearchive.domain.media.merger import Merger
from tubearchive.domain.media.ordering import (
    SortKey,
    filter_videos,
    interactive_reorder,
    print_video_list,
    sort_videos,
)
from tubearchive.domain.media.scanner import scan_videos
from tubearchive.domain.models.video import FadeConfig, VideoFile
from tubearchive.domain.services.external_audio import (
    analyze_long_external_audio,
    analyze_long_external_audio_from_dir,
    apply_clip_adjustments,
)
from tubearchive.infra.db.repository import SplitJobRepository
from tubearchive.shared.progress import format_size

logger = logging.getLogger(__name__)

# Names exported from the package namespace. Listing the re-exported submodule
# symbols here marks them as explicit exports for mypy's implicit-reexport check.
__all__ = [
    "TranscodeOptions",
    "TranscodeResult",
    "_apply_bgm_mixing",
    "_apply_ordering",
    "_apply_post_merge_loudnorm",
    "_apply_subtitle_burn",
    "_archive_originals",
    "_can_skip_transcoding",
    "_cleanup_temp",
    "_cmd_dry_run",
    "_collect_clip_info",
    "_detect_silence_only",
    "_emit_progress",
    "_generate_thumbnails",
    "_generate_timelapse",
    "_get_media_duration",
    "_has_audio_stream",
    "_is_file_in_use",
    "_link_merge_job_to_project",
    "_make_watermark_text",
    "_mark_transcoding_jobs_merged",
    "_print_quality_report",
    "_print_summary",
    "_prompt_archive_delete_confirmation",
    "_resolve_output_path",
    "_run_backup",
    "_run_error_hook",
    "_run_skip_transcoding",
    "_to_video_file",
    "_transcode_parallel",
    "_transcode_sequential",
    "_transcode_single",
    "check_output_disk_space",
    "get_output_filename",
    "get_temp_dir",
    "handle_single_file_upload",
    "run_pipeline",
    "save_merge_job_to_db",
]


def _apply_ordering(
    video_files: list[VideoFile],
    validated_args: ValidatedArgs,
    *,
    allow_interactive: bool = True,
) -> list[VideoFile]:
    """필터링·정렬·인터랙티브 재정렬을 순차 적용한다.

    Args:
        video_files: 스캔된 영상 파일 리스트
        validated_args: 검증된 CLI 인자
        allow_interactive: ``--reorder`` 인터랙티브 모드 허용 여부
            (dry-run에서는 False)

    Returns:
        최종 순서의 영상 파일 리스트

    Raises:
        ValueError: 필터 적용 후 파일이 없거나 재정렬 후 파일이 없을 때
    """
    if validated_args.exclude_patterns or validated_args.include_only_patterns:
        video_files = filter_videos(
            video_files,
            exclude_patterns=validated_args.exclude_patterns,
            include_only_patterns=validated_args.include_only_patterns,
        )
        if not video_files:
            raise ValueError("All files excluded by filter patterns")

    if validated_args.sort_key != "time":
        video_files = sort_videos(video_files, SortKey(validated_args.sort_key))

    if allow_interactive and validated_args.reorder:
        video_files = interactive_reorder(video_files)
        if not video_files:
            raise ValueError("No files remaining after reorder")

    return video_files


def _resolve_output_path(validated_args: ValidatedArgs) -> Path:
    """출력 파일 경로를 결정한다.

    우선순위: ``--output`` 직접 지정 > ``--output-dir`` + 자동 파일명.

    Args:
        validated_args: 검증된 CLI 인자

    Returns:
        최종 출력 파일 경로
    """
    if validated_args.output:
        return validated_args.output
    output_filename = get_output_filename(validated_args.targets)
    output_dir = validated_args.output_dir or Path.cwd()
    return output_dir / output_filename


def _is_file_in_use(path: Path) -> bool:
    """파일이 다른 프로세스에 의해 사용 중인지 확인한다 (비차단).

    배타적 락(LOCK_EX | LOCK_NB) 획득을 시도하여 다른 프로세스가
    파일을 열고 있는지 감지한다. 락 획득 실패 시 사용 중으로 판단.
    """
    try:
        with path.open("r+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except OSError:
        return True


def _cleanup_temp(
    temp_dir: Path,
    results: list[TranscodeResult],
    final_path: Path,
) -> None:
    """임시 파일 및 폴더를 정리한다.

    트랜스코딩 스킵(stream-copy concat) 경로에서는
    ``TranscodeResult.output_path``가 *원본 입력 파일 경로*를 가리킨다.
    이를 무차별 unlink하면 사용자의 원본 클립이 삭제되므로,
    ``temp_dir`` 하위의 파일만 삭제하도록 제한한다.
    """
    logger.info("Cleaning up temporary files...")
    for r in results:
        if (
            r.output_path.exists()
            and r.output_path != final_path
            and temp_dir in r.output_path.parents
        ):
            if _is_file_in_use(r.output_path):
                logger.warning(f"  Skipping (in use by another process): {r.output_path}")
            else:
                r.output_path.unlink()
                logger.debug(f"  Removed: {r.output_path}")

    # 임시 폴더 삭제
    if temp_dir.exists():
        try:
            shutil.rmtree(temp_dir)
            logger.info(f"Removed temp directory: {temp_dir}")
        except OSError as e:
            logger.warning(f"Failed to remove temp directory: {e}")


def _print_summary(summary_markdown: str | None) -> None:
    """병합 요약 마크다운을 구분선과 함께 콘솔에 출력한다.

    Args:
        summary_markdown: 출력할 마크다운 문자열. ``None`` 이면 무시.
    """
    if not summary_markdown:
        return
    print("\n" + "=" * 60)
    print("📋 SUMMARY (Copy & Paste)")
    print("=" * 60)
    print(summary_markdown)
    print("=" * 60 + "\n")


def _run_error_hook(
    hooks: HooksConfig,
    error: Exception,
    *,
    output_path: Path | None = None,
    validated_args: ValidatedArgs | None = None,
) -> None:
    """실패 시 on_error 훅을 실행."""
    # Lazy import: callers patch tubearchive.app.cli.main.run_hooks
    from tubearchive.app.cli.main import run_hooks as _run_hooks  # type: ignore[attr-defined]

    input_paths = tuple(validated_args.targets) if validated_args is not None else ()
    _run_hooks(
        hooks,
        "on_error",
        context=HookContext(
            output_path=output_path,
            input_paths=input_paths,
            error_message=str(error),
        ),
    )


def run_pipeline(
    validated_args: ValidatedArgs,
    context: PipelineContext | None = None,
    generated_thumbnail_paths: list[Path] | None = None,
    generated_subtitle_paths: list[Path] | None = None,
) -> Path:
    """
    전체 파이프라인 실행.

    스캔 → 트랜스코딩 → 병합 → DB 저장 → 정리 → Summary 출력

    Args:
        validated_args: 검증된 인자
        context: 파이프라인 실행 컨텍스트 (notifier + on_progress 콜백, None이면 비활성화)
        generated_thumbnail_paths: 썸네일 생성 결과 저장용 출력 버퍼 (기본값 None)
        generated_subtitle_paths: 자막 생성 결과 저장용 출력 버퍼 (기본값 None)

    Returns:
        최종 출력 파일 경로
    """
    notifier = context.notifier if context else None
    # 1. 파일 스캔
    logger.info("Scanning video files...")
    video_files = scan_videos(validated_args.targets)

    if not video_files:
        logger.error("No video files found")
        raise ValueError("No video files found")

    logger.info(f"Found {len(video_files)} video files")
    for video_file in video_files:
        logger.info(f"  - {video_file.path.name}")

    video_files = _apply_ordering(video_files, validated_args)

    has_external_audio = validated_args.external_audio_path or validated_args.external_audio_dir
    if (
        has_external_audio
        and validated_args.external_audio_scope != "long"
        and len(video_files) != 1
    ):
        raise ValueError("external audio currently supports exactly one input video")

    # --detect-silence: 분석만 수행 후 종료
    if validated_args.detect_silence:
        _detect_silence_only(video_files, validated_args)
        return Path()  # 빈 경로 반환

    # 단일 파일 + --upload 시 빠른 경로
    if (
        len(video_files) == 1
        and validated_args.upload
        and validated_args.external_audio_path is None
        and validated_args.external_audio_dir is None
        and validated_args.template_intro is None
        and validated_args.template_outro is None
    ):
        return handle_single_file_upload(video_files[0], validated_args)

    # 템플릿 삽입 (템플릿은 검증 및 파일 존재 확인을 validate_args에서 수행)
    main_video_files = list(video_files)
    template_intro_file: VideoFile | None = None
    template_outro_file: VideoFile | None = None
    main_paths = {vf.path for vf in main_video_files}

    if validated_args.template_intro and validated_args.template_intro not in main_paths:
        template_intro_file = _to_video_file(validated_args.template_intro)

    if (
        validated_args.template_outro
        and validated_args.template_outro not in main_paths
        and (
            template_intro_file is None or template_intro_file.path != validated_args.template_outro
        )
    ):
        template_outro_file = _to_video_file(validated_args.template_outro)

    template_intro_count = 1 if template_intro_file is not None else 0
    template_outro_count = 1 if template_outro_file is not None else 0

    # 1.5 그룹핑 및 재정렬
    if validated_args.group_sequences:
        groups = group_sequences(main_video_files)
        main_video_files = reorder_with_groups(main_video_files, groups)
        for group in groups:
            if len(group.files) > 1:
                logger.info(
                    "연속 시퀀스 감지: %s (%d개 파일)",
                    group.group_id,
                    len(group.files),
                )
    else:
        groups = [
            FileSequenceGroup(files=(video_file,), group_id=f"s_{i}")
            for i, video_file in enumerate(main_video_files)
        ]

    fade_map = compute_fade_map(
        groups=groups,
        default_fade=validated_args.fade_duration,
    )

    # 2. 트랜스코딩용 임시 디렉토리 (이후 단계에서도 공유)
    temp_dir = get_temp_dir()
    logger.info(f"Using temp directory: {temp_dir}")

    external_audio_segments: dict[Path, ExternalAudioSegment] | None = None
    if validated_args.external_audio_scope == "long":
        if validated_args.external_audio_path:
            external_audio_segments = analyze_long_external_audio(
                main_video_files,
                validated_args.external_audio_path,
                validated_args.external_audio_min_confidence,
                use_clap_sync=validated_args.sync_audio_clap,
                wav_start_offset_seconds=validated_args.external_audio_wav_offset,
            )
        elif validated_args.external_audio_dir:
            external_audio_segments = analyze_long_external_audio_from_dir(
                main_video_files,
                validated_args.external_audio_dir,
                temp_dir,
            )

        if external_audio_segments and validated_args.external_audio_clip_adjustments:
            external_audio_segments = apply_clip_adjustments(
                external_audio_segments,
                validated_args.external_audio_clip_adjustments,
            )

    video_files = list(main_video_files)
    if template_intro_file is not None:
        video_files.insert(0, template_intro_file)
        fade_map[template_intro_file.path] = FadeConfig(
            fade_in=validated_args.fade_duration,
            fade_out=0.0,
        )
        first_main = main_video_files[0]
        first_fade = fade_map.get(first_main.path)
        if first_fade is not None:
            fade_map[first_main.path] = FadeConfig(
                fade_in=0.0,
                fade_out=first_fade.fade_out,
            )

    if template_outro_file is not None:
        video_files.append(template_outro_file)
        fade_map[template_outro_file.path] = FadeConfig(
            fade_in=0.0,
            fade_out=validated_args.fade_duration,
        )
        last_main = main_video_files[-1]
        last_fade = fade_map.get(last_main.path)
        if last_fade is not None:
            fade_map[last_main.path] = FadeConfig(
                fade_in=last_fade.fade_in,
                fade_out=0.0,
            )
    # 2. 트랜스코딩

    transcode_opts = TranscodeOptions(
        denoise=validated_args.denoise,
        denoise_level=validated_args.denoise_level,
        external_audio_path=(
            None
            if validated_args.external_audio_scope == "long"
            else validated_args.external_audio_path
        ),
        external_audio_dir=validated_args.external_audio_dir,
        external_audio_scope=validated_args.external_audio_scope,
        external_audio_segments=external_audio_segments,
        sync_audio_clap=validated_args.sync_audio_clap,
        external_audio_drift_correction=validated_args.external_audio_drift_correction,
        external_audio_offset=validated_args.external_audio_offset,
        external_audio_mode=validated_args.external_audio_mode,
        camera_audio_volume=validated_args.camera_audio_volume,
        external_audio_min_confidence=validated_args.external_audio_min_confidence,
        external_audio_match_window=validated_args.external_audio_match_window,
        fade_map=fade_map,
        fade_duration=validated_args.fade_duration,
        trim_silence=validated_args.trim_silence,
        silence_threshold=validated_args.silence_threshold,
        silence_min_duration=validated_args.silence_min_duration,
        stabilize=validated_args.stabilize,
        stabilize_strength=validated_args.stabilize_strength,
        stabilize_crop=validated_args.stabilize_crop,
        lut_path=validated_args.lut_path,
        auto_lut=validated_args.auto_lut,
        lut_before_hdr=validated_args.lut_before_hdr,
        device_luts=validated_args.device_luts,
        video_denoise=validated_args.video_denoise,
        video_denoise_strength=validated_args.video_denoise_strength,
        wb_kelvin=validated_args.wb_kelvin,
        auto_white_balance=validated_args.auto_white_balance,
        device_wb=validated_args.device_wb,
        watermark=validated_args.watermark,
        watermark_text=validated_args.watermark_text or None,
        watermark_pos=validated_args.watermark_pos,
        watermark_size=validated_args.watermark_size,
        watermark_color=validated_args.watermark_color,
        watermark_alpha=validated_args.watermark_alpha,
    )

    if validated_args.stabilize:
        logger.info(
            "영상 안정화 활성화 (vidstab 2-pass, strength=%s, crop=%s) "
            "— 트랜스코딩 시간이 증가합니다",
            validated_args.stabilize_strength,
            validated_args.stabilize_crop,
        )

    # 트랜스코딩 스킵 검사: 모든 입력이 이미 PROFILE_SDR과 정합하고
    # 어떤 필터도 활성화되지 않은 경우, 트랜스코딩 단계를 통째로 건너뛰고
    # 원본 파일을 그대로 concat demuxer로 stream-copy 병합한다.
    # 메타데이터 캐시를 반환받아 스킵 분기의 재-probe(ffprobe 2N → N)를 방지한다.
    can_skip, skip_reason, metadata_cache = _can_skip_transcoding(
        main_video_files,
        transcode_opts,
        validated_args,
        template_intro_file,
        template_outro_file,
    )
    parallel = validated_args.parallel
    if can_skip:
        logger.info(f"트랜스코딩 스킵 (stream-copy 모드): {skip_reason}")
        # 스킵이 가능한 경우 템플릿이 없음이 보장되므로 video_files == main_video_files.
        results = _run_skip_transcoding(main_video_files, temp_dir, metadata_cache, context=context)
    elif parallel > 1:
        logger.debug(f"Skip not eligible: {skip_reason}")
        logger.info(f"Starting parallel transcoding (workers: {parallel})...")
        results = _transcode_parallel(
            video_files,
            temp_dir,
            parallel,
            transcode_opts,
            context=context,
            metadata_cache=metadata_cache,
        )
    else:
        logger.debug(f"Skip not eligible: {skip_reason}")
        logger.info("Starting transcoding...")
        results = _transcode_sequential(
            video_files,
            temp_dir,
            transcode_opts,
            context=context,
            metadata_cache=metadata_cache,
        )

    video_ids = [r.video_id for r in results]
    main_start = template_intro_count
    main_end = len(results) - template_outro_count if template_outro_count else len(results)
    if main_start >= main_end:
        # 템플릿만 들어간 경우(또는 인덱스 역전) 대비: 전체 결과를 사용
        main_start = 0
        main_end = len(results)
    main_results = results[main_start:main_end]
    main_video_files = video_files[main_start:main_end]
    if not main_video_files:
        main_video_files = video_files
        main_video_ids = [r.video_id for r in results]
        main_video_clips = [r.clip_info for r in results]
    else:
        main_video_ids = [r.video_id for r in main_results]
        main_video_clips = [r.clip_info for r in main_results]

    run_hooks(
        validated_args.hooks,
        "on_transcode",
        context=HookContext(
            input_paths=tuple(vf.path for vf in video_files),
            output_path=results[0].output_path if results else None,
        ),
    )

    # 알림: 트랜스코딩 완료
    if notifier:
        from tubearchive.infra.notification import transcode_complete_event

        notifier.notify(
            transcode_complete_event(
                file_count=len(main_results),
                total_duration=sum(r.clip_info.duration for r in main_results),
            )
        )

    # 3. 병합
    logger.info("Merging videos...")
    output_path = _resolve_output_path(validated_args)
    final_path = Merger(temp_dir=temp_dir).merge(
        [r.output_path for r in results],
        output_path,
    )
    logger.info(f"Final output: {final_path}")

    run_hooks(
        validated_args.hooks,
        "on_merge",
        context=HookContext(
            output_path=final_path,
            input_paths=tuple(vf.path for vf in video_files),
        ),
    )

    # 알림: 병합 완료
    if notifier:
        from tubearchive.infra.notification import merge_complete_event

        notifier.notify(
            merge_complete_event(
                output_path=str(final_path),
                file_count=len(main_results),
                total_size_bytes=final_path.stat().st_size if final_path.exists() else 0,
            )
        )

    # 3.4 라우드니스 정규화 (옵션) — 병합 결과 전체에 1회 적용
    # 클립별이 아닌 한 번에 측정/정규화하므로 클립 간 상대 라우드니스가 보존된다.
    # BGM 믹싱은 정규화된 원본 오디오에 BGM 비율을 적용해야 의도대로 동작하므로
    # 반드시 BGM 단계보다 먼저 수행한다.
    if validated_args.normalize_audio:
        logger.info("Applying post-merge loudnorm...")
        temp_loud_output = temp_dir / f"loudnorm_{final_path.name}"
        normalized_path = _apply_post_merge_loudnorm(
            video_path=final_path,
            output_path=temp_loud_output,
        )
        # 정규화가 스킵된 경우(오디오 없음·분석 실패) 원본 경로 그대로 반환되므로
        # ``shutil.move`` 호출 시 ``SameFileError``가 발생한다. 경로가 동일하면 무동작.
        if normalized_path != final_path:
            shutil.move(str(normalized_path), str(final_path))
            logger.info(f"Loudnorm applied: {final_path}")
        else:
            logger.info("Loudnorm skipped (no audio or analysis failed); keeping merged output")

    # 3.5 BGM 믹싱 (옵션)
    if validated_args.bgm_path:
        logger.info("Applying BGM mixing...")
        temp_bgm_output = temp_dir / f"bgm_mixed_{final_path.name}"
        bgm_mixed_path = _apply_bgm_mixing(
            video_path=final_path,
            bgm_path=validated_args.bgm_path,
            bgm_volume=validated_args.bgm_volume,
            bgm_loop=validated_args.bgm_loop,
            output_path=temp_bgm_output,
        )
        # 원본을 BGM 믹싱된 파일로 대체
        shutil.move(str(bgm_mixed_path), str(final_path))
        logger.info(f"BGM mixing applied: {final_path}")

    # 4.1 자막 생성/하드코딩 (선택)
    subtitle_path: Path | None = None
    if validated_args.subtitle:
        from tubearchive.domain.media.subtitle import generate_subtitles

        logger.info("Generating subtitles for merged output...")
        generated = final_path.with_suffix(f".{validated_args.subtitle_format}")
        subtitle_result = generate_subtitles(
            final_path,
            model=validated_args.subtitle_model,
            language=validated_args.subtitle_lang,
            output_format=validated_args.subtitle_format,
            output_path=generated,
        )
        subtitle_path = subtitle_result.subtitle_path
        if subtitle_result.detected_language and validated_args.subtitle_lang is None:
            validated_args.subtitle_lang = subtitle_result.detected_language
        if generated_subtitle_paths is not None:
            generated_subtitle_paths.append(subtitle_path)

        if validated_args.subtitle_burn:
            logger.info("Applying hardcoded subtitles...")
            burned_path = _apply_subtitle_burn(
                input_path=final_path,
                subtitle_path=subtitle_path,
            )
            # 원본을 burned 파일로 교체하여 --output 경로를 유지
            final_path.unlink(missing_ok=True)
            burned_path.rename(final_path)

    # 4.1 화질 리포트 출력 (선택)
    if validated_args.quality_report:
        _print_quality_report(main_video_files, main_results)

    # 4. DB 저장 및 Summary 생성
    video_ids = [r.video_id for r in results]
    summary, merge_job_id = save_merge_job_to_db(
        final_path,
        main_video_clips,
        validated_args.targets,
        main_video_ids,
        groups=groups,
    )

    # 4.1 프로젝트 연결 (--project 옵션 시)
    if validated_args.project and merge_job_id is not None:
        _link_merge_job_to_project(validated_args.project, merge_job_id)

    # 4.5 썸네일 생성 (비필수)
    if generated_thumbnail_paths is not None:
        generated_thumbnail_paths.clear()

    if validated_args.thumbnail:
        thumbnail_paths = _generate_thumbnails(final_path, validated_args)
        if generated_thumbnail_paths is not None:
            generated_thumbnail_paths.extend(thumbnail_paths)
        if thumbnail_paths:
            print(f"\n🖼️  썸네일 {len(thumbnail_paths)}장 생성:")
            for tp in thumbnail_paths:
                print(f"  - {tp}")

    # 4.6 영상 분할 (비필수)
    split_files: list[Path] = []
    if validated_args.split_duration or validated_args.split_size:
        from tubearchive.domain.media.splitter import SplitOptions, VideoSplitter

        splitter = VideoSplitter()
        split_opts = SplitOptions(
            duration=(
                splitter.parse_duration(validated_args.split_duration)
                if validated_args.split_duration
                else None
            ),
            size=(
                splitter.parse_size(validated_args.split_size)
                if validated_args.split_size
                else None
            ),
        )

        split_output_dir = final_path.parent
        split_criterion = "duration" if split_opts.duration else "size"
        split_value = validated_args.split_duration or validated_args.split_size or ""
        logger.info("Splitting video...")
        try:
            split_files = splitter.split_video(final_path, split_output_dir, split_opts)
            if split_files:
                print(f"\n✂️  영상 {len(split_files)}개로 분할:")
                for sf in split_files:
                    file_size = sf.stat().st_size if sf.exists() else 0
                    size_str = format_size(file_size)
                    print(f"  - {sf.name} ({size_str})")

                # DB에 split job 저장
                if merge_job_id is not None:
                    try:
                        from tubearchive.app.cli.main import (
                            database_session,  # lazy: avoids circular import
                        )

                        with database_session() as conn:
                            split_repo = SplitJobRepository(conn)
                            split_repo.create(
                                merge_job_id=merge_job_id,
                                split_criterion=split_criterion,
                                split_value=split_value,
                                output_files=split_files,
                            )
                        logger.debug("Split job saved to database")
                    except Exception as e:
                        logger.warning(f"Failed to save split job to DB: {e}")
        except Exception as e:
            logger.warning(f"Failed to split video: {e}")
            print(f"\n⚠️  영상 분할 실패: {e}")

    # 4.7 타임랩스 생성 (비필수)
    timelapse_path: Path | None = None
    if validated_args.timelapse_speed:
        timelapse_path = _generate_timelapse(final_path, validated_args)
        if timelapse_path:
            print(f"\n⏩ 타임랩스 ({validated_args.timelapse_speed}x) 생성:")
            print(f"  - {timelapse_path}")

    # 4.8 클라우드 백업 (결과물 + 옵션에 따라 원본)
    video_paths_for_archive = [
        (r.video_id, vf.path) for r, vf in zip(main_results, main_video_files, strict=True)
    ]
    if validated_args.backup_remote:
        original_for_backup = (
            [path for _, path in video_paths_for_archive] if validated_args.backup_all else []
        )
        _run_backup(
            final_path=final_path,
            split_files=split_files,
            timelapse_path=timelapse_path,
            original_paths_for_backup=original_for_backup,
            validated_args=validated_args,
            merge_job_id=merge_job_id,
        )

    # 5. 임시 파일 정리 및 DB 상태 업데이트 (--keep-temp와 무관하게 항상 실행)
    _mark_transcoding_jobs_merged(video_ids)
    if not validated_args.keep_temp:
        _cleanup_temp(temp_dir, results, final_path)

    # 5.5 원본 파일 아카이빙 (CLI 옵션 또는 config 정책)
    _archive_originals(video_paths_for_archive, validated_args)

    # 6. Summary 출력
    _print_summary(summary)

    return final_path


def _cmd_dry_run(validated_args: ValidatedArgs) -> None:
    """실행 계획만 출력하고 실제 트랜스코딩은 수행하지 않는다.

    ``--dry-run`` 플래그 처리용.
    """
    logger.info("Dry run mode - showing execution plan only")

    video_files = scan_videos(validated_args.targets)
    original_count = len(video_files)
    video_files = _apply_ordering(video_files, validated_args, allow_interactive=False)
    output_str = str(_resolve_output_path(validated_args))

    print("\n=== Dry Run Execution Plan ===")
    print(f"Input targets: {[str(t) for t in validated_args.targets]}")

    if original_count != len(video_files):
        print(f"Video files found: {original_count} (filtered to {len(video_files)})")
        if validated_args.exclude_patterns:
            print(f"  Exclude patterns: {validated_args.exclude_patterns}")
        if validated_args.include_only_patterns:
            print(f"  Include-only patterns: {validated_args.include_only_patterns}")
    else:
        print(f"Video files found: {len(video_files)}")

    if validated_args.sort_key != "time":
        print(f"Sort key: {validated_args.sort_key}")

    print_video_list(video_files, header="최종 클립 순서")

    print(f"Output: {output_str}")
    print("Temp dir: /tmp/tubearchive/<uuid>")
    print(f"Resume enabled: {not validated_args.no_resume}")
    print(f"Keep temp files: {validated_args.keep_temp}")
    print(f"Parallel workers: {validated_args.parallel}")
    print(f"Denoise enabled: {validated_args.denoise}")
    print(f"Denoise level: {validated_args.denoise_level}")
    print(f"Normalize audio: {validated_args.normalize_audio}")
    if validated_args.external_audio_path or validated_args.external_audio_dir:
        print(f"External audio: {validated_args.external_audio_path}")
        print(f"  candidate dir: {validated_args.external_audio_dir}")
        print(f"  scope: {validated_args.external_audio_scope}")
        print(f"  mode: {validated_args.external_audio_mode}")
        print(f"  camera volume: {validated_args.camera_audio_volume}")
        print(f"  clap sync: {validated_args.sync_audio_clap}")
        print(f"  drift correction: {validated_args.external_audio_drift_correction}")
        print(f"  offset: {validated_args.external_audio_offset}s")
        print(f"  min confidence: {validated_args.external_audio_min_confidence}")
        print(f"  match window: {validated_args.external_audio_match_window}s")
    print(f"Group sequences: {validated_args.group_sequences}")
    fade_display = (
        "disabled" if validated_args.fade_duration == 0.0 else f"{validated_args.fade_duration}s"
    )
    print(f"Fade duration: {fade_display}")
    if validated_args.stabilize:
        strength = validated_args.stabilize_strength
        crop = validated_args.stabilize_crop
        print(f"Stabilize: enabled (strength={strength}, crop={crop})")
    else:
        print("Stabilize: disabled")
    if validated_args.watermark:
        print("Watermark: enabled")
        print(
            f"  position={validated_args.watermark_pos}, "
            f"size={validated_args.watermark_size}, "
            f"color={validated_args.watermark_color}, "
            f"alpha={validated_args.watermark_alpha}"
        )
    else:
        print("Watermark: disabled")
    if validated_args.thumbnail:
        print(f"Thumbnail: enabled (quality={validated_args.thumbnail_quality})")
        if validated_args.thumbnail_timestamps:
            print(f"  timestamps: {validated_args.thumbnail_timestamps}")
        else:
            print("  timestamps: auto (10%, 33%, 50%)")
    if validated_args.bgm_path:
        print(f"BGM: {validated_args.bgm_path}")
        print(f"  volume: {validated_args.bgm_volume}")
        print(f"  loop: {validated_args.bgm_loop}")
    print("=" * 30)
