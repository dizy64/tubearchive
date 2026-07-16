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
from dataclasses import dataclass
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
    _apply_post_merge_processing,
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
    _build_transcode_options,
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


@dataclass(frozen=True)
class _VideoAssembly:
    """트랜스코딩 직전 단계의 입력 구성 결과.

    템플릿 삽입·시퀀스 그룹핑·fade_map·외부 오디오 세그먼트를 한 번에 산출해
    ``run_pipeline`` 의 본체를 단순화한다.
    """

    video_files: list[VideoFile]  # 템플릿이 삽입된 최종 트랜스코딩 대상
    main_video_files: list[VideoFile]  # 템플릿 제외, 그룹핑/재정렬된 메인 클립
    groups: list[FileSequenceGroup]
    fade_map: dict[Path, FadeConfig]
    temp_dir: Path
    external_audio_segments: dict[Path, ExternalAudioSegment] | None
    template_intro_file: VideoFile | None
    template_outro_file: VideoFile | None
    template_intro_count: int
    template_outro_count: int


def _prepare_video_assembly(
    validated_args: ValidatedArgs,
    video_files: list[VideoFile],
) -> _VideoAssembly:
    """템플릿 삽입·그룹핑·fade_map·외부 오디오 세그먼트를 계산해 조립 결과를 반환한다.

    스캔·정렬이 끝난 ``video_files`` 를 받아 트랜스코딩 단계가 필요로 하는 모든
    입력 구성을 산출한다. 부수효과 없이(임시 디렉토리 생성 제외) 값만 만든다.
    """
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

    # 그룹핑 및 재정렬
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

    # 트랜스코딩용 임시 디렉토리 (이후 단계에서도 공유)
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
                wav_start_offset_seconds=validated_args.external_audio_wav_offset,
                clip_adjustments=validated_args.external_audio_clip_adjustments,
            )

        if (
            external_audio_segments
            and validated_args.external_audio_clip_adjustments
            and validated_args.external_audio_path
        ):
            external_audio_segments = apply_clip_adjustments(
                external_audio_segments,
                validated_args.external_audio_clip_adjustments,
            )

    # 템플릿 클립을 트랜스코딩 대상 목록에 삽입하고 경계 fade를 조정한다.
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

    return _VideoAssembly(
        video_files=video_files,
        main_video_files=main_video_files,
        groups=groups,
        fade_map=fade_map,
        temp_dir=temp_dir,
        external_audio_segments=external_audio_segments,
        template_intro_file=template_intro_file,
        template_outro_file=template_outro_file,
        template_intro_count=template_intro_count,
        template_outro_count=template_outro_count,
    )


def _run_splitting(
    final_path: Path,
    validated_args: ValidatedArgs,
    merge_job_id: int | None,
) -> list[Path]:
    """``--split-duration`` / ``--split-size`` 지정 시 병합 결과를 분할하고 DB에 기록한다.

    분할/DB 저장 실패는 전체 파이프라인을 중단하지 않고 경고만 남긴다(비필수 단계).
    분할이 비활성이거나 결과가 없으면 빈 리스트를 반환한다.
    """
    if not (validated_args.split_duration or validated_args.split_size):
        return []

    from tubearchive.domain.media.splitter import SplitOptions, VideoSplitter

    splitter = VideoSplitter()
    split_opts = SplitOptions(
        duration=(
            splitter.parse_duration(validated_args.split_duration)
            if validated_args.split_duration
            else None
        ),
        size=(
            splitter.parse_size(validated_args.split_size) if validated_args.split_size else None
        ),
    )

    split_output_dir = final_path.parent
    split_criterion = "duration" if split_opts.duration else "size"
    split_value = validated_args.split_duration or validated_args.split_size or ""
    logger.info("Splitting video...")
    split_files: list[Path] = []
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
    return split_files


def _run_transcoding(
    video_files: list[VideoFile],
    main_video_files: list[VideoFile],
    transcode_opts: TranscodeOptions,
    temp_dir: Path,
    validated_args: ValidatedArgs,
    template_intro_file: VideoFile | None,
    template_outro_file: VideoFile | None,
    context: PipelineContext | None,
) -> list[TranscodeResult]:
    """스킵 판정 결과에 따라 stream-copy / 병렬 / 순차 트랜스코딩을 디스패치한다.

    모든 입력이 PROFILE_SDR과 정합하고 필터가 없으면 트랜스코딩을 통째로 건너뛰고
    원본을 concat demuxer로 stream-copy 병합한다. 메타데이터 캐시를 재사용해
    스킵 분기의 재-probe(ffprobe 2N → N)를 방지한다.
    """
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
        return _run_skip_transcoding(main_video_files, temp_dir, metadata_cache, context=context)
    if parallel > 1:
        logger.debug(f"Skip not eligible: {skip_reason}")
        logger.info(f"Starting parallel transcoding (workers: {parallel})...")
        return _transcode_parallel(
            video_files,
            temp_dir,
            parallel,
            transcode_opts,
            context=context,
            metadata_cache=metadata_cache,
        )
    logger.debug(f"Skip not eligible: {skip_reason}")
    logger.info("Starting transcoding...")
    return _transcode_sequential(
        video_files,
        temp_dir,
        transcode_opts,
        context=context,
        metadata_cache=metadata_cache,
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

    # 1.5 입력 조립: 템플릿 삽입 + 시퀀스 그룹핑 + fade_map + 외부 오디오 세그먼트
    assembly = _prepare_video_assembly(validated_args, video_files)
    video_files = assembly.video_files
    main_video_files = assembly.main_video_files
    groups = assembly.groups
    fade_map = assembly.fade_map
    temp_dir = assembly.temp_dir
    external_audio_segments = assembly.external_audio_segments
    template_intro_file = assembly.template_intro_file
    template_outro_file = assembly.template_outro_file
    template_intro_count = assembly.template_intro_count
    template_outro_count = assembly.template_outro_count

    # 2. 트랜스코딩
    transcode_opts = _build_transcode_options(validated_args, fade_map, external_audio_segments)

    if validated_args.stabilize:
        logger.info(
            "영상 안정화 활성화 (vidstab 2-pass, strength=%s, crop=%s) "
            "— 트랜스코딩 시간이 증가합니다",
            validated_args.stabilize_strength,
            validated_args.stabilize_crop,
        )

    results = _run_transcoding(
        video_files,
        main_video_files,
        transcode_opts,
        temp_dir,
        validated_args,
        template_intro_file,
        template_outro_file,
        context,
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

    # 3.4 후처리: 라우드니스 정규화 → BGM 믹싱 → 자막 → 화질 리포트 (순서 중요)
    _apply_post_merge_processing(
        final_path,
        temp_dir,
        validated_args,
        main_video_files,
        main_results,
        generated_subtitle_paths,
    )

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
    split_files = _run_splitting(final_path, validated_args, merge_job_id)

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
