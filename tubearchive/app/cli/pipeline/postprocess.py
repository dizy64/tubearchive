"""병합 후처리 (BGM, loudnorm, 무음 감지, 썸네일, 자막, 품질, 타임랩스)."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from tubearchive.app.cli.pipeline.io_utils import _get_media_duration, _has_audio_stream
from tubearchive.app.cli.pipeline.transcode import TranscodeResult
from tubearchive.app.cli.validators import ValidatedArgs
from tubearchive.domain.models.video import VideoFile

logger = logging.getLogger(__name__)


def _apply_bgm_mixing(
    video_path: Path,
    bgm_path: Path,
    bgm_volume: float,
    bgm_loop: bool,
    output_path: Path,
) -> Path:
    """병합된 영상에 BGM을 믹싱한다.

    ffprobe로 영상/BGM 길이와 오디오 스트림 존재 여부를 확인한 뒤
    :func:`~tubearchive.infra.ffmpeg.effects.create_bgm_filter` 로 필터를 생성하고
    ffmpeg로 오디오만 재인코딩한다 (영상은 ``-c:v copy``).

    Args:
        video_path: 병합된 영상 파일 경로
        bgm_path: BGM 파일 경로
        bgm_volume: BGM 상대 볼륨 (0.0~1.0)
        bgm_loop: BGM 루프 재생 여부
        output_path: 출력 파일 경로

    Returns:
        BGM이 믹싱된 최종 파일 경로

    Raises:
        RuntimeError: FFmpeg 실행 실패
    """
    from tubearchive.infra.ffmpeg.effects import create_bgm_filter

    logger.info(f"Applying BGM mixing: {bgm_path.name}")

    video_duration = _get_media_duration(video_path)
    bgm_duration = _get_media_duration(bgm_path)
    has_audio = _has_audio_stream(video_path)

    logger.info(
        f"Video duration: {video_duration:.2f}s, BGM duration: {bgm_duration:.2f}s, "
        f"has_audio: {has_audio}"
    )

    bgm_filter = create_bgm_filter(
        bgm_duration=bgm_duration,
        video_duration=video_duration,
        bgm_volume=bgm_volume,
        bgm_loop=bgm_loop,
        has_audio=has_audio,
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(bgm_path),
        "-filter_complex",
        bgm_filter,
        "-map",
        "0:v",
        "-map",
        "[a_out]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        str(output_path),
    ]

    logger.info(f"Running BGM mixing: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.error(f"BGM mixing failed: {result.stderr}")
        raise RuntimeError(f"BGM mixing failed: {result.stderr}")

    logger.info(f"BGM mixing completed: {output_path}")
    return output_path


def _apply_post_merge_loudnorm(
    video_path: Path,
    output_path: Path,
) -> Path:
    """병합된 영상 전체에 EBU R128 loudnorm 2-pass를 적용한다.

    클립별이 아닌 병합 결과 한 번에 측정·정규화하므로, 클립 간 상대 라우드니스가
    보존되고 전체 영상의 평균 라우드니스만 :data:`LOUDNORM_TARGET_I` 목표값에 맞춰진다.
    비디오는 ``-c:v copy``로 복사하고 오디오만 재인코딩한다.

    Args:
        video_path: 라우드니스 정규화 대상 영상 (병합 결과)
        output_path: 정규화된 출력 파일 경로

    Returns:
        정규화된 출력 파일 경로. 오디오 스트림이 없거나 분석이 실패하면
        ``video_path``를 그대로 ``output_path``로 복사하여 반환한다.
    """
    from tubearchive.infra.ffmpeg.effects import (
        create_loudnorm_analysis_filter,
        create_loudnorm_filter,
        parse_loudnorm_stats,
    )
    from tubearchive.infra.ffmpeg.executor import FFmpegError, FFmpegExecutor

    # 스킵 경로(오디오 없음, 분석 실패)에서는 수 GB 파일을 굳이 복사하지 않고
    # 원본 경로를 그대로 반환한다. 호출부에서 반환값과 원본 경로 동일 여부를
    # 확인하여 ``shutil.move`` 의 ``SameFileError`` 를 회피해야 한다.
    if not _has_audio_stream(video_path):
        logger.info("No audio stream in merged output, skipping loudnorm")
        return video_path

    executor = FFmpegExecutor()
    analysis_filter = create_loudnorm_analysis_filter()
    analysis_cmd = executor.build_loudness_analysis_command(
        input_path=video_path,
        audio_filter=analysis_filter,
    )

    logger.info("Running post-merge loudnorm analysis pass")
    try:
        stderr = executor.run_analysis(analysis_cmd)
        analysis = parse_loudnorm_stats(stderr)
    except (FFmpegError, ValueError) as e:
        logger.warning(f"Post-merge loudnorm analysis failed, skipping normalization: {e}")
        return video_path

    logger.info(
        f"Loudnorm (post-merge): I={analysis.input_i:.1f}dB "
        f"TP={analysis.input_tp:.1f}dB LRA={analysis.input_lra:.1f}"
    )

    loudnorm_filter = create_loudnorm_filter(analysis)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-map",
        "0:v",
        "-map",
        "0:a",
        "-c:v",
        "copy",
        "-af",
        loudnorm_filter,
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        "-ar",
        "48000",
        str(output_path),
    ]

    logger.info(f"Applying post-merge loudnorm: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error(f"Post-merge loudnorm failed: {result.stderr}")
        raise RuntimeError(f"Post-merge loudnorm failed: {result.stderr}")

    logger.info(f"Post-merge loudnorm completed: {output_path}")
    return output_path


def _detect_silence_only(
    video_files: list[VideoFile],
    validated_args: ValidatedArgs,
) -> None:
    """
    무음 구간 감지 전용 모드.

    각 영상의 무음 구간을 감지하고 콘솔에 출력한다.
    """
    from tubearchive.infra.ffmpeg.effects import (
        create_silence_detect_filter,
        parse_silence_segments,
    )
    from tubearchive.infra.ffmpeg.executor import FFmpegExecutor

    executor = FFmpegExecutor()

    threshold = validated_args.silence_threshold
    min_duration = validated_args.silence_min_duration

    for video_file in video_files:
        print(f"\n🔍 분석 중: {video_file.path.name}")

        # silencedetect 필터 생성
        detect_filter = create_silence_detect_filter(
            threshold=threshold,
            min_duration=min_duration,
        )

        # 분석 명령 실행
        cmd = executor.build_silence_detection_command(
            input_path=video_file.path,
            audio_filter=detect_filter,
        )
        stderr = executor.run_analysis(cmd)

        # 파싱
        segments = parse_silence_segments(stderr)

        if not segments:
            print("  무음 구간 없음")
        else:
            print(f"  무음 구간 {len(segments)}개 발견:")
            for i, seg in enumerate(segments, 1):
                print(f"    {i}. {seg.start:.2f}s - {seg.end:.2f}s (길이: {seg.duration:.2f}s)")


def _generate_thumbnails(
    video_path: Path,
    validated_args: ValidatedArgs,
) -> list[Path]:
    """병합 영상에서 썸네일 생성.

    실패 시 경고만 남기고 빈 리스트 반환 (파이프라인 중단 없음).
    """
    from tubearchive.infra.ffmpeg.thumbnail import extract_thumbnails, parse_timestamp

    timestamps: list[float] | None = None
    if validated_args.thumbnail_timestamps:
        parsed: list[float] = []
        for ts in validated_args.thumbnail_timestamps:
            try:
                parsed.append(parse_timestamp(ts))
            except ValueError as e:
                logger.warning("Invalid thumbnail timestamp '%s': %s", ts, e)
        timestamps = parsed if parsed else None

    try:
        return extract_thumbnails(
            video_path,
            timestamps=timestamps,
            output_dir=validated_args.output_dir,
            quality=validated_args.thumbnail_quality,
        )
    except Exception:
        logger.warning("Failed to generate thumbnails", exc_info=True)
        return []


def _apply_subtitle_burn(
    input_path: Path,
    subtitle_path: Path,
) -> Path:
    """자막을 비디오에 하드코딩한다."""
    from tubearchive.domain.media.subtitle import build_subtitle_filter

    output_path = input_path.with_name(f"{input_path.stem}_subtitled{input_path.suffix}")
    subtitle_filter = build_subtitle_filter(subtitle_path)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        subtitle_filter,
        "-c:a",
        "copy",
        "-c:v",
        "libx265",
        str(output_path),
    ]
    logger.info("Applying hardcoded subtitle: %s", output_path.name)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        from tubearchive.infra.ffmpeg.executor import FFmpegError

        logger.error("Subtitle burn failed: %s", result.stderr)
        context = (
            f"input_path={input_path!s}, "
            f"subtitle_path={subtitle_path!s}, "
            f"output_path={output_path!s}, "
            f"stderr={result.stderr}"
        )
        raise FFmpegError(
            f"Failed to burn subtitles ({context})",
            result.stderr,
        )
    return output_path


def _print_quality_report(
    video_files: list[VideoFile],
    results: list[TranscodeResult],
) -> None:
    """트랜스코딩 전/후 SSIM/PSNR/VMAF 지표를 출력한다."""
    from tubearchive.domain.media.quality import generate_quality_reports

    pairs = [
        (source.path, result.output_path)
        for source, result in zip(video_files, results, strict=True)
    ]
    reports = generate_quality_reports(pairs)
    if not reports:
        print("\n🔬 화질 리포트: 계산 대상 없음")
        return

    print("\n🔬 화질 리포트:")
    for report in reports:
        print(f"\n  - 원본: {report.source_path.name}")
        print(f"    결과: {report.output_path.name}")
        if report.ssim is not None:
            print(f"    SSIM: {report.ssim:.4f}")
        if report.psnr is not None:
            print(f"    PSNR: {report.psnr:.4f} dB")
        if report.vmaf is not None:
            print(f"    VMAF: {report.vmaf:.4f}")

        if report.unavailable:
            missing = ", ".join(sorted(report.unavailable))
            print(f"    미지원/실패 지표: {missing}")
        if report.errors:
            for err in report.errors:
                print(f"    경고: {err}")


def _generate_timelapse(
    video_path: Path,
    validated_args: ValidatedArgs,
) -> Path | None:
    """병합 영상에서 타임랩스 생성.

    실패 시 경고만 남기고 None 반환 (파이프라인 중단 없음).

    Args:
        video_path: 입력 병합 영상 경로
        validated_args: 검증된 CLI 인자

    Returns:
        타임랩스 파일 경로 (실패 시 None)
    """
    from tubearchive.domain.media.timelapse import TimelapseGenerator

    if validated_args.timelapse_speed is None:
        return None

    # 출력 경로 생성
    stem = video_path.stem
    suffix = video_path.suffix
    output_dir = validated_args.output_dir or video_path.parent
    output_path = output_dir / f"{stem}_timelapse_{validated_args.timelapse_speed}x{suffix}"

    try:
        logger.info(f"Generating {validated_args.timelapse_speed}x timelapse: {output_path.name}")
        generator = TimelapseGenerator()
        return generator.generate(
            input_path=video_path,
            output_path=output_path,
            speed=validated_args.timelapse_speed,
            keep_audio=validated_args.timelapse_audio,
            resolution=validated_args.timelapse_resolution,
        )
    except Exception:
        logger.warning("Failed to generate timelapse", exc_info=True)
        return None
