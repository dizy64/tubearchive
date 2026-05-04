"""파이프라인 관련 함수 및 데이터 클래스.

트랜스코딩·병합·DB 저장·후처리(BGM, 자막, 아카이브, 백업 등)
파이프라인 구성 요소를 담는다.

``main.py`` 에서 re-export 되므로 외부 임포트 경로는 변경 없이
``tubearchive.app.cli.main`` 을 그대로 사용할 수 있다.
"""

from __future__ import annotations

import fcntl
import json
import logging
import shutil
import subprocess
import sys
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Literal

from tubearchive.app.cli.context import (
    FileDoneEvent,
    FileProgressEvent,
    FileStartEvent,
    PipelineContext,
)
from tubearchive.app.cli.validators import ValidatedArgs
from tubearchive.config import HooksConfig
from tubearchive.domain.media.backup import BackupExecutor, BackupResult
from tubearchive.domain.media.detector import detect_metadata
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
from tubearchive.domain.media.transcoder import Transcoder
from tubearchive.domain.models.clip import ClipInfo
from tubearchive.domain.models.video import FadeConfig, VideoFile, VideoMetadata
from tubearchive.infra.db.repository import (
    MergeJobRepository,
    SplitJobRepository,
    TranscodingJobRepository,
)
from tubearchive.infra.ffmpeg.effects import SilenceSegment
from tubearchive.shared.progress import MultiProgressBar, ProgressInfo, format_size
from tubearchive.shared.summary_generator import generate_single_file_description

logger = logging.getLogger(__name__)


def get_temp_dir() -> Path:
    """실행별 고유 임시 디렉토리 생성 및 반환.

    공유 디렉토리(/tmp/tubearchive/)를 사용하면 동시 실행 중
    한 쪽이 cleanup할 때 나머지의 임시 파일도 삭제되는 문제가 발생한다.
    UUID 서브디렉토리로 격리하여 각 실행이 독립적인 트랜잭션을 갖도록 한다.
    """
    temp_base = Path("/tmp/tubearchive") / uuid.uuid4().hex[:8]  # noqa: S108
    temp_base.mkdir(parents=True, exist_ok=True)
    return temp_base


def check_output_disk_space(output_dir: Path, required_bytes: int) -> bool:
    """
    출력 디렉토리 디스크 공간 확인.

    Args:
        output_dir: 출력 디렉토리
        required_bytes: 필요한 바이트 수

    Returns:
        공간이 충분하면 True
    """
    usage = shutil.disk_usage(output_dir)
    if usage.free < required_bytes:
        logger.warning(
            f"Insufficient disk space: {usage.free / (1024**3):.1f}GB available, "
            f"{required_bytes / (1024**3):.1f}GB required"
        )
        return False
    return True


@dataclass(frozen=True)
class TranscodeOptions:
    """트랜스코딩 공통 옵션.

    ``_transcode_single``, ``_transcode_parallel``, ``_transcode_sequential``
    에서 공유하는 오디오·페이드 설정을 묶는다.

    Attributes:
        denoise: 오디오 노이즈 제거 여부 (afftdn)
        denoise_level: 노이즈 제거 강도 (``light`` | ``medium`` | ``heavy``)
        normalize_audio: EBU R128 loudnorm 2-pass 적용 여부
        fade_map: 파일별 페이드 설정 맵 (그룹 경계 기반)
        fade_duration: 기본 페이드 시간 (초)
        trim_silence: 무음 구간 제거 여부
        silence_threshold: 무음 기준 데시벨
        silence_min_duration: 최소 무음 길이 (초)
        lut_path: LUT 파일 경로 (직접 지정, auto_lut보다 우선)
        auto_lut: 기기 모델 기반 자동 LUT 매칭 활성화
        lut_before_hdr: LUT를 HDR→SDR 변환 전에 적용
        device_luts: 기기 키워드 → LUT 파일 경로 매핑
    """

    denoise: bool = False
    denoise_level: str = "medium"
    normalize_audio: bool = False
    fade_map: dict[Path, FadeConfig] | None = None
    fade_duration: float = 0.5
    watermark: bool = False
    trim_silence: bool = False
    silence_threshold: str = "-30dB"
    silence_min_duration: float = 2.0
    stabilize: bool = False
    stabilize_strength: str = "medium"
    stabilize_crop: str = "crop"
    lut_path: Path | None = None
    auto_lut: bool = False
    lut_before_hdr: bool = False
    device_luts: dict[str, str] | None = None
    watermark_text: str | None = None
    watermark_pos: str = "bottom-right"
    watermark_size: int = 48
    watermark_color: str = "white"
    watermark_alpha: float = 0.85


@dataclass(frozen=True)
class TranscodeResult:
    """단일 트랜스코딩 결과.

    Attributes:
        output_path: 트랜스코딩된 임시 파일 경로
        video_id: DB ``videos`` 테이블 ID
        clip_info: 클립 메타데이터 (파일명, 길이, 기기명, 촬영시각)
        silence_segments: 무음 구간 리스트 (trim_silence 활성화 시)
    """

    output_path: Path
    video_id: int
    clip_info: ClipInfo
    silence_segments: list[SilenceSegment] | None = None


def _to_video_file(path: Path) -> VideoFile:
    """템플릿 경로를 ``VideoFile`` 객체로 변환한다.

    ``scan_videos``와 동일한 생성 시간 계산 규칙을 사용한다.
    """
    stat = path.stat()
    if sys.platform == "darwin":
        creation_time = datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_mtime))
    else:
        creation_time = datetime.fromtimestamp(stat.st_ctime)

    return VideoFile(path=path, creation_time=creation_time, size_bytes=stat.st_size)


def get_output_filename(targets: list[Path]) -> str:
    """
    입력 타겟에서 출력 파일명 생성.

    디렉토리명 또는 첫 번째 파일의 부모 디렉토리명을 사용.

    Args:
        targets: 입력 타겟 목록

    Returns:
        출력 파일명 (확장자 포함)
    """
    if not targets:
        return "output.mp4"

    first_target = targets[0]
    name = first_target.name if first_target.is_dir() else first_target.parent.name

    # 빈 이름이거나 현재 디렉토리면 기본값
    if not name or name == ".":
        name = "output"

    return f"{name}.mp4"


def _get_media_duration(media_path: Path) -> float:
    """ffprobe를 사용하여 미디어 파일의 길이를 초 단위로 반환한다.

    Args:
        media_path: 미디어 파일 경로

    Returns:
        길이 (초)

    Raises:
        RuntimeError: ffprobe 실행 실패 또는 길이 파싱 실패
    """
    try:
        probe_result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(probe_result.stdout)
        return float(info["format"]["duration"])
    except (subprocess.CalledProcessError, KeyError, ValueError) as e:
        raise RuntimeError(f"Failed to probe duration: {media_path} - {e}") from e


def _has_audio_stream(media_path: Path) -> bool:
    """ffprobe를 사용하여 미디어 파일에 오디오 스트림이 있는지 확인한다.

    Args:
        media_path: 미디어 파일 경로

    Returns:
        오디오 스트림 존재 여부
    """
    try:
        probe_result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                "-select_streams",
                "a",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(probe_result.stdout)
        streams = info.get("streams", [])
        return len(streams) > 0
    except (subprocess.CalledProcessError, ValueError):
        return False


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


def handle_single_file_upload(
    video_file: VideoFile,
    args: ValidatedArgs,
) -> Path:
    """
    단일 파일 직접 업로드 처리.

    인코딩/병합 없이 DB 저장 후 원본 파일 경로 반환.

    Args:
        video_file: VideoFile 객체
        args: 검증된 CLI 인자

    Returns:
        원본 파일 경로
    """
    logger.info(f"Single file detected with --upload, skipping transcode: {video_file.path.name}")

    # 1. 메타데이터 수집
    metadata = detect_metadata(video_file.path)

    # 2. YouTube 제목 생성 (디렉토리명 기반)
    title = get_output_filename([video_file.path]).replace(".mp4", "")

    # 3. 촬영 시간 추출
    creation_time_str = video_file.creation_time.strftime("%H:%M:%S")

    # 4. 클립 정보 생성
    clip = ClipInfo(
        name=video_file.path.name,
        duration=metadata.duration_seconds,
        device=metadata.device_model or "Unknown",
        shot_time=creation_time_str,
    )

    # 5. YouTube 설명 생성 (단일 파일용)
    youtube_description = generate_single_file_description(
        device=clip.device, shot_time=clip.shot_time
    )

    # 6. DB 저장 (타임라인 dict: start/end 포함)
    clip_dict: dict[str, str | float | None] = {
        "name": clip.name,
        "duration": clip.duration,
        "start": 0.0,
        "end": clip.duration,
        "device": clip.device,
        "shot_time": clip.shot_time,
    }
    from tubearchive.app.cli.main import database_session  # lazy: avoids circular import

    with database_session() as conn:
        repo = MergeJobRepository(conn)
        today = date.today().isoformat()

        repo.create(
            output_path=video_file.path,
            video_ids=[],  # 트랜스코딩 안 함
            title=title,
            date=today,
            total_duration_seconds=metadata.duration_seconds,
            total_size_bytes=video_file.path.stat().st_size,
            clips_info_json=json.dumps([clip_dict]),
            summary_markdown=youtube_description,
        )

    # 7. 콘솔 출력
    logger.info(f"Saved to DB: {title}")
    print("\n📁 단일 파일 업로드 모드 (트랜스코딩 생략)")
    print(f"📹 파일: {video_file.path.name}")
    minutes = int(metadata.duration_seconds // 60)
    seconds = int(metadata.duration_seconds % 60)
    print(f"⏱️  길이: {minutes}분 {seconds}초")
    if metadata.device_model:
        print(f"📷 기기: {metadata.device_model}")

    return video_file.path


def _collect_clip_info(video_file: VideoFile, metadata: VideoMetadata | None = None) -> ClipInfo:
    """영상 파일에서 Summary·타임라인용 클립 메타데이터를 수집한다.

    ffprobe로 해상도·코덱·길이 등을 추출하고, 파일 생성 시간에서
    촬영 시각 문자열을 만든다. ffprobe 실패 시 duration=0.0 폴백.

    Args:
        video_file: 대상 영상 파일

    Returns:
        ClipInfo(name, duration, device, shot_time)
    """
    try:
        if metadata is None:
            metadata = detect_metadata(video_file.path)
        creation_time_str = video_file.creation_time.strftime("%H:%M:%S")
        return ClipInfo(
            name=video_file.path.name,
            duration=metadata.duration_seconds,
            device=metadata.device_model,
            shot_time=creation_time_str,
        )
    except Exception as e:
        logger.warning(f"Failed to get metadata for {video_file.path}: {e}")
        return ClipInfo(name=video_file.path.name, duration=0.0, device=None, shot_time=None)


def _make_watermark_text(video_file: VideoFile, metadata: VideoMetadata) -> str:
    """워터마크 텍스트 생성 (촬영 시각 + 위치 정보)."""
    shot_time = video_file.creation_time.strftime("%Y.%m.%d")

    location = metadata.location
    if location is None:
        lat = metadata.location_latitude
        lon = metadata.location_longitude
        if lat is not None and lon is not None:
            location = f"{lat:.6f}, {lon:.6f}"

    if location:
        return f"{shot_time} | {location}"
    return shot_time


def _transcode_single(
    video_file: VideoFile,
    temp_dir: Path,
    opts: TranscodeOptions,
) -> TranscodeResult:
    """단일 파일을 독립 Transcoder 컨텍스트에서 트랜스코딩한다.

    ``_transcode_parallel`` 에서 ThreadPoolExecutor에 제출되는 단위 작업이다.
    각 호출마다 Transcoder를 새로 생성하여 스레드 안전성을 보장한다.

    Args:
        video_file: 트랜스코딩할 원본 영상
        temp_dir: 트랜스코딩 출력 임시 디렉토리
        opts: 공통 트랜스코딩 옵션 (denoise, loudnorm, fade 등)

    Returns:
        ``TranscodeResult`` (출력 경로, video DB ID, 클립 메타데이터)
    """
    fade_config = opts.fade_map.get(video_file.path) if opts.fade_map else None
    fade_in = fade_config.fade_in if fade_config else None
    fade_out = fade_config.fade_out if fade_config else None

    with Transcoder(temp_dir=temp_dir) as transcoder:
        metadata = detect_metadata(video_file.path)
        if opts.watermark:
            watermark_text = opts.watermark_text or _make_watermark_text(video_file, metadata)
        else:
            watermark_text = None

        output_path, video_id, silence_segments = transcoder.transcode_video(
            video_file,
            metadata=metadata,
            denoise=opts.denoise,
            denoise_level=opts.denoise_level,
            normalize_audio=opts.normalize_audio,
            fade_duration=opts.fade_duration,
            fade_in_duration=fade_in,
            fade_out_duration=fade_out,
            trim_silence=opts.trim_silence,
            silence_threshold=opts.silence_threshold,
            silence_min_duration=opts.silence_min_duration,
            stabilize=opts.stabilize,
            stabilize_strength=opts.stabilize_strength,
            stabilize_crop=opts.stabilize_crop,
            lut_path=str(opts.lut_path) if opts.lut_path else None,
            auto_lut=opts.auto_lut,
            lut_before_hdr=opts.lut_before_hdr,
            device_luts=opts.device_luts,
            watermark_text=watermark_text,
            watermark_position=opts.watermark_pos,
            watermark_size=opts.watermark_size,
            watermark_color=opts.watermark_color,
            watermark_alpha=opts.watermark_alpha,
        )
        clip_info = _collect_clip_info(video_file, metadata)
        return TranscodeResult(
            output_path=output_path,
            video_id=video_id,
            clip_info=clip_info,
            silence_segments=silence_segments,
        )


def _transcode_parallel(
    video_files: list[VideoFile],
    temp_dir: Path,
    max_workers: int,
    opts: TranscodeOptions,
    context: PipelineContext | None = None,
) -> list[TranscodeResult]:
    """``ThreadPoolExecutor`` 를 사용한 병렬 트랜스코딩.

    각 파일을 독립된 :class:`Transcoder` 컨텍스트에서 처리하며,
    완료 순서에 관계없이 **원본 인덱스 순** 으로 결과를 정렬하여 반환한다.

    Args:
        video_files: 트랜스코딩 대상 파일 목록
        temp_dir: 임시 출력 디렉토리
        max_workers: 최대 동시 워커 수
        opts: 트랜스코딩 공통 옵션 (denoise, loudnorm, fade 등)
        context: 파이프라인 진행률 컨텍스트 (TUI 연동용, None이면 기존 동작)

    Returns:
        원본 순서가 유지된 트랜스코딩 결과 리스트

    Raises:
        RuntimeError: 하나 이상의 워커가 실패한 경우
    """
    results: dict[int, TranscodeResult] = {}
    completed_count = 0
    total_count = len(video_files)
    print_lock = Lock()

    def on_complete(idx: int, filename: str, status: str, success: bool) -> None:
        """병렬 워커 완료 콜백 -- 진행 카운터 갱신, 콘솔 출력 및 이벤트 emit."""
        nonlocal completed_count
        with print_lock:
            completed_count += 1
            print(
                f"\r🎬 트랜스코딩: [{completed_count}/{total_count}] {status}: {filename}",
                end="",
                flush=True,
            )
            if completed_count == total_count:
                print()  # 줄바꿈
        # emit outside the lock — on_progress is an arbitrary callable
        if context and context.on_progress:
            context.on_progress(FileDoneEvent(filename=filename, success=success))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict[Future[TranscodeResult], int] = {}
        for i, video_file in enumerate(video_files):
            if context and context.on_progress:
                context.on_progress(
                    FileStartEvent(
                        filename=video_file.path.name,
                        file_index=i,
                        total_files=total_count,
                    )
                )
            futures[executor.submit(_transcode_single, video_file, temp_dir, opts)] = i

        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                results[idx] = result
                on_complete(idx, video_files[idx].path.name, "완료", success=True)
            except Exception as e:
                logger.error(f"Failed to transcode {video_files[idx].path}: {e}")
                on_complete(idx, video_files[idx].path.name, "실패", success=False)
                raise

    return [results[i] for i in range(total_count)]


def _transcode_sequential(
    video_files: list[VideoFile],
    temp_dir: Path,
    opts: TranscodeOptions,
    context: PipelineContext | None = None,
) -> list[TranscodeResult]:
    """영상 파일을 순차적으로 트랜스코딩한다.

    :class:`MultiProgressBar` 로 파일별 진행률(fps, ETA)을 실시간 표시한다.
    ``parallel=1`` 이거나 파일이 1개일 때 사용된다.

    Args:
        video_files: 트랜스코딩할 영상 목록
        temp_dir: 트랜스코딩 결과 저장 임시 디렉토리
        opts: 트랜스코딩 공통 옵션 (오디오·페이드 설정)
        context: 파이프라인 진행률 컨텍스트 (TUI 연동용, None이면 기존 동작)

    Returns:
        트랜스코딩 결과 리스트 (출력 경로, video_id, 클립 정보)
    """
    results: list[TranscodeResult] = []
    progress = MultiProgressBar(total_files=len(video_files))

    with Transcoder(temp_dir=temp_dir) as transcoder:
        for i, video_file in enumerate(video_files):
            progress.start_file(video_file.path.name)

            if context and context.on_progress:
                context.on_progress(
                    FileStartEvent(
                        filename=video_file.path.name,
                        file_index=i,
                        total_files=len(video_files),
                    )
                )

            filename = video_file.path.name

            def on_progress_info(
                info: ProgressInfo,
                _filename: str = filename,
                _ctx: PipelineContext | None = context,
            ) -> None:
                """FFmpeg 상세 진행률을 MultiProgressBar 및 PipelineContext에 전달."""
                progress.update_with_info(info)
                if _ctx and _ctx.on_progress:
                    _ctx.on_progress(FileProgressEvent(filename=_filename, info=info))

            fade_config = opts.fade_map.get(video_file.path) if opts.fade_map else None
            fade_in = fade_config.fade_in if fade_config else None
            fade_out = fade_config.fade_out if fade_config else None

            metadata = detect_metadata(video_file.path)
            if opts.watermark:
                watermark_text = opts.watermark_text or _make_watermark_text(video_file, metadata)
            else:
                watermark_text = None
            try:
                output_path, video_id, silence_segments = transcoder.transcode_video(
                    video_file,
                    metadata=metadata,
                    denoise=opts.denoise,
                    denoise_level=opts.denoise_level,
                    normalize_audio=opts.normalize_audio,
                    fade_duration=opts.fade_duration,
                    fade_in_duration=fade_in,
                    fade_out_duration=fade_out,
                    trim_silence=opts.trim_silence,
                    silence_threshold=opts.silence_threshold,
                    silence_min_duration=opts.silence_min_duration,
                    stabilize=opts.stabilize,
                    stabilize_strength=opts.stabilize_strength,
                    stabilize_crop=opts.stabilize_crop,
                    lut_path=str(opts.lut_path) if opts.lut_path else None,
                    auto_lut=opts.auto_lut,
                    lut_before_hdr=opts.lut_before_hdr,
                    device_luts=opts.device_luts,
                    watermark_text=watermark_text,
                    watermark_position=opts.watermark_pos,
                    watermark_size=opts.watermark_size,
                    watermark_color=opts.watermark_color,
                    watermark_alpha=opts.watermark_alpha,
                    progress_info_callback=on_progress_info,
                )
                clip_info = _collect_clip_info(video_file, metadata)
                results.append(
                    TranscodeResult(
                        output_path=output_path,
                        video_id=video_id,
                        clip_info=clip_info,
                        silence_segments=silence_segments,
                    )
                )
                progress.finish_file()

                if context and context.on_progress:
                    context.on_progress(FileDoneEvent(filename=video_file.path.name, success=True))
            except Exception:
                if context and context.on_progress:
                    context.on_progress(FileDoneEvent(filename=video_file.path.name, success=False))
                raise

    return results


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
    """임시 파일 및 폴더를 정리한다."""
    logger.info("Cleaning up temporary files...")
    for r in results:
        if r.output_path.exists() and r.output_path != final_path:
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

    # --detect-silence: 분석만 수행 후 종료
    if validated_args.detect_silence:
        _detect_silence_only(video_files, validated_args)
        return Path()  # 빈 경로 반환

    # 단일 파일 + --upload 시 빠른 경로
    if (
        len(video_files) == 1
        and validated_args.upload
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
    temp_dir = get_temp_dir()
    logger.info(f"Using temp directory: {temp_dir}")

    transcode_opts = TranscodeOptions(
        denoise=validated_args.denoise,
        denoise_level=validated_args.denoise_level,
        normalize_audio=validated_args.normalize_audio,
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

    parallel = validated_args.parallel
    if parallel > 1:
        logger.info(f"Starting parallel transcoding (workers: {parallel})...")
        results = _transcode_parallel(
            video_files, temp_dir, parallel, transcode_opts, context=context
        )
    else:
        logger.info("Starting transcoding...")
        results = _transcode_sequential(video_files, temp_dir, transcode_opts, context=context)

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


def _run_backup(
    *,
    final_path: Path,
    split_files: list[Path],
    timelapse_path: Path | None,
    original_paths_for_backup: list[Path],
    validated_args: ValidatedArgs,
    merge_job_id: int | None,
) -> None:
    """병합/분할/타임랩스/원본 영상을 백업한다.

    실패해도 파이프라인을 중단하지 않고 로그만 남긴다.
    """
    if not validated_args.backup_remote:
        return

    remote = validated_args.backup_remote.strip()
    if not remote:
        logger.warning("backup remote is empty. skip backup.")
        return

    backup_targets: list[tuple[Path, Literal["output", "split", "timelapse", "original"]]] = []
    if final_path.exists():
        backup_targets.append((final_path, "output"))
    else:
        logger.warning("Final output not found for backup: %s", final_path)

    for split_file in split_files:
        if split_file.exists():
            backup_targets.append((split_file, "split"))
        else:
            logger.warning("Split file not found for backup: %s", split_file)

    if timelapse_path is not None:
        if timelapse_path.exists():
            backup_targets.append((timelapse_path, "timelapse"))
        else:
            logger.warning("Timelapse file not found for backup: %s", timelapse_path)

    for original_path in original_paths_for_backup:
        if original_path.exists():
            backup_targets.append((original_path, "original"))
        else:
            logger.warning("Original file not found for backup: %s", original_path)

    if not backup_targets:
        logger.warning("No backup targets found.")
        return

    logger.info("Starting backup (%s) for %d target(s)", remote, len(backup_targets))
    executor = BackupExecutor(remote)
    results: list[
        tuple[Path, Literal["output", "split", "timelapse", "original"], BackupResult]
    ] = []

    for source_path, source_type in backup_targets:
        backup_result = executor.copy(source_path)
        results.append((source_path, source_type, backup_result))
        if backup_result.success:
            logger.info("Backup succeeded: %s -> %s (%s)", source_path, remote, source_type)
        else:
            logger.warning(
                "Backup failed: %s -> %s (%s): %s",
                source_path,
                remote,
                source_type,
                backup_result.message,
            )

    if merge_job_id is None:
        logger.debug(
            "merge_job_id is None; skip backup history insertion (target count=%d)",
            len(results),
        )
        return

    from tubearchive.app.cli.main import database_session  # lazy: avoids circular import

    with database_session() as conn:
        from tubearchive.infra.db.repository import BackupHistoryRepository

        backup_repo = BackupHistoryRepository(conn)
        for source_path, source_type, result in results:
            backup_repo.insert_history(
                merge_job_id=merge_job_id,
                source_path=source_path,
                remote=remote,
                source_type=source_type,
                success=result.success,
                error_message=result.message,
            )


def _archive_originals(
    video_paths: list[tuple[int, Path]],
    validated_args: ValidatedArgs,
) -> None:
    """원본 파일들을 정책에 따라 아카이빙한다.

    CLI 옵션(``--archive-originals``) 또는 설정 파일(``[archive]``)의
    정책을 읽어 원본 파일을 이동/삭제/유지한다.

    우선순위: CLI ``--archive-originals`` > config ``[archive].policy``

    Args:
        video_paths: (video_id, original_path) 튜플 리스트
        validated_args: 검증된 CLI 인자
    """
    from tubearchive.config import get_default_archive_destination, get_default_archive_policy
    from tubearchive.domain.media.archiver import ArchivePolicy, Archiver

    if not video_paths:
        logger.warning("아카이빙할 원본 파일이 없습니다.")
        return

    # 정책 결정: CLI 옵션 > config > 기본값(KEEP)
    if validated_args.archive_originals:
        policy = ArchivePolicy.MOVE
        destination: Path | None = validated_args.archive_originals
    else:
        policy_str = get_default_archive_policy()
        policy = ArchivePolicy(policy_str)
        destination = get_default_archive_destination()

    # KEEP 정책이면 아무것도 하지 않음
    if policy == ArchivePolicy.KEEP:
        logger.debug("아카이브 정책이 KEEP입니다. 원본 파일 유지.")
        return

    # MOVE 정책인데 destination이 없으면 경고
    if policy == ArchivePolicy.MOVE and not destination:
        logger.warning("MOVE 정책이 설정되었으나 destination이 없습니다. 원본 파일 유지.")
        return

    # DELETE 정책 시 확인 프롬프트 (core 모듈이 아닌 CLI 계층에서 처리)
    if (
        policy == ArchivePolicy.DELETE
        and not validated_args.archive_force
        and not _prompt_archive_delete_confirmation(len(video_paths))
    ):
        logger.info("사용자가 삭제를 취소했습니다.")
        return

    logger.info("원본 파일 아카이빙 시작 (정책: %s)...", policy.value)

    from tubearchive.app.cli.main import database_session  # lazy: avoids circular import

    with database_session() as conn:
        from tubearchive.infra.db.repository import ArchiveHistoryRepository

        archive_repo = ArchiveHistoryRepository(conn)
        archiver = Archiver(
            repo=archive_repo,
            policy=policy,
            destination=destination,
        )
        stats = archiver.archive_files(video_paths)

    if policy == ArchivePolicy.MOVE:
        logger.info("아카이빙 완료: 이동 %d, 실패 %d", stats.moved, stats.failed)
    elif policy == ArchivePolicy.DELETE:
        logger.info("아카이빙 완료: 삭제 %d, 실패 %d", stats.deleted, stats.failed)


def _prompt_archive_delete_confirmation(file_count: int) -> bool:
    """원본 파일 삭제 확인 프롬프트를 표시한다.

    Args:
        file_count: 삭제 대상 파일 개수

    Returns:
        True: 삭제 승인, False: 취소
    """
    print(f"\n⚠️  {file_count}개의 원본 파일을 영구 삭제하려고 합니다.")
    print("이 작업은 되돌릴 수 없습니다.")
    response = input("계속하시겠습니까? (y/N): ").strip().lower()
    return response in {"y", "yes"}


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


def _mark_transcoding_jobs_merged(video_ids: list[int]) -> None:
    """트랜스코딩 작업 상태를 merged로 업데이트 (임시 파일 정리 후)."""
    if not video_ids:
        return
    try:
        from tubearchive.app.cli.main import database_session  # lazy: avoids circular import

        with database_session() as conn:
            job_repo = TranscodingJobRepository(conn)
            count = job_repo.mark_merged_by_video_ids(video_ids)
        logger.debug(f"Marked {count} transcoding jobs as merged")
    except Exception:
        logger.warning("Failed to mark transcoding jobs as merged", exc_info=True)


def save_merge_job_to_db(
    output_path: Path,
    video_clips: list[ClipInfo],
    targets: list[Path],
    video_ids: list[int],
    groups: list[FileSequenceGroup] | None = None,
) -> tuple[str | None, int | None]:
    """병합 작업 정보를 DB에 저장 (타임라인 및 Summary 포함).

    Args:
        output_path: 출력 파일 경로
        video_clips: 클립 메타데이터 리스트
        targets: 입력 타겟 목록 (제목 추출용)
        video_ids: 병합된 영상들의 DB ID 목록
        groups: 시퀀스 그룹 목록 (Summary 생성용)

    Returns:
        (콘솔 출력용 Summary 마크다운, merge_job_id) 튜플. 실패 시 (None, None).
    """
    from tubearchive.shared.summary_generator import (
        generate_clip_summary,
        generate_youtube_description,
    )

    try:
        from tubearchive.app.cli.main import (  # type: ignore[attr-defined]  # lazy: callers patch main.*
            MergeJobRepository as _MergeJobRepository,
        )
        from tubearchive.app.cli.main import (
            database_session,
        )

        with database_session() as conn:
            repo = _MergeJobRepository(conn)

            # 타임라인 정보 생성 (각 클립의 메타데이터 포함)
            timeline: list[dict[str, str | float | None]] = []
            current_time = 0.0
            for clip in video_clips:
                timeline.append(
                    {
                        "name": clip.name,
                        "duration": clip.duration,
                        "start": current_time,
                        "end": current_time + clip.duration,
                        "device": clip.device,
                        "shot_time": clip.shot_time,
                    }
                )
                current_time += clip.duration

            clips_json = json.dumps(timeline, ensure_ascii=False)

            # 제목: 디렉토리명
            title = None
            if targets:
                first_target = targets[0]
                title = first_target.name if first_target.is_dir() else first_target.parent.name
                if not title or title == ".":
                    title = output_path.stem

            today = date.today().isoformat()

            total_duration = sum(c.duration for c in video_clips)
            total_size = output_path.stat().st_size if output_path.exists() else 0

            # 콘솔 출력용 요약 (마크다운 형식)
            console_summary = generate_clip_summary(video_clips, groups=groups)
            # YouTube 설명용 (타임스탬프 + 촬영기기)
            youtube_description = generate_youtube_description(video_clips, groups=groups)

            merge_job_id = repo.create(
                output_path=output_path,
                video_ids=video_ids,
                title=title,
                date=today,
                total_duration_seconds=total_duration,
                total_size_bytes=total_size,
                clips_info_json=clips_json,
                summary_markdown=youtube_description,
            )

        logger.debug("Merge job saved to database with summary")
        return console_summary, merge_job_id

    except Exception as e:
        logger.warning(f"Failed to save merge job to DB: {e}")
        return None, None


def _link_merge_job_to_project(project_name: str, merge_job_id: int) -> None:
    """병합 결과를 프로젝트에 연결한다.

    프로젝트가 없으면 자동 생성하고, merge_job을 연결한다.
    날짜 범위도 자동으로 갱신된다.

    Args:
        project_name: 프로젝트 이름
        merge_job_id: merge_job ID
    """
    from tubearchive.infra.db.repository import ProjectRepository

    try:
        from tubearchive.app.cli.main import database_session  # lazy: avoids circular import

        with database_session() as conn:
            repo = ProjectRepository(conn)
            project = repo.get_or_create(project_name)
            if project.id is None:
                logger.warning("Project created but has no ID")
                return
            repo.add_merge_job(project.id, merge_job_id)
            logger.info(f"Merge job {merge_job_id} linked to project '{project_name}'")
            print(f"\n📁 프로젝트 '{project_name}'에 병합 결과 연결됨")
    except Exception as e:
        logger.warning(f"Failed to link merge job to project: {e}")


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
