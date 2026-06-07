"""외부 오디오와 영상 내장 오디오의 clap 기반 싱크 추정."""

from __future__ import annotations

import logging
import operator
import subprocess
import sys
import uuid
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median

logger = logging.getLogger(__name__)

# detector는 audio_sync를 import하지 않으므로 순환 참조 없음
from tubearchive.domain.media.detector import get_audio_bext_start_utc  # noqa: E402

SUPPORTED_EXTERNAL_AUDIO_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".flac",
    ".m4a",
    ".mp3",
    ".wav",
    ".wave",
}
DEFAULT_EXTERNAL_AUDIO_MATCH_WINDOW_SECONDS = 300.0
AUDIO_EXTRACTION_TIMEOUT_SECONDS = 300.0
AUDIO_PROBE_TIMEOUT_SECONDS = 30.0


class AudioSyncError(ValueError):
    """오디오 자동 싱크를 신뢰할 수 없을 때 발생한다."""


@dataclass(frozen=True)
class TransientCandidate:
    """박수처럼 짧고 큰 소리 후보."""

    time_seconds: float
    score: float


@dataclass(frozen=True)
class AudioSyncOffset:
    """외부 오디오에 적용할 싱크 보정값."""

    offset_seconds: float
    confidence: float
    reference_time_seconds: float
    external_time_seconds: float


@dataclass(frozen=True)
class AudioSyncDrift:
    """외부 오디오 싱크와 장시간 드리프트 보정값."""

    offset_seconds: float
    tempo_ratio: float
    confidence: float
    reference_start_time_seconds: float
    external_start_time_seconds: float
    reference_end_time_seconds: float
    external_end_time_seconds: float


@dataclass(frozen=True)
class ExternalAudioCandidate:
    """영상과 매칭 가능한 외부 오디오 후보."""

    path: Path
    score: float
    duration_seconds: float
    duration_delta_seconds: float
    mtime_delta_seconds: float


@dataclass(frozen=True)
class ExternalAudioSegment:
    """긴 외부 녹음에서 한 영상 클립에 대응하는 구간."""

    path: Path
    start_seconds: float
    duration_seconds: float
    confidence: float
    tempo_ratio: float = 1.0


def find_transient_candidates(
    samples: Sequence[float],
    sample_rate: int,
    *,
    threshold_ratio: float = 8.0,
    min_gap_seconds: float = 0.25,
    search_window_ms: float = 20.0,
) -> list[TransientCandidate]:
    """짧고 큰 transient 후보를 찾는다.

    새 의존성을 피하기 위해 절대 진폭 기반의 단순한 onset 검출을 사용한다.
    FFmpeg로 저해상도 mono PCM을 추출한 뒤 박수/클랩처럼 배경 대비 큰 피크를
    찾는 목적에 맞춘다.
    """
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be > 0, got: {sample_rate}")
    if not samples:
        return []

    magnitudes = [abs(sample) for sample in samples]
    noise_floor = max(median(magnitudes), 1e-6)
    threshold = noise_floor * threshold_ratio
    min_gap_samples = max(1, int(sample_rate * min_gap_seconds))
    window_samples = max(1, int(sample_rate * search_window_ms / 1000))

    candidates: list[TransientCandidate] = []
    index = 0
    last_peak_index = -min_gap_samples
    while index < len(magnitudes):
        if magnitudes[index] < threshold or index - last_peak_index < min_gap_samples:
            index += 1
            continue

        end = min(len(magnitudes), index + window_samples)
        local_index = max(range(index, end), key=magnitudes.__getitem__)
        local_peak = magnitudes[local_index]
        candidates.append(
            TransientCandidate(
                time_seconds=local_index / sample_rate,
                score=local_peak / noise_floor,
            )
        )
        last_peak_index = local_index
        index = local_index + min_gap_samples

    return sorted(candidates, key=lambda candidate: candidate.score, reverse=True)


def estimate_clap_sync_offset(
    reference_samples: Sequence[float],
    external_samples: Sequence[float],
    sample_rate: int,
) -> AudioSyncOffset:
    """내장 오디오와 외부 오디오의 대표 clap 피크 차이를 offset으로 계산한다.

    반환되는 offset은 FFmpeg에서 외부 오디오 입력에 적용할 값이다.
    양수면 외부 오디오를 그만큼 늦추고, 음수면 앞당긴다.
    """
    reference_candidates = find_transient_candidates(reference_samples, sample_rate)
    external_candidates = find_transient_candidates(external_samples, sample_rate)
    if not reference_candidates or not external_candidates:
        raise AudioSyncError("No clear transient candidates found for clap sync")

    reference = reference_candidates[0]
    external = external_candidates[0]
    confidence = min(1.0, min(reference.score, external.score) / 10.0)
    return AudioSyncOffset(
        offset_seconds=reference.time_seconds - external.time_seconds,
        confidence=confidence,
        reference_time_seconds=reference.time_seconds,
        external_time_seconds=external.time_seconds,
    )


def estimate_clap_sync_with_drift(
    reference_samples: Sequence[float],
    external_samples: Sequence[float],
    sample_rate: int,
) -> AudioSyncDrift:
    """두 개 이상 transient를 이용해 offset과 tempo drift를 함께 추정한다.

    ``tempo_ratio`` 는 FFmpeg ``atempo`` 에 적용할 값이다. 1보다 크면 외부
    오디오를 빠르게 재생해 길이를 줄이고, 1보다 작으면 느리게 재생한다.
    """
    reference_candidates = sorted(
        find_transient_candidates(reference_samples, sample_rate),
        key=lambda candidate: candidate.time_seconds,
    )
    external_candidates = sorted(
        find_transient_candidates(external_samples, sample_rate),
        key=lambda candidate: candidate.time_seconds,
    )
    if len(reference_candidates) < 2 or len(external_candidates) < 2:
        raise AudioSyncError("At least two transient candidates are required for drift correction")

    reference_start = reference_candidates[0]
    reference_end = reference_candidates[-1]
    external_start = external_candidates[0]
    external_end = external_candidates[-1]

    reference_span = reference_end.time_seconds - reference_start.time_seconds
    external_span = external_end.time_seconds - external_start.time_seconds
    if reference_span <= 0 or external_span <= 0:
        raise AudioSyncError("Invalid transient span for drift correction")

    tempo_ratio = external_span / reference_span
    if not (0.5 <= tempo_ratio <= 2.0):
        raise AudioSyncError(
            f"Estimated audio tempo ratio is out of supported range: {tempo_ratio}"
        )

    confidence = min(
        1.0,
        min(
            reference_start.score,
            reference_end.score,
            external_start.score,
            external_end.score,
        )
        / 10.0,
    )
    return AudioSyncDrift(
        offset_seconds=reference_start.time_seconds - external_start.time_seconds,
        tempo_ratio=tempo_ratio,
        confidence=confidence,
        reference_start_time_seconds=reference_start.time_seconds,
        external_start_time_seconds=external_start.time_seconds,
        reference_end_time_seconds=reference_end.time_seconds,
        external_end_time_seconds=external_end.time_seconds,
    )


def extract_mono_pcm_samples(
    media_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    sample_rate: int = 1000,
    start_seconds: float | None = None,
    duration_seconds: float | None = None,
) -> list[float]:
    """FFmpeg로 media_path의 첫 오디오 스트림을 mono s16le 샘플로 추출한다.

    Args:
        media_path: 미디어 파일 경로
        ffmpeg_path: ffmpeg 실행 파일 경로
        sample_rate: 출력 샘플레이트 (Hz)
        start_seconds: 추출 시작 위치(초). None이면 처음부터.
        duration_seconds: 추출 길이(초). None이면 끝까지.

    Raises:
        AudioSyncError: 추출 실패 또는 타임아웃
    """
    cmd = [ffmpeg_path, "-v", "error"]
    if start_seconds is not None:
        cmd += ["-ss", str(start_seconds)]
    if duration_seconds is not None:
        cmd += ["-t", str(duration_seconds)]
    cmd += [
        "-i",
        str(media_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=AUDIO_EXTRACTION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioSyncError(
            "Timed out extracting audio samples from "
            f"{media_path} after {AUDIO_EXTRACTION_TIMEOUT_SECONDS:.0f}s"
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise AudioSyncError(f"Failed to extract audio samples from {media_path}: {stderr}")

    raw = array("h")
    stdout = result.stdout
    if len(stdout) % 2 != 0:
        stdout = stdout[:-1]
    raw.frombytes(stdout)
    if sys.byteorder != "little":
        raw.byteswap()
    return [sample / 32768.0 for sample in raw]


def _extract_mono_pcm_segment(
    media_path: Path,
    start_seconds: float,
    duration_seconds: float,
    *,
    sample_rate: int = 100,
    ffmpeg_path: str = "ffmpeg",
) -> list[float]:
    """미디어의 지정 구간을 mono PCM으로 추출한다. 오류 시 빈 리스트 반환."""
    try:
        return extract_mono_pcm_samples(
            media_path,
            ffmpeg_path=ffmpeg_path,
            sample_rate=sample_rate,
            start_seconds=start_seconds,
            duration_seconds=duration_seconds,
        )
    except (AudioSyncError, FileNotFoundError):
        return []


def _raw_pcm_correlation_offset(
    ref: list[float],
    cand: list[float],
    search_range_frames: int,
) -> tuple[int, float]:
    """ref를 cand에서 찾아 최적 lag (frames)와 normalized correlation을 반환.

    lag > 0: cand의 뒤쪽에서 ref와 더 잘 매치됨 → cand 시작을 lag/sr초 늦춰야 함
    """
    n = min(len(ref), max(0, len(cand) - search_range_frames * 2))
    if n < max(1, len(ref) // 10):
        return 0, 0.0

    ref_n = ref[:n]
    ref_power = sum(map(operator.mul, ref_n, ref_n))
    if ref_power == 0.0:
        return 0, 0.0

    best_lag, best_corr = 0, -1e18
    for lag in range(-search_range_frames, search_range_frames + 1):
        offset = lag + search_range_frames
        end = offset + n
        if end > len(cand):
            continue
        corr = sum(map(operator.mul, ref_n, cand[offset:end]))
        if corr > best_corr:
            best_corr = corr
            best_lag = lag

    offset = best_lag + search_range_frames
    cand_slice = cand[offset : offset + n]
    cand_power = sum(map(operator.mul, cand_slice, cand_slice))
    denom = (ref_power * cand_power) ** 0.5
    confidence = best_corr / denom if denom > 0.0 else 0.0

    return best_lag, max(0.0, confidence)


def fine_tune_bext_offset_by_correlation(
    clip_path: Path,
    wav_path: Path,
    bext_offset_seconds: float,
    *,
    search_range_seconds: float = 12.0,
    sample_duration_seconds: float = 30.0,
    sample_rate: int = 100,
    min_confidence: float = 0.10,
    ffmpeg_path: str = "ffmpeg",
) -> tuple[float, float]:
    """BEXT 기반 오프셋을 ±search_range_seconds 범위에서 raw PCM correlation으로 정밀화.

    DJI creation_time은 초 단위이므로 실제 녹화 시작과 수 초 오차가 있을 수 있다.
    raw PCM cross-correlation으로 클립 내장 오디오와 WAV 해당 구간을 비교해
    실제 WAV 시작 위치를 찾는다.
    confidence가 min_confidence 미만이면 원래 bext_offset_seconds를 그대로 반환한다.

    Returns:
        (refined_offset_seconds, confidence) 튜플
    """
    wav_start = max(0.0, bext_offset_seconds - search_range_seconds)
    wav_duration = sample_duration_seconds + 2 * search_range_seconds

    clip_samples = _extract_mono_pcm_segment(
        clip_path,
        start_seconds=0.0,
        duration_seconds=sample_duration_seconds,
        sample_rate=sample_rate,
        ffmpeg_path=ffmpeg_path,
    )
    wav_samples = _extract_mono_pcm_segment(
        wav_path,
        start_seconds=wav_start,
        duration_seconds=wav_duration,
        sample_rate=sample_rate,
        ffmpeg_path=ffmpeg_path,
    )

    if not clip_samples or not wav_samples:
        return bext_offset_seconds, 0.0

    search_range_frames = int(search_range_seconds * sample_rate)
    lag_frames, confidence = _raw_pcm_correlation_offset(
        clip_samples, wav_samples, search_range_frames
    )

    if confidence < min_confidence:
        logger.warning(
            "BEXT fine-tuning: confidence %.3f < %.3f, 원래 오프셋 사용 (%.3fs)",
            confidence,
            min_confidence,
            bext_offset_seconds,
        )
        return bext_offset_seconds, confidence

    refined_offset = wav_start + search_range_seconds + lag_frames / sample_rate
    logger.debug(
        "BEXT fine-tuning: %.3fs → %.3fs (delta=%+.3fs, conf=%.3f)",
        bext_offset_seconds,
        refined_offset,
        refined_offset - bext_offset_seconds,
        confidence,
    )
    return refined_offset, confidence


def calculate_clap_sync_offset(
    reference_path: Path,
    external_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    sample_rate: int = 1000,
) -> AudioSyncOffset:
    """두 미디어 파일에서 샘플을 추출해 clap sync offset을 계산한다."""
    reference_samples = extract_mono_pcm_samples(
        reference_path,
        ffmpeg_path=ffmpeg_path,
        sample_rate=sample_rate,
    )
    external_samples = extract_mono_pcm_samples(
        external_path,
        ffmpeg_path=ffmpeg_path,
        sample_rate=sample_rate,
    )
    return estimate_clap_sync_offset(reference_samples, external_samples, sample_rate)


def calculate_clap_sync_drift(
    reference_path: Path,
    external_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
    sample_rate: int = 1000,
) -> AudioSyncDrift:
    """두 미디어 파일에서 샘플을 추출해 offset과 drift tempo를 계산한다."""
    reference_samples = extract_mono_pcm_samples(
        reference_path,
        ffmpeg_path=ffmpeg_path,
        sample_rate=sample_rate,
    )
    external_samples = extract_mono_pcm_samples(
        external_path,
        ffmpeg_path=ffmpeg_path,
        sample_rate=sample_rate,
    )
    return estimate_clap_sync_with_drift(reference_samples, external_samples, sample_rate)


def _energy_envelope(
    samples: Sequence[float],
    sample_rate: int,
    *,
    frame_seconds: float = 0.1,
) -> list[float]:
    """샘플을 저해상도 에너지 envelope로 변환한다."""
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be > 0, got: {sample_rate}")
    frame_size = max(1, int(sample_rate * frame_seconds))
    envelope: list[float] = []
    for start in range(0, len(samples), frame_size):
        frame = samples[start : start + frame_size]
        if not frame:
            continue
        envelope.append(sum(map(abs, frame)) / len(frame))
    return envelope


def _normalize(values: Sequence[float]) -> list[float]:
    """상관관계 계산을 위해 평균 0, 표준편차 1에 가깝게 정규화한다."""
    if not values:
        return []
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    stddev = variance**0.5
    if stddev <= 1e-9:
        raise AudioSyncError("Audio envelope is too flat to match reliably")
    return [(value - mean) / stddev for value in values]


def estimate_segment_by_transient(
    reference_samples: Sequence[float],
    external_samples: Sequence[float],
    sample_rate: int,
    *,
    external_path: Path,
    reference_duration_seconds: float,
    search_start_seconds: float = 0.0,
    search_slack_seconds: float = 120.0,
    min_confidence: float = 0.35,
) -> ExternalAudioSegment:
    """transient 매칭으로 긴 외부 녹음에서 reference clip의 시작 위치를 찾는다.

    envelope 상관 분석이 저신뢰도일 때 fallback으로 사용한다. 악기 연주처럼
    명확한 어택 transient가 있는 경우 환경음 기반 envelope보다 정확하다.

    - reference: 첫 30초에서만 대표 transient를 찾는다 (클립 시작점 기준).
    - external: search_start_seconds 주변 search_slack_seconds 창에서만 찾는다.
      창을 전체 clip 길이로 늘리면 클립마다 누적 오차가 발생하므로 slack만 사용.
    """
    ref_window_seconds = min(30.0, reference_duration_seconds)
    ref_window_size = max(1, int(ref_window_seconds * sample_rate))
    reference_candidates = find_transient_candidates(
        list(reference_samples[:ref_window_size]), sample_rate
    )
    if not reference_candidates:
        raise AudioSyncError("Reference clip has no detectable transients for segment matching")

    ref_transient = reference_candidates[0]

    search_start_sample = int(search_start_seconds * sample_rate)
    if search_start_sample >= len(external_samples):
        raise AudioSyncError("Search window start exceeds external audio length")

    search_end_sample = min(
        int((search_start_seconds + search_slack_seconds) * sample_rate),
        len(external_samples),
    )
    external_window = list(external_samples[search_start_sample:search_end_sample])
    window_candidates = find_transient_candidates(external_window, sample_rate)
    if not window_candidates:
        raise AudioSyncError("No transient candidates found in external audio search window")

    best_candidate = window_candidates[0]
    # search_start_sample / sample_rate로 실제 슬라이싱 시작 시점을 기준으로 계산해
    # search_start_seconds와의 반올림 오차(최대 1/sample_rate)를 제거한다.
    external_abs_time = search_start_sample / sample_rate + best_candidate.time_seconds
    segment_start = max(0.0, external_abs_time - ref_transient.time_seconds)

    confidence = min(1.0, min(ref_transient.score, best_candidate.score) / 10.0)
    if confidence < min_confidence:
        raise AudioSyncError(
            f"Transient sync confidence too low: {confidence:.2f} (min={min_confidence:.2f})"
        )

    return ExternalAudioSegment(
        path=external_path,
        start_seconds=segment_start,
        duration_seconds=reference_duration_seconds,
        confidence=confidence,
    )


def estimate_external_audio_segment(
    reference_samples: Sequence[float],
    external_samples: Sequence[float],
    sample_rate: int,
    *,
    external_path: Path,
    reference_duration_seconds: float,
    search_start_seconds: float = 0.0,
    search_end_seconds: float | None = None,
    min_confidence: float = 0.35,
) -> ExternalAudioSegment:
    """긴 외부 녹음에서 reference_samples와 가장 잘 맞는 구간을 찾는다.

    박수 한 번만 보는 방식이 아니라 에너지 envelope의 정규화 상관관계를
    사용한다. 반환되는 start_seconds는 외부 녹음에서 clip 오디오가 시작되는
    시점이다.

    search_end_seconds를 지정하면 그 시점까지만 탐색한다. 전체 WAV에서
    false positive를 막기 위해 scope=long에서는 반드시 상한을 전달해야 한다.
    """
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be > 0, got: {sample_rate}")
    if reference_duration_seconds <= 0:
        raise ValueError(
            f"reference_duration_seconds must be > 0, got: {reference_duration_seconds}"
        )
    if search_start_seconds < 0:
        raise ValueError(f"search_start_seconds must be >= 0, got: {search_start_seconds}")

    frame_seconds = 0.1
    reference_env = _normalize(_energy_envelope(reference_samples, sample_rate))
    external_env = _normalize(_energy_envelope(external_samples, sample_rate))
    if len(reference_env) < 2:
        raise AudioSyncError("Reference audio is too short to match")
    if len(external_env) < len(reference_env):
        raise AudioSyncError("External audio is shorter than reference clip")

    start_frame = min(
        max(0, int(search_start_seconds / frame_seconds)),
        len(external_env) - len(reference_env),
    )
    ref_len = len(reference_env)
    natural_end = len(external_env) - ref_len + 1
    if search_end_seconds is not None:
        bound_end = max(start_frame + 1, int(search_end_seconds / frame_seconds) - ref_len + 1)
        end_frame = min(natural_end, bound_end)
    else:
        end_frame = natural_end

    best_index = start_frame
    best_corr = -1.0
    for index in range(start_frame, end_frame):
        window = external_env[index : index + ref_len]
        corr = sum(map(operator.mul, reference_env, window)) / ref_len
        if corr > best_corr:
            best_corr = corr
            best_index = index

    confidence = max(0.0, min(1.0, (best_corr + 1.0) / 2.0))
    if confidence < min_confidence:
        raise AudioSyncError(
            f"External audio segment confidence too low: {confidence:.2f} "
            f"(min={min_confidence:.2f})"
        )

    return ExternalAudioSegment(
        path=external_path,
        start_seconds=best_index * frame_seconds,
        duration_seconds=reference_duration_seconds,
        confidence=confidence,
    )


_SEGMENT_SEARCH_SLACK_SECONDS = 120.0

# 카메라/레코더 간 허용하는 시계 오차 상한 (초).
# WAV 타임라인에서 클립 시작 시각이 WAV 경계 근처일 때 이 값만큼 여유를 준다.
_CLOCK_TOLERANCE_SECONDS: float = 15.0
_CLOCK_TOLERANCE = timedelta(seconds=_CLOCK_TOLERANCE_SECONDS)


def _clamp_wav_ss(wav_ss: float, upper: float) -> float:
    """WAV seek 위치를 [0, upper] 범위로 클램핑한다.

    단일 WAV 케이스는 ``upper = wav_duration - clip_dur`` 를 전달해
    clip이 WAV 경계를 벗어나지 않도록 한다.
    span 케이스는 ``upper = wav_duration`` 을 전달해 시작점만 클램핑한다.
    """
    return max(0.0, min(wav_ss, upper))


def calculate_external_audio_segments(
    reference_paths: Sequence[Path],
    external_path: Path,
    *,
    reference_durations: dict[Path, float],
    ffmpeg_path: str = "ffmpeg",
    sample_rate: int = 200,
    min_confidence: float = 0.35,
    clap_sync_fallback: bool = False,
) -> dict[Path, ExternalAudioSegment]:
    """긴 외부 녹음 1개에서 각 영상 클립에 대응하는 구간 맵을 계산한다.

    각 클립 탐색 범위는 search_start + clip_duration + slack으로 제한해
    distant false positive를 방지한다. envelope 상관이 min_confidence 미만이고
    clap_sync_fallback=True이면 transient 매칭으로 재시도한다.
    """
    if not reference_paths:
        return {}

    external_samples = extract_mono_pcm_samples(
        external_path,
        ffmpeg_path=ffmpeg_path,
        sample_rate=sample_rate,
    )
    segments: dict[Path, ExternalAudioSegment] = {}
    search_start_seconds = 0.0
    for reference_path in reference_paths:
        reference_samples = extract_mono_pcm_samples(
            reference_path,
            ffmpeg_path=ffmpeg_path,
            sample_rate=sample_rate,
        )
        duration_seconds = reference_durations[reference_path]
        search_end_seconds = search_start_seconds + duration_seconds + _SEGMENT_SEARCH_SLACK_SECONDS
        try:
            segment = estimate_external_audio_segment(
                reference_samples,
                external_samples,
                sample_rate,
                external_path=external_path,
                reference_duration_seconds=duration_seconds,
                search_start_seconds=search_start_seconds,
                search_end_seconds=search_end_seconds,
                min_confidence=min_confidence,
            )
        except AudioSyncError:
            if not clap_sync_fallback:
                raise
            segment = estimate_segment_by_transient(
                reference_samples,
                external_samples,
                sample_rate,
                external_path=external_path,
                reference_duration_seconds=duration_seconds,
                search_start_seconds=search_start_seconds,
                search_slack_seconds=_SEGMENT_SEARCH_SLACK_SECONDS,
                min_confidence=min_confidence,
            )
        segments[reference_path] = segment
        search_start_seconds = segment.start_seconds + segment.duration_seconds

    return segments


def calculate_external_audio_segments_from_timestamps(
    reference_paths: Sequence[Path],
    external_path: Path,
    *,
    reference_durations: dict[Path, float],
    reference_timestamps: dict[Path, datetime],
    wav_start_offset_seconds: float = 0.0,
) -> dict[Path, ExternalAudioSegment]:
    """클립 타임스탬프 기반으로 긴 외부 녹음의 클립별 시작 위치를 계산한다.

    오디오 분석 없이 각 클립의 촬영 시각 차이로 WAV 위치를 결정한다.
    WAV 녹음 시작이 첫 번째 클립 시작과 동시라고 가정하며,
    ``wav_start_offset_seconds`` 로 WAV가 먼저/늦게 시작한 경우를 보정한다.
    (양수 → WAV가 클립1보다 먼저 시작, 음수 → WAV가 늦게 시작)
    """
    if not reference_paths:
        return {}

    sorted_paths = sorted(reference_paths, key=lambda p: reference_timestamps[p])
    base_time = reference_timestamps[sorted_paths[0]]

    segments: dict[Path, ExternalAudioSegment] = {}
    for path in reference_paths:
        elapsed = (reference_timestamps[path] - base_time).total_seconds()
        wav_start = elapsed + wav_start_offset_seconds
        if wav_start < 0:
            raise AudioSyncError(
                f"WAV 시작 오프셋({wav_start_offset_seconds:.1f}초)이 너무 작아 "
                f"{path.name}의 WAV 시작 위치({wav_start:.1f}초)가 음수가 됩니다. "
                "양수 값(WAV가 클립보다 먼저 시작)을 사용하거나 0으로 설정하세요."
            )
        segments[path] = ExternalAudioSegment(
            path=external_path,
            start_seconds=wav_start,
            duration_seconds=reference_durations[path],
            confidence=1.0,
        )
    return segments


def probe_media_duration(path: Path, *, ffprobe_path: str = "ffprobe") -> float:
    """ffprobe로 미디어 길이를 초 단위로 조회한다."""
    cmd = [
        ffprobe_path,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=AUDIO_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioSyncError(
            f"Timed out probing media duration for {path} after {AUDIO_PROBE_TIMEOUT_SECONDS:.0f}s"
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise AudioSyncError(f"Failed to probe media duration for {path}: {stderr}")
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise AudioSyncError(f"Invalid media duration for {path}: {result.stdout!r}") from exc
    if duration <= 0:
        raise AudioSyncError(f"Invalid media duration for {path}: {duration}")
    return duration


def _score_external_audio_candidate(
    *,
    video_duration_seconds: float,
    video_creation_time: datetime,
    candidate_duration_seconds: float,
    candidate_mtime: datetime,
    match_window_seconds: float = DEFAULT_EXTERNAL_AUDIO_MATCH_WINDOW_SECONDS,
) -> float:
    """영상 길이와 파일 시각 근접도로 외부 오디오 후보 점수를 계산한다."""
    if video_duration_seconds <= 0:
        raise ValueError(f"video_duration_seconds must be > 0, got: {video_duration_seconds}")
    if candidate_duration_seconds <= 0:
        return 0.0
    if match_window_seconds <= 0:
        raise ValueError(f"match_window_seconds must be > 0, got: {match_window_seconds}")

    duration_delta = abs(candidate_duration_seconds - video_duration_seconds)
    duration_score = max(0.0, 1.0 - duration_delta / video_duration_seconds)
    mtime_delta = abs((candidate_mtime - video_creation_time).total_seconds())
    time_score = max(0.0, 1.0 - mtime_delta / match_window_seconds)
    return duration_score * 0.65 + time_score * 0.35


def select_external_audio_candidate(
    directory: Path,
    *,
    video_creation_time: datetime,
    video_duration_seconds: float,
    ffprobe_path: str = "ffprobe",
    match_window_seconds: float = DEFAULT_EXTERNAL_AUDIO_MATCH_WINDOW_SECONDS,
    min_score: float = 0.2,
) -> ExternalAudioCandidate:
    """디렉토리에서 영상과 가장 가까운 외부 오디오 후보를 선택한다."""
    if not directory.is_dir():
        raise AudioSyncError(f"External audio directory not found: {directory}")

    candidates: list[ExternalAudioCandidate] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTERNAL_AUDIO_EXTENSIONS:
            continue
        try:
            duration_seconds = probe_media_duration(path, ffprobe_path=ffprobe_path)
        except AudioSyncError:
            continue
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        duration_delta = abs(duration_seconds - video_duration_seconds)
        mtime_delta = abs((mtime - video_creation_time).total_seconds())
        score = _score_external_audio_candidate(
            video_duration_seconds=video_duration_seconds,
            video_creation_time=video_creation_time,
            candidate_duration_seconds=duration_seconds,
            candidate_mtime=mtime,
            match_window_seconds=match_window_seconds,
        )
        candidates.append(
            ExternalAudioCandidate(
                path=path,
                score=score,
                duration_seconds=duration_seconds,
                duration_delta_seconds=duration_delta,
                mtime_delta_seconds=mtime_delta,
            )
        )

    if not candidates:
        raise AudioSyncError(f"No supported external audio files found in: {directory}")

    best = max(candidates, key=lambda candidate: candidate.score)
    if best.score < min_score:
        raise AudioSyncError(
            f"No reliable external audio candidate found in {directory} "
            f"(best score={best.score:.2f}, min={min_score:.2f})"
        )
    return best


# ---------------------------------------------------------------------------
# BEXT 타임스탬프 기반 WAV 디렉토리 자동 매핑
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WavInfo:
    """BEXT time_reference로 파악한 WAV 파일의 타임라인 정보."""

    path: Path
    start_utc: datetime
    duration_seconds: float

    @property
    def end_utc(self) -> datetime:
        return self.start_utc + timedelta(seconds=self.duration_seconds)


def scan_wav_dir_bext(
    wav_dir: Path,
    tz_offset_seconds: int,
    *,
    ffprobe_path: str = "ffprobe",
) -> list[WavInfo]:
    """WAV 디렉토리에서 BEXT time_reference가 있는 파일만 추출해 시작 시각 순으로 반환.

    BEXT가 없는 파일(ffmpeg concat 결과물 등)은 제외한다.
    """
    if not wav_dir.is_dir():
        raise AudioSyncError(f"WAV 디렉토리가 존재하지 않거나 디렉토리가 아닙니다: {wav_dir}")
    results: list[WavInfo] = []
    for path in wav_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTERNAL_AUDIO_EXTENSIONS:
            continue
        start_utc = get_audio_bext_start_utc(path, tz_offset_seconds)
        if start_utc is None:
            continue
        try:
            duration = probe_media_duration(path, ffprobe_path=ffprobe_path)
        except AudioSyncError:
            continue
        results.append(WavInfo(path=path, start_utc=start_utc, duration_seconds=duration))

    results.sort(key=lambda w: w.start_utc)
    return results


def _create_spanning_wav(
    segments: list[tuple[Path, float, float]],
    output_path: Path,
    *,
    ffmpeg_path: str = "ffmpeg",
) -> None:
    """여러 WAV 구간을 이어 붙여 단일 WAV 파일로 만든다.

    Args:
        segments: (wav_path, start_seconds, duration_seconds) 목록 (순서 중요)
        output_path: 출력 WAV 경로
    """
    inputs: list[str] = []
    filter_parts: list[str] = []
    for i, (wav_path, start_s, dur_s) in enumerate(segments):
        inputs += ["-ss", str(start_s), "-t", str(dur_s), "-i", str(wav_path)]
        filter_parts.append(f"[{i}:a]atrim=start=0:duration={dur_s},asetpts=PTS-STARTPTS[a{i}]")

    n = len(segments)
    concat_inputs = "".join(f"[a{i}]" for i in range(n))
    filter_complex = ";".join(filter_parts) + f";{concat_inputs}concat=n={n}:v=0:a=1[out]"

    cmd = [
        ffmpeg_path,
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[out]",
        "-c:a",
        "pcm_s24le",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=AUDIO_EXTRACTION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise AudioSyncError("Timed out concatenating WAV segments") from exc
    except FileNotFoundError as exc:
        raise AudioSyncError(
            "ffmpeg을 실행할 수 없습니다. ffmpeg이 설치되어 있고 PATH에 등록되어 있는지 확인하세요."
        ) from exc
    if result.returncode != 0:
        raise AudioSyncError(f"Failed to concat WAV segments: {result.stderr[-500:]}")


def calculate_external_audio_segments_from_wav_dir(
    clip_paths: Sequence[Path],
    wav_dir: Path,
    *,
    reference_timestamps: dict[Path, datetime],
    reference_durations: dict[Path, float],
    tz_offset_seconds: int,
    temp_dir: Path,
    fine_tune: bool = True,
    ffprobe_path: str = "ffprobe",
    ffmpeg_path: str = "ffmpeg",
) -> dict[Path, ExternalAudioSegment]:
    """WAV 디렉토리의 BEXT 메타데이터로 각 클립에 맞는 WAV 구간을 자동 매핑한다.

    각 WAV 파일의 BEXT time_reference(녹음 시작 UTC)와 DJI 클립의 creation_time(UTC)을
    비교해 클립별 WAV 파일 + 시작 오프셋을 결정한다.

    클립이 두 WAV 파일 경계에 걸치는 경우 임시 concat WAV를 생성한다.
    fine_tune=True이면 raw PCM cross-correlation으로 BEXT 오프셋을 정밀화한다.
    DJI creation_time은 초 단위이므로 실제 시작과 수 초 오차가 있을 수 있다.
    """
    wav_infos = scan_wav_dir_bext(wav_dir, tz_offset_seconds, ffprobe_path=ffprobe_path)
    if not wav_infos:
        raise AudioSyncError(
            f"BEXT time_reference가 있는 WAV 파일을 찾지 못했습니다: {wav_dir}\n"
            "ffmpeg concat으로 만든 파일은 BEXT가 제거됩니다. 원본 WAV 파일 디렉토리를 지정하세요."
        )

    logger.info(
        "WAV 타임라인 구축: %d개 파일 (%s ~ %s UTC)",
        len(wav_infos),
        wav_infos[0].start_utc.strftime("%H:%M:%S"),
        wav_infos[-1].end_utc.strftime("%H:%M:%S"),
    )

    segments: dict[Path, ExternalAudioSegment] = {}
    for clip_path in clip_paths:
        clip_utc = reference_timestamps[clip_path]
        clip_dur = reference_durations[clip_path]
        clip_end_utc = clip_utc + timedelta(seconds=clip_dur)

        # 클립 시작 시각이 속하는 WAV 찾기
        # (카메라/레코더 시계 오차 최대 _CLOCK_TOLERANCE_SECONDS초 허용)
        start_wav: WavInfo | None = None
        for w in wav_infos:
            if (w.start_utc - _CLOCK_TOLERANCE) <= clip_utc < (w.end_utc + _CLOCK_TOLERANCE):
                start_wav = w
                break

        if start_wav is None:
            raise AudioSyncError(
                f"{clip_path.name}: WAV 타임라인에 포함되지 않음 "
                f"(클립 시작 {clip_utc.strftime('%H:%M:%S')} UTC, "
                f"WAV 범위 {wav_infos[0].start_utc.strftime('%H:%M:%S')}~"
                f"{wav_infos[-1].end_utc.strftime('%H:%M:%S')} UTC)"
            )

        wav_ss = (clip_utc - start_wav.start_utc).total_seconds()
        # DJI creation_time은 초 단위이므로 raw PCM correlation으로 실제 시작을 정밀화.
        # 단일/span 두 경로 모두 동일한 fine-tune 입력을 쓰므로 분기 전에 1회만 호출한다.
        conf = 1.0
        if fine_tune:
            wav_ss, conf = fine_tune_bext_offset_by_correlation(
                clip_path,
                start_wav.path,
                wav_ss,
                ffmpeg_path=ffmpeg_path,
            )

        if clip_end_utc <= start_wav.end_utc:
            # 단일 WAV 안에 완전히 포함
            if fine_tune:
                logger.info(
                    "%s: BEXT fine-tune → WAV ss=%.3fs (conf=%.3f)",
                    clip_path.name,
                    wav_ss,
                    conf,
                )
            # fine_tune 여부와 무관하게 WAV 경계 초과 방지
            wav_ss = _clamp_wav_ss(wav_ss, start_wav.duration_seconds - clip_dur)
            segments[clip_path] = ExternalAudioSegment(
                path=start_wav.path,
                start_seconds=wav_ss,
                duration_seconds=clip_dur,
                confidence=1.0,
            )
        else:
            # 두 WAV 이상에 걸쳐 있음 → 첫 WAV 오프셋 클램핑 후 임시 concat WAV 생성
            if fine_tune:
                # span 케이스: 첫 WAV 경계 초과 방지 (clip_dur 제한 없이 시작점만 클램핑)
                wav_ss = _clamp_wav_ss(wav_ss, start_wav.duration_seconds)
                logger.info(
                    "%s: BEXT fine-tune (span) → WAV ss=%.3fs (conf=%.3f)",
                    clip_path.name,
                    wav_ss,
                    conf,
                )

            span_segments: list[tuple[Path, float, float]] = []
            # fine-tune된 wav_ss로 실제 시작 epoch(초)를 역산해 span_segments 재구성.
            # timedelta 객체 대신 float 산술로 처리해 루프 내 allocation을 줄인다.
            epoch = start_wav.start_utc.timestamp()
            remaining_start_epoch = epoch + wav_ss
            remaining_dur = clip_dur

            for w in wav_infos:
                if remaining_dur <= 0:
                    break
                w_end_epoch = w.end_utc.timestamp()
                if w_end_epoch <= remaining_start_epoch:
                    continue
                w_start_epoch = w.start_utc.timestamp()
                if w_start_epoch > remaining_start_epoch:
                    # WAV 파일 사이에 gap 존재
                    gap = w_start_epoch - remaining_start_epoch
                    logger.warning(
                        "%s: WAV 파일 간 %.1fs gap 감지 — 해당 구간 오디오 없음",
                        clip_path.name,
                        gap,
                    )
                    break
                seg_start_in_wav = remaining_start_epoch - w_start_epoch
                available = w.duration_seconds - seg_start_in_wav
                use_dur = min(remaining_dur, available)
                if use_dur > 0:
                    span_segments.append((w.path, seg_start_in_wav, use_dur))
                    remaining_start_epoch += use_dur
                    remaining_dur -= use_dur

            if not span_segments:
                raise AudioSyncError(
                    f"{clip_path.name}: WAV 파일에서 유효한 오디오 구간을 찾지 못했습니다. "
                    "WAV 파일이 클립 촬영 시각과 겹치는지 확인하세요."
                )
            if remaining_dur > 1e-3:
                logger.warning(
                    "%s: WAV 커버리지 부족 — %.3fs 미포함 (클립 끝 오디오 없음)",
                    clip_path.name,
                    remaining_dur,
                )

            concat_path = temp_dir / f"span_{uuid.uuid4().hex[:8]}.wav"
            logger.info(
                "%s: WAV 경계 걸침 → %d개 WAV concat → %s",
                clip_path.name,
                len(span_segments),
                concat_path.name,
            )
            _create_spanning_wav(span_segments, concat_path, ffmpeg_path=ffmpeg_path)
            segments[clip_path] = ExternalAudioSegment(
                path=concat_path,
                start_seconds=0.0,
                duration_seconds=clip_dur,
                confidence=1.0,
            )

    return segments
