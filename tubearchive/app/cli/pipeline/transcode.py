"""트랜스코딩 단위 작업 및 스킵 판정."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from tubearchive.app.cli.context import (
    FileDoneEvent,
    FileProgressEvent,
    FileStartEvent,
    PipelineContext,
)
from tubearchive.app.cli.pipeline.io_utils import _emit_progress
from tubearchive.app.cli.validators import ValidatedArgs
from tubearchive.domain.media.audio_sync import (
    ExternalAudioSegment,
)
from tubearchive.domain.media.detector import (
    detect_metadata,
)
from tubearchive.domain.media.transcoder import Transcoder
from tubearchive.domain.models.clip import ClipInfo
from tubearchive.domain.models.video import FadeConfig, VideoFile, VideoMetadata
from tubearchive.infra.ffmpeg.effects import SilenceSegment
from tubearchive.shared.progress import MultiProgressBar, ProgressInfo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranscodeOptions:
    """트랜스코딩 공통 옵션.

    ``_transcode_single``, ``_transcode_parallel``, ``_transcode_sequential``
    에서 공유하는 오디오·페이드 설정을 묶는다.

    Attributes:
        denoise: 오디오 노이즈 제거 여부 (afftdn)
        denoise_level: 노이즈 제거 강도 (``light`` | ``medium`` | ``heavy``)
        external_audio_path: 영상 내장 오디오 대신 사용할 외부 오디오 파일
        external_audio_dir: 영상과 자동 매칭할 외부 오디오 후보 디렉토리
        external_audio_scope: 외부 오디오 적용 범위 (single/long)
        external_audio_segments: 긴 외부 녹음의 클립별 구간 맵
        sync_audio_clap: 박수/피크 기반 자동 싱크 여부
        external_audio_drift_correction: 장시간 drift 보정 여부
        external_audio_offset: 외부 오디오에 적용할 수동 offset(초)
        external_audio_mode: 외부 오디오 적용 방식 (replace/mix)
        camera_audio_volume: mix 모드에서 카메라 내장 오디오 볼륨
        external_audio_min_confidence: 자동 싱크 최소 신뢰도
        external_audio_match_window: 후보 선택 시 파일 시각 매칭 창(초)
        fade_map: 파일별 페이드 설정 맵 (그룹 경계 기반)
        fade_duration: 기본 페이드 시간 (초)
        trim_silence: 무음 구간 제거 여부
        silence_threshold: 무음 기준 데시벨
        silence_min_duration: 최소 무음 길이 (초)
        lut_path: LUT 파일 경로 (직접 지정, auto_lut보다 우선)
        auto_lut: 기기 모델 기반 자동 LUT 매칭 활성화
        lut_before_hdr: LUT를 HDR→SDR 변환 전에 적용
        device_luts: 기기 키워드 → LUT 파일 경로 매핑

    Note:
        EBU R128 라우드니스 정규화는 트랜스코딩이 아닌 병합 직후 단계
        (:func:`_apply_post_merge_loudnorm`)에서 한 번만 수행하므로
        이 데이터클래스에는 ``normalize_audio`` 필드가 없다.
    """

    denoise: bool = False
    denoise_level: str = "medium"
    external_audio_path: Path | None = None
    external_audio_dir: Path | None = None
    external_audio_scope: str = "single"
    external_audio_segments: dict[Path, ExternalAudioSegment] | None = None
    sync_audio_clap: bool = False
    external_audio_drift_correction: bool = False
    external_audio_offset: float = 0.0
    external_audio_mode: str = "replace"
    camera_audio_volume: float = 0.1
    external_audio_min_confidence: float = 0.6
    external_audio_match_window: float = 300.0
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
    video_denoise: bool = False
    video_denoise_strength: str = "medium"
    wb_kelvin: int | None = None
    auto_white_balance: bool = False
    device_wb: dict[str, str] | None = None
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
    context: PipelineContext | None = None,
    file_index: int = 0,
    total_count: int = 1,
    cached_metadata: VideoMetadata | None = None,
) -> TranscodeResult:
    """단일 파일을 독립 Transcoder 컨텍스트에서 트랜스코딩한다.

    ``_transcode_parallel`` 에서 ThreadPoolExecutor에 제출되는 단위 작업이다.
    각 호출마다 Transcoder를 새로 생성하여 스레드 안전성을 보장한다.

    Args:
        video_file: 트랜스코딩할 원본 영상
        temp_dir: 트랜스코딩 출력 임시 디렉토리
        opts: 공통 트랜스코딩 옵션 (denoise, loudnorm, fade 등)
        context: 파이프라인 진행률 컨텍스트 (TUI 연동용, None이면 기존 동작)
        file_index: 파일 인덱스 (0-based)
        total_count: 전체 파일 수
        cached_metadata: ``_can_skip_transcoding`` 등이 이미 probe한 메타데이터.
            ``None``이면 ffprobe로 새로 감지한다.

    Returns:
        ``TranscodeResult`` (출력 경로, video DB ID, 클립 메타데이터)
    """
    filename = video_file.path.name
    _emit_progress(
        context,
        FileStartEvent(filename=filename, file_index=file_index, total_files=total_count),
    )

    def _on_progress_info(
        info: ProgressInfo,
        _ctx: PipelineContext | None = context,
        _fname: str = filename,
        _idx: int = file_index,
    ) -> None:
        _emit_progress(_ctx, FileProgressEvent(filename=_fname, file_index=_idx, info=info))

    fade_config = opts.fade_map.get(video_file.path) if opts.fade_map else None
    fade_in = fade_config.fade_in if fade_config else None
    fade_out = fade_config.fade_out if fade_config else None
    external_segment = (
        opts.external_audio_segments.get(video_file.path) if opts.external_audio_segments else None
    )
    external_audio_path = external_segment.path if external_segment else opts.external_audio_path
    external_audio_start = external_segment.start_seconds if external_segment else None
    external_audio_duration = external_segment.duration_seconds if external_segment else None
    sync_audio_clap = opts.sync_audio_clap and external_segment is None

    with Transcoder(temp_dir=temp_dir) as transcoder:
        metadata = (
            cached_metadata if cached_metadata is not None else detect_metadata(video_file.path)
        )
        if opts.watermark:
            watermark_text = opts.watermark_text or _make_watermark_text(video_file, metadata)
        else:
            watermark_text = None

        output_path, video_id, silence_segments = transcoder.transcode_video(
            video_file,
            metadata=metadata,
            denoise=opts.denoise,
            denoise_level=opts.denoise_level,
            external_audio_path=external_audio_path,
            external_audio_dir=opts.external_audio_dir,
            sync_audio_clap=sync_audio_clap,
            external_audio_drift_correction=opts.external_audio_drift_correction,
            external_audio_offset=opts.external_audio_offset,
            external_audio_mode=opts.external_audio_mode,
            camera_audio_volume=opts.camera_audio_volume,
            external_audio_min_confidence=opts.external_audio_min_confidence,
            external_audio_match_window=opts.external_audio_match_window,
            external_audio_start=external_audio_start,
            external_audio_duration=external_audio_duration,
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
            video_denoise=opts.video_denoise,
            video_denoise_strength=opts.video_denoise_strength,
            wb_kelvin=opts.wb_kelvin,
            auto_white_balance=opts.auto_white_balance,
            device_wb=opts.device_wb,
            watermark_text=watermark_text,
            watermark_position=opts.watermark_pos,
            watermark_size=opts.watermark_size,
            watermark_color=opts.watermark_color,
            watermark_alpha=opts.watermark_alpha,
            progress_info_callback=_on_progress_info,
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
    metadata_cache: dict[Path, VideoMetadata] | None = None,
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
        metadata_cache: ``_can_skip_transcoding`` 등이 사전에 probe한 메타데이터.
            제공되면 워커가 ffprobe를 재호출하지 않는다.

    Returns:
        원본 순서가 유지된 트랜스코딩 결과 리스트

    Raises:
        RuntimeError: 하나 이상의 워커가 실패한 경우
    """
    cache = metadata_cache or {}
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
        _emit_progress(context, FileDoneEvent(filename=filename, file_index=idx, success=success))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures: dict[Future[TranscodeResult], int] = {}
        for i, video_file in enumerate(video_files):
            futures[
                executor.submit(
                    _transcode_single,
                    video_file,
                    temp_dir,
                    opts,
                    context,
                    i,
                    total_count,
                    cache.get(video_file.path),
                )
            ] = i

        first_error: Exception | None = None
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                results[idx] = result
                on_complete(idx, video_files[idx].path.name, "완료", success=True)
            except Exception as e:
                logger.error(f"Failed to transcode {video_files[idx].path}: {e}")
                on_complete(idx, video_files[idx].path.name, "실패", success=False)
                if first_error is None:
                    first_error = e
        if first_error is not None:
            raise first_error

    return [results[i] for i in range(total_count)]


def _transcode_sequential(
    video_files: list[VideoFile],
    temp_dir: Path,
    opts: TranscodeOptions,
    context: PipelineContext | None = None,
    metadata_cache: dict[Path, VideoMetadata] | None = None,
) -> list[TranscodeResult]:
    """영상 파일을 순차적으로 트랜스코딩한다.

    :class:`MultiProgressBar` 로 파일별 진행률(fps, ETA)을 실시간 표시한다.
    ``parallel=1`` 이거나 파일이 1개일 때 사용된다.

    Args:
        video_files: 트랜스코딩할 영상 목록
        temp_dir: 트랜스코딩 결과 저장 임시 디렉토리
        opts: 트랜스코딩 공통 옵션 (오디오·페이드 설정)
        context: 파이프라인 진행률 컨텍스트 (TUI 연동용, None이면 기존 동작)
        metadata_cache: ``_can_skip_transcoding`` 등이 사전에 probe한 메타데이터.
            제공되면 ffprobe를 재호출하지 않는다.

    Returns:
        트랜스코딩 결과 리스트 (출력 경로, video_id, 클립 정보)
    """
    results: list[TranscodeResult] = []
    progress = MultiProgressBar(total_files=len(video_files))
    cache = metadata_cache or {}

    with Transcoder(temp_dir=temp_dir) as transcoder:
        for i, video_file in enumerate(video_files):
            progress.start_file(video_file.path.name)

            _emit_progress(
                context,
                FileStartEvent(
                    filename=video_file.path.name,
                    file_index=i,
                    total_files=len(video_files),
                ),
            )

            filename = video_file.path.name

            def on_progress_info(
                info: ProgressInfo,
                _filename: str = filename,
                _ctx: PipelineContext | None = context,
                _idx: int = i,
            ) -> None:
                """FFmpeg 상세 진행률을 MultiProgressBar 및 PipelineContext에 전달."""
                progress.update_with_info(info)
                _emit_progress(
                    _ctx, FileProgressEvent(filename=_filename, file_index=_idx, info=info)
                )

            fade_config = opts.fade_map.get(video_file.path) if opts.fade_map else None
            fade_in = fade_config.fade_in if fade_config else None
            fade_out = fade_config.fade_out if fade_config else None
            external_segment = (
                opts.external_audio_segments.get(video_file.path)
                if opts.external_audio_segments
                else None
            )
            external_audio_path = (
                external_segment.path if external_segment else opts.external_audio_path
            )
            external_audio_start = external_segment.start_seconds if external_segment else None
            external_audio_duration = (
                external_segment.duration_seconds if external_segment else None
            )
            sync_audio_clap = opts.sync_audio_clap and external_segment is None

            metadata = cache.get(video_file.path) or detect_metadata(video_file.path)
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
                    external_audio_path=external_audio_path,
                    external_audio_dir=opts.external_audio_dir,
                    sync_audio_clap=sync_audio_clap,
                    external_audio_drift_correction=opts.external_audio_drift_correction,
                    external_audio_offset=opts.external_audio_offset,
                    external_audio_mode=opts.external_audio_mode,
                    camera_audio_volume=opts.camera_audio_volume,
                    external_audio_min_confidence=opts.external_audio_min_confidence,
                    external_audio_match_window=opts.external_audio_match_window,
                    external_audio_start=external_audio_start,
                    external_audio_duration=external_audio_duration,
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
                    video_denoise=opts.video_denoise,
                    video_denoise_strength=opts.video_denoise_strength,
                    wb_kelvin=opts.wb_kelvin,
                    auto_white_balance=opts.auto_white_balance,
                    device_wb=opts.device_wb,
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

                _emit_progress(
                    context,
                    FileDoneEvent(filename=video_file.path.name, file_index=i, success=True),
                )
            except Exception:
                _emit_progress(
                    context,
                    FileDoneEvent(filename=video_file.path.name, file_index=i, success=False),
                )
                raise

    return results


# PROFILE_SDR과 정합한다고 판정할 때 비교하는 기준값.
# encoder 이름(hevc_videotoolbox)이 아니라 ffprobe codec_name(hevc)을 사용한다.
_SKIP_TARGET_VIDEO_CODEC = "hevc"
# hevc_videotoolbox 인코더는 -pix_fmt p010le를 요청해도
# ffprobe가 yuv420p10le로 보고하는 경우가 있다 (두 이름 모두 10-bit 4:2:0).
_SKIP_TARGET_PIXEL_FORMATS = frozenset({"p010le", "yuv420p10le"})
_SKIP_TARGET_WIDTH = 3840
_SKIP_TARGET_HEIGHT = 2160
_SKIP_TARGET_FPS = 30000 / 1001  # 29.97
_SKIP_TARGET_FPS_TOLERANCE = 0.05
_SKIP_TARGET_AUDIO_CODEC = "aac"
_SKIP_TARGET_AUDIO_SAMPLE_RATE = 48000


def _can_skip_transcoding(
    video_files: list[VideoFile],
    transcode_opts: TranscodeOptions,
    validated_args: ValidatedArgs,
    template_intro_file: VideoFile | None,
    template_outro_file: VideoFile | None,
) -> tuple[bool, str, dict[Path, VideoMetadata]]:
    """트랜스코딩을 건너뛰고 stream-copy concat으로 바로 병합할 수 있는지 판정한다.

    조건:
        1. 어떤 필터/이펙트도 활성화되지 않았다 (denoise, LUT, WB, vidstab, fade 등).
        2. 인트로/아웃트로 템플릿이 없다.
        3. 모든 입력 파일이 동질적인 메타데이터(코덱·해상도·fps·SAR·오디오 포맷)를 갖는다.
        4. 그 메타데이터가 :data:`PROFILE_SDR` 의 출력 사양과 일치한다.

    조건 중 하나라도 실패하면 일반 트랜스코딩 경로를 그대로 사용한다.

    Args:
        video_files: 병합 대상 영상 파일 목록 (템플릿 제외).
        transcode_opts: 트랜스코딩 옵션.
        validated_args: 검증된 CLI 인자.
        template_intro_file: 인트로 템플릿 (있으면 스킵 불가).
        template_outro_file: 아웃트로 템플릿 (있으면 스킵 불가).

    Returns:
        ``(스킵 가능 여부, 사유 문자열, 경로→VideoMetadata 캐시)``.
        세 번째 값은 메타데이터 probe 결과로, 스킵 경로에서 ffprobe 재호출을
        피하기 위해 호출자가 ``_run_skip_transcoding``에 그대로 전달한다.
        probe 자체가 실패하거나 필터 검사에서 일찍 탈락한 경우는 빈 dict.
    """
    if not video_files:
        return False, "no input files", {}

    if template_intro_file is not None or template_outro_file is not None:
        return False, "template intro/outro present", {}

    # --- 필터/이펙트 검사 ---
    if transcode_opts.denoise:
        return False, "audio denoise enabled", {}
    if (
        transcode_opts.external_audio_path is not None
        or transcode_opts.external_audio_dir is not None
        or transcode_opts.external_audio_segments
    ):
        return False, "external audio enabled", {}
    if transcode_opts.video_denoise:
        return False, "video denoise enabled", {}
    if transcode_opts.trim_silence:
        return False, "trim silence enabled", {}
    if transcode_opts.stabilize:
        return False, "stabilize enabled", {}
    if transcode_opts.watermark:
        return False, "watermark enabled", {}
    if transcode_opts.lut_path is not None or transcode_opts.auto_lut:
        return False, "LUT enabled", {}
    if transcode_opts.wb_kelvin is not None or transcode_opts.auto_white_balance:
        return False, "white balance adjustment enabled", {}
    if transcode_opts.fade_map:
        for fade in transcode_opts.fade_map.values():
            if fade.fade_in > 0 or fade.fade_out > 0:
                return False, "dip-to-black fade enabled", {}

    # --- 메타데이터 균질성 + PROFILE_SDR 정합 검사 ---
    try:
        metadatas = [detect_metadata(vf.path) for vf in video_files]
    except Exception as e:
        return False, f"metadata probe failed: {e}", {}

    metadata_cache: dict[Path, VideoMetadata] = {
        vf.path: m for vf, m in zip(video_files, metadatas, strict=True)
    }

    first = metadatas[0]
    homogeneous_fields = (
        "codec",
        "pixel_format",
        "width",
        "height",
        "sar",
        "color_space",
        "color_transfer",
        "color_primaries",
        "audio_codec",
        "audio_sample_rate",
        "audio_channels",
        "audio_stream_count",
        "has_audio",
        "is_portrait",
    )
    # 메타데이터 단계까지 probe가 진행되면, 스킵 자격이 없더라도 일반 트랜스코딩
    # 경로(_transcode_parallel/sequential)에서 캐시를 재사용할 수 있도록 늘 동봉한다.
    for m in metadatas[1:]:
        for field in homogeneous_fields:
            if getattr(m, field) != getattr(first, field):
                return False, f"heterogeneous {field} across inputs", metadata_cache
        if abs(m.fps - first.fps) > _SKIP_TARGET_FPS_TOLERANCE:
            return False, "heterogeneous fps across inputs", metadata_cache

    # PROFILE_SDR 정합 검사 (기준 파일 first만 검사하면 충분 — 위에서 균질성 보장)
    if first.codec != _SKIP_TARGET_VIDEO_CODEC:
        return (
            False,
            f"video codec {first.codec!r} ≠ {_SKIP_TARGET_VIDEO_CODEC!r}",
            metadata_cache,
        )
    if first.pixel_format not in _SKIP_TARGET_PIXEL_FORMATS:
        return (
            False,
            f"pixel format {first.pixel_format!r} not in {sorted(_SKIP_TARGET_PIXEL_FORMATS)}",
            metadata_cache,
        )
    if first.width != _SKIP_TARGET_WIDTH or first.height != _SKIP_TARGET_HEIGHT:
        return (
            False,
            f"resolution {first.width}x{first.height} ≠ {_SKIP_TARGET_WIDTH}x{_SKIP_TARGET_HEIGHT}",
            metadata_cache,
        )
    if abs(first.fps - _SKIP_TARGET_FPS) > _SKIP_TARGET_FPS_TOLERANCE:
        return False, f"fps {first.fps:.3f} ≠ 29.97", metadata_cache
    if first.is_portrait:
        return False, "portrait orientation requires layout filter", metadata_cache
    if first.is_vfr:
        return False, "variable frame rate", metadata_cache
    # HDR transfer는 SDR로 변환이 필요하므로 스킵 불가.
    # color_transfer가 None인 경우는 모호하지만 ffprobe가 bt709 SDR을 종종 None으로 보고하므로 허용.
    if first.color_transfer not in (None, "bt709"):
        return (
            False,
            f"color transfer {first.color_transfer!r} requires conversion",
            metadata_cache,
        )
    if first.color_space not in (None, "bt709"):
        return (
            False,
            f"color space {first.color_space!r} requires conversion",
            metadata_cache,
        )
    if first.color_primaries not in (None, "bt709"):
        return (
            False,
            f"color primaries {first.color_primaries!r} requires conversion",
            metadata_cache,
        )
    if first.sar not in (None, "1:1"):
        return False, f"non-square pixels (sar={first.sar!r})", metadata_cache
    if first.has_audio:
        if first.audio_codec != _SKIP_TARGET_AUDIO_CODEC:
            return (
                False,
                f"audio codec {first.audio_codec!r} ≠ {_SKIP_TARGET_AUDIO_CODEC!r}",
                metadata_cache,
            )
        if first.audio_sample_rate != _SKIP_TARGET_AUDIO_SAMPLE_RATE:
            return False, f"sample rate {first.audio_sample_rate} ≠ 48000", metadata_cache
    # PROFILE_SDR 출력은 단일 오디오 스트림만 가지므로 다중 오디오 트랙 입력
    # (외부 마이크 + 내장 마이크 등)은 stream-copy concat이 보장되지 않는다.
    # 균질성 검사로 모든 파일 간 일치는 이미 확인됐으므로 첫 파일만 확인하면 충분.
    if first.audio_stream_count > 1:
        return (
            False,
            f"multi-track audio (count={first.audio_stream_count}) not concat-safe",
            metadata_cache,
        )

    return True, "all inputs already match PROFILE_SDR with no filters enabled", metadata_cache


def _run_skip_transcoding(
    video_files: list[VideoFile],
    temp_dir: Path,
    metadata_cache: dict[Path, VideoMetadata],
    context: PipelineContext | None = None,
) -> list[TranscodeResult]:
    """트랜스코딩을 건너뛰고 원본 파일을 그대로 사용하는 결과를 생성한다.

    각 파일을 DB에 등록하고 즉시 완료(``COMPLETED``) 상태의 transcoding_job을
    기록하여 다운스트림(DB 통계, Resume 등)이 정상 동작하도록 한다.

    Args:
        video_files: 입력 영상 파일 목록 (PROFILE_SDR과 이미 정합한다고 검증됨).
        temp_dir: Transcoder DB 컨텍스트용 임시 디렉토리.
        metadata_cache: ``_can_skip_transcoding`` 이 채워둔 메타데이터 캐시.
            여기 누락된 경로는 fallback으로 ``detect_metadata``를 재호출한다.
        context: 파이프라인 진행률 컨텍스트.

    Returns:
        각 파일의 원본 경로를 ``output_path``로 갖는 :class:`TranscodeResult` 리스트.
    """
    from tubearchive.domain.models.job import JobStatus

    results: list[TranscodeResult] = []
    total = len(video_files)
    with Transcoder(temp_dir=temp_dir) as transcoder:
        for i, video_file in enumerate(video_files):
            _emit_progress(
                context,
                FileStartEvent(
                    filename=video_file.path.name,
                    file_index=i,
                    total_files=total,
                ),
            )
            metadata = metadata_cache.get(video_file.path) or detect_metadata(video_file.path)
            video_id = transcoder.register_video(video_file, metadata)
            job_id = transcoder.resume_mgr.get_or_create_job(video_id)
            transcoder.job_repo.update_status(job_id, JobStatus.COMPLETED)
            transcoder.job_repo.mark_completed(job_id, video_file.path)
            clip_info = _collect_clip_info(video_file, metadata)
            results.append(
                TranscodeResult(
                    output_path=video_file.path,
                    video_id=video_id,
                    clip_info=clip_info,
                    silence_segments=None,
                )
            )
            _emit_progress(
                context,
                FileDoneEvent(filename=video_file.path.name, file_index=i, success=True),
            )
    logger.info(f"Skipped transcoding for {total} file(s) — using originals for concat")
    return results
