"""외부 오디오 ↔ 영상 클립 매핑 도메인 서비스.

긴 외부 녹음(별도 레코더 WAV)을 여러 영상 클립에 자동 정렬하는 오케스트레이션을
담당한다. 스캔(:mod:`scanner`)·메타데이터(:mod:`detector`)·신호 분석
(:mod:`audio_sync`) 도메인 모듈을 조합해 클립별 :class:`ExternalAudioSegment` 를
산출하며, 전략 선택(타임스탬프 → envelope → transient)도 이 계층이 소유한다.

순수 도메인 의존성만 가진다 (app/cli·infra에 의존하지 않음). 따라서 CLI 파이프라인과
TUI 사전 분석이 동일 경로를 공유한다.
"""

from __future__ import annotations

import logging
from dataclasses import replace as dc_replace
from datetime import datetime
from pathlib import Path

from tubearchive.domain.media.audio_sync import (
    AudioSyncError,
    ExternalAudioSegment,
    calculate_external_audio_segments,
    calculate_external_audio_segments_from_timestamps,
    calculate_external_audio_segments_from_wav_dir,
)
from tubearchive.domain.media.detector import (
    detect_local_timezone_offset,
    detect_metadata,
    get_audio_bext_start_utc,
    get_video_creation_time,
)
from tubearchive.domain.media.grouper import group_sequences, reorder_with_groups
from tubearchive.domain.media.scanner import scan_videos
from tubearchive.domain.models.video import VideoFile, VideoMetadata

logger = logging.getLogger(__name__)


def _auto_detect_wav_offset(
    audio_path: Path,
    reference_video_path: Path,
    reference_timestamps: dict[Path, datetime],
) -> float:
    """BEXT time_reference + DJI timezone으로 WAV 시작 offset(초)을 자동 계산한다.

    WAV 파일의 BEXT ``time_reference`` 와 레코더의 로컬 날짜를 사용해 WAV 녹음
    시작 UTC datetime을 구한 뒤, 첫 번째 영상의 UTC creation_time과의 차이를 반환한다.

    반환값이 양수이면 WAV가 첫 영상보다 먼저 시작된 것이다.
    BEXT 정보가 없거나 DJI 파일명 timezone 감지에 실패하면 0.0을 반환한다.
    """
    tz_offset = detect_local_timezone_offset(reference_video_path)
    if tz_offset is None:
        logger.debug("Timezone auto-detection failed (non-DJI file?) — using wav_offset=0")
        return 0.0

    wav_start_utc = get_audio_bext_start_utc(audio_path, tz_offset)
    if wav_start_utc is None:
        logger.debug("No BEXT time_reference in audio file — using wav_offset=0")
        return 0.0

    first_video_utc = min(reference_timestamps.values())
    offset = (first_video_utc - wav_start_utc).total_seconds()
    logger.info(
        "BEXT 기반 WAV offset 자동 감지: %.1fs (WAV 시작 %s UTC, 첫 클립 %s UTC)",
        offset,
        wav_start_utc.strftime("%H:%M:%S"),
        first_video_utc.strftime("%H:%M:%S"),
    )
    return offset


def _build_reference_durations(
    video_files: list[VideoFile],
    metadata_cache: dict[Path, VideoMetadata] | None,
    *,
    require_audio: bool = False,
) -> dict[Path, float]:
    """각 클립의 길이를 수집한다. ``metadata_cache`` 적중 시 ffprobe 재호출을 생략한다.

    ``require_audio=True`` 이면 카메라 내장 오디오가 없는 클립에서 ``ValueError`` 를 던진다.
    """
    durations: dict[Path, float] = {}
    for video_file in video_files:
        cached = (metadata_cache or {}).get(video_file.path)
        metadata = cached if cached is not None else detect_metadata(video_file.path)
        if require_audio and not metadata.has_audio:
            raise ValueError(
                f"Long external audio matching requires camera audio: {video_file.path}"
            )
        durations[video_file.path] = metadata.duration_seconds
    return durations


def analyze_long_external_audio(
    video_files: list[VideoFile],
    external_audio_path: Path,
    min_confidence: float,
    use_clap_sync: bool = False,
    wav_start_offset_seconds: float = 0.0,
    metadata_cache: dict[Path, VideoMetadata] | None = None,
) -> dict[Path, ExternalAudioSegment]:
    """긴 외부 녹음에서 각 영상 클립에 대응하는 외부 오디오 구간을 찾는다.

    ffprobe ``creation_time`` 태그가 모든 클립에 존재하면 타임스탬프 기반으로
    WAV 위치를 계산한다. 타임스탬프를 얻을 수 없는 클립이 하나라도 있으면
    envelope/transient 오디오 분석으로 폴백한다.

    ``metadata_cache`` 가 주어지면 포함된 파일은 ``detect_metadata`` 를 재호출하지 않는다.
    """
    if not video_files:
        raise AudioSyncError("분석할 영상 파일이 없습니다.")

    reference_durations = _build_reference_durations(
        video_files, metadata_cache, require_audio=True
    )

    logger.info("Analyzing long external audio: %s", external_audio_path)

    # 타임스탬프 기반 계산 시도
    reference_timestamps: dict[Path, datetime] = {}
    for video_file in video_files:
        ts = get_video_creation_time(video_file.path)
        if ts is None:
            reference_timestamps = {}
            break
        reference_timestamps[video_file.path] = ts

    # 타임스탬프 품질 검사(중복/역행) → 신뢰할 수 없으면 envelope 폴백
    if reference_timestamps:
        ordered_ts = [reference_timestamps[v.path] for v in video_files]
        has_duplicate = len(set(ordered_ts)) < len(ordered_ts)
        has_non_monotonic = any(
            (ordered_ts[i + 1] - ordered_ts[i]).total_seconds() <= 0
            for i in range(len(ordered_ts) - 1)
        )
        if has_duplicate or has_non_monotonic:
            logger.warning("타임스탬프 중복/역행 감지 → envelope/transient 매칭으로 폴백")
            reference_timestamps = {}

    if reference_timestamps:
        effective_offset = wav_start_offset_seconds
        if effective_offset == 0.0:
            effective_offset = _auto_detect_wav_offset(
                external_audio_path, video_files[0].path, reference_timestamps
            )
        logger.info(
            "Using timestamp-based external audio alignment (wav_offset=%.1fs)",
            effective_offset,
        )
        segments = calculate_external_audio_segments_from_timestamps(
            [video_file.path for video_file in video_files],
            external_audio_path,
            reference_durations=reference_durations,
            reference_timestamps=reference_timestamps,
            wav_start_offset_seconds=effective_offset,
        )
    else:
        logger.info("Timestamps unavailable, falling back to audio envelope matching")
        segments = calculate_external_audio_segments(
            [video_file.path for video_file in video_files],
            external_audio_path,
            reference_durations=reference_durations,
            min_confidence=min_confidence,
            clap_sync_fallback=use_clap_sync,
        )

    for video_file in video_files:
        segment = segments[video_file.path]
        logger.info(
            "External audio segment: %s -> start=%.3fs duration=%.3fs confidence=%.2f",
            video_file.path.name,
            segment.start_seconds,
            segment.duration_seconds,
            segment.confidence,
        )
    return segments


def analyze_long_external_audio_from_dir(
    video_files: list[VideoFile],
    wav_dir: Path,
    temp_dir: Path,
    metadata_cache: dict[Path, VideoMetadata] | None = None,
) -> dict[Path, ExternalAudioSegment]:
    """WAV 디렉토리의 BEXT 메타데이터로 각 클립에 맞는 WAV 구간을 자동 매핑한다.

    - 각 WAV의 BEXT time_reference → 녹음 시작 UTC
    - 각 DJI 클립의 creation_time UTC와 비교
    - 클립이 WAV 경계에 걸치면 임시 concat WAV 생성
    - DJI 파일명으로 타임존 오프셋 자동 감지 (비-DJI 카메라는 시스템 로컬 폴백)

    ``metadata_cache`` 가 주어지면 포함된 파일은 ``detect_metadata`` 를 재호출하지 않는다.
    """
    if not video_files:
        raise AudioSyncError("분석할 영상 파일이 없습니다.")

    reference_timestamps: dict[Path, datetime] = {}
    for video_file in video_files:
        ts = get_video_creation_time(video_file.path)
        if ts is None:
            raise AudioSyncError(
                f"creation_time 태그를 읽지 못했습니다: {video_file.path.name}\n"
                "BEXT 기반 WAV 매핑은 각 클립의 촬영 시각 메타데이터가 필요합니다."
            )
        reference_timestamps[video_file.path] = ts

    tz_offset = detect_local_timezone_offset(video_files[0].path)
    if tz_offset is None:
        utc_delta = datetime.now().astimezone().utcoffset()
        system_offset = int(utc_delta.total_seconds()) if utc_delta is not None else 0
        logger.warning(
            "타임존 오프셋 자동 감지 실패 (DJI 파일명 패턴이 아님). "
            "시스템 로컬 타임존(%+ds)을 기본값으로 사용.",
            system_offset,
        )
        tz_offset = system_offset

    reference_durations = _build_reference_durations(video_files, metadata_cache)

    logger.info(
        "WAV 디렉토리 기반 외부 오디오 매핑: %s (타임존 오프셋 %+ds)",
        wav_dir,
        tz_offset,
    )

    segments = calculate_external_audio_segments_from_wav_dir(
        [vf.path for vf in video_files],
        wav_dir,
        reference_timestamps=reference_timestamps,
        reference_durations=reference_durations,
        tz_offset_seconds=tz_offset,
        temp_dir=temp_dir,
    )

    for video_file in video_files:
        seg = segments[video_file.path]
        logger.info(
            "External audio segment: %s -> wav=%s start=%.3fs duration=%.3fs",
            video_file.path.name,
            seg.path.name,
            seg.start_seconds,
            seg.duration_seconds,
        )
    return segments


def apply_clip_adjustments(
    segments: dict[Path, ExternalAudioSegment],
    adjustments: dict[str, float],
) -> dict[Path, ExternalAudioSegment]:
    """클립별 수동 오프셋 보정을 segments에 적용한다.

    파일명에 패턴이 포함된 클립의 start_seconds를 조정한다.
    """
    result = dict(segments)
    for path, seg in segments.items():
        for pattern, delta in adjustments.items():
            if pattern in path.name:
                new_start = max(0.0, seg.start_seconds + delta)
                result[path] = dc_replace(seg, start_seconds=new_start)
                logger.info(
                    "%s: 수동 오프셋 보정 %+.3fs → start=%.3fs",
                    path.name,
                    delta,
                    new_start,
                )
                break
    return result


def analyze_long_audio_segments(
    targets: list[Path],
    wav_dir: Path,
    temp_dir: Path,
) -> dict[Path, ExternalAudioSegment]:
    """TUI 사전 분석 전용: 파이프라인 실행 없이 오디오 세그먼트 매핑만 수행한다.

    scan → group → main_video_files → BEXT 매핑 순으로 처리하고,
    confidence가 낮은 클립을 포함한 전체 결과를 반환한다.
    """
    all_files = scan_videos(targets)
    if not all_files:
        raise AudioSyncError("대상 디렉토리에서 영상 파일을 찾을 수 없습니다.")
    groups = group_sequences(all_files)
    ordered = reorder_with_groups(all_files, groups)
    return analyze_long_external_audio_from_dir(ordered, wav_dir, temp_dir)
