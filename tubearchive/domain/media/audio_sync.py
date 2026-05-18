"""외부 오디오와 영상 내장 오디오의 clap 기반 싱크 추정."""

from __future__ import annotations

import subprocess
import sys
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median

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
) -> list[float]:
    """FFmpeg로 media_path의 첫 오디오 스트림을 mono s16le 샘플로 추출한다."""
    cmd = [
        ffmpeg_path,
        "-v",
        "error",
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
    raw.frombytes(result.stdout)
    if sys.byteorder != "little":
        raw.byteswap()
    return [sample / 32768.0 for sample in raw]


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
        envelope.append(sum(abs(sample) for sample in frame) / len(frame))
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


def estimate_external_audio_segment(
    reference_samples: Sequence[float],
    external_samples: Sequence[float],
    sample_rate: int,
    *,
    external_path: Path,
    reference_duration_seconds: float,
    search_start_seconds: float = 0.0,
    min_confidence: float = 0.35,
) -> ExternalAudioSegment:
    """긴 외부 녹음에서 reference_samples와 가장 잘 맞는 구간을 찾는다.

    박수 한 번만 보는 방식이 아니라 에너지 envelope의 정규화 상관관계를
    사용한다. 반환되는 start_seconds는 외부 녹음에서 clip 오디오가 시작되는
    시점이다.
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

    best_index = start_frame
    best_corr = -1.0
    ref_len = len(reference_env)
    for index in range(start_frame, len(external_env) - ref_len + 1):
        window = external_env[index : index + ref_len]
        corr = sum(a * b for a, b in zip(reference_env, window, strict=True)) / ref_len
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


def calculate_external_audio_segments(
    reference_paths: Sequence[Path],
    external_path: Path,
    *,
    reference_durations: dict[Path, float],
    ffmpeg_path: str = "ffmpeg",
    sample_rate: int = 200,
    min_confidence: float = 0.35,
) -> dict[Path, ExternalAudioSegment]:
    """긴 외부 녹음 1개에서 각 영상 클립에 대응하는 구간 맵을 계산한다."""
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
        segment = estimate_external_audio_segment(
            reference_samples,
            external_samples,
            sample_rate,
            external_path=external_path,
            reference_duration_seconds=duration_seconds,
            search_start_seconds=search_start_seconds,
            min_confidence=min_confidence,
        )
        segments[reference_path] = segment
        search_start_seconds = segment.start_seconds + segment.duration_seconds

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
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
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
