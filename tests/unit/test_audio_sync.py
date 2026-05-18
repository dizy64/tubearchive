"""외부 오디오 clap sync 단위 테스트."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.domain.media.audio_sync import (
    AudioSyncError,
    _score_external_audio_candidate,
    estimate_clap_sync_offset,
    estimate_clap_sync_with_drift,
    estimate_external_audio_segment,
    extract_mono_pcm_samples,
    find_transient_candidates,
    probe_media_duration,
    select_external_audio_candidate,
)


def _pulse_samples(length: int, pulse_index: int, amplitude: float = 1.0) -> list[float]:
    samples = [0.01] * length
    samples[pulse_index] = amplitude
    samples[pulse_index + 1] = amplitude * 0.7
    return samples


def _multi_pulse_samples(length: int, pulse_indexes: tuple[int, ...]) -> list[float]:
    samples = [0.01] * length
    for pulse_index in pulse_indexes:
        samples[pulse_index] = 1.0
        samples[pulse_index + 1] = 0.7
    return samples


def test_find_transient_candidates_detects_clap_peak() -> None:
    """짧고 큰 transient를 clap 후보로 검출한다."""
    samples = _pulse_samples(length=5000, pulse_index=1500)

    candidates = find_transient_candidates(samples, sample_rate=1000)

    assert candidates
    assert candidates[0].time_seconds == pytest.approx(1.5, abs=0.03)
    assert candidates[0].score > 5.0


def test_estimate_clap_sync_offset_returns_reference_minus_external() -> None:
    """offset은 외부 오디오에 적용할 지연값(reference_peak - external_peak)이다."""
    reference = _pulse_samples(length=5000, pulse_index=2100)
    external = _pulse_samples(length=5000, pulse_index=1600)

    result = estimate_clap_sync_offset(reference, external, sample_rate=1000)

    assert result.offset_seconds == pytest.approx(0.5, abs=0.03)
    assert result.reference_time_seconds == pytest.approx(2.1, abs=0.03)
    assert result.external_time_seconds == pytest.approx(1.6, abs=0.03)
    assert result.confidence >= 0.8


def test_estimate_clap_sync_offset_raises_when_no_clear_transient() -> None:
    """뚜렷한 공통 피크가 없으면 자동 싱크를 실패시킨다."""
    reference = [0.01] * 5000
    external = [0.01] * 5000

    with pytest.raises(AudioSyncError, match="transient"):
        estimate_clap_sync_offset(reference, external, sample_rate=1000)


def test_estimate_clap_sync_with_drift_returns_tempo_ratio() -> None:
    """두 개 이상 clap 후보가 있으면 외부 오디오 tempo 보정 비율을 추정한다."""
    reference = _multi_pulse_samples(length=12000, pulse_indexes=(2000, 10000))
    external = _multi_pulse_samples(length=12000, pulse_indexes=(1500, 9700))

    result = estimate_clap_sync_with_drift(reference, external, sample_rate=1000)

    assert result.offset_seconds == pytest.approx(0.5, abs=0.03)
    assert result.tempo_ratio == pytest.approx(8.2 / 8.0, rel=0.01)
    assert result.confidence >= 0.8


def test_score_external_audio_candidate_prefers_duration_and_time_match() -> None:
    """외부 오디오 후보는 영상 길이와 촬영 시각에 가까울수록 높은 점수를 받는다."""
    video_time = datetime(2026, 1, 1, 12, 0, 0)

    good = _score_external_audio_candidate(
        video_duration_seconds=60.0,
        video_creation_time=video_time,
        candidate_duration_seconds=61.0,
        candidate_mtime=video_time + timedelta(seconds=10),
        match_window_seconds=300.0,
    )
    bad = _score_external_audio_candidate(
        video_duration_seconds=60.0,
        video_creation_time=video_time,
        candidate_duration_seconds=10.0,
        candidate_mtime=video_time + timedelta(hours=2),
        match_window_seconds=300.0,
    )

    assert good > bad
    assert good > 0.8
    assert bad < 0.2


def test_select_external_audio_candidate_chooses_best_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """디렉토리 후보 중 길이/시각 점수가 가장 높은 외부 오디오를 선택한다."""
    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    poor = audio_dir / "old.wav"
    best = audio_dir / "take.wav"
    poor.touch()
    best.touch()

    video_time = datetime(2026, 1, 1, 12, 0, 0)
    (tmp_path / "video.mp4").touch()
    poor_mtime = (video_time + timedelta(hours=1)).timestamp()
    best_mtime = (video_time + timedelta(seconds=8)).timestamp()
    poor.touch()
    best.touch()
    import os

    os.utime(poor, (poor_mtime, poor_mtime))
    os.utime(best, (best_mtime, best_mtime))

    durations = {poor: 5.0, best: 59.5}

    monkeypatch.setattr(
        "tubearchive.domain.media.audio_sync.probe_media_duration",
        lambda path, *, ffprobe_path="ffprobe": durations[path],
    )

    selected = select_external_audio_candidate(
        audio_dir,
        video_creation_time=video_time,
        video_duration_seconds=60.0,
    )

    assert selected.path == best
    assert selected.score > 0.8


def test_extract_mono_pcm_samples_times_out() -> None:
    """FFmpeg 샘플 추출이 멈추면 명확한 AudioSyncError로 실패한다."""
    with (
        patch(
            "tubearchive.domain.media.audio_sync.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=300),
        ),
        pytest.raises(AudioSyncError, match="Timed out extracting audio samples"),
    ):
        extract_mono_pcm_samples(Path("clip.mov"))


def test_probe_media_duration_times_out() -> None:
    """ffprobe 길이 조회가 멈추면 명확한 AudioSyncError로 실패한다."""
    with (
        patch(
            "tubearchive.domain.media.audio_sync.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["ffprobe"], timeout=30),
        ),
        pytest.raises(AudioSyncError, match="Timed out probing media duration"),
    ):
        probe_media_duration(Path("external.wav"))


def test_probe_media_duration_passes_timeout_to_subprocess() -> None:
    """ffprobe 호출에는 짧은 timeout을 반드시 전달한다."""
    mock_result = MagicMock(returncode=0, stdout="12.5\n", stderr="")
    with patch(
        "tubearchive.domain.media.audio_sync.subprocess.run",
        return_value=mock_result,
    ) as mock_run:
        assert probe_media_duration(Path("external.wav")) == 12.5

    assert mock_run.call_args.kwargs["timeout"] == 30.0


def test_estimate_external_audio_segment_finds_matching_region() -> None:
    """긴 외부 녹음에서 클립 오디오와 같은 envelope 구간의 시작점을 찾는다."""
    reference = _multi_pulse_samples(length=2000, pulse_indexes=(300, 1200))
    external = [0.01] * 6000
    external[2300] = 1.0
    external[2301] = 0.7
    external[3200] = 1.0
    external[3201] = 0.7

    segment = estimate_external_audio_segment(
        reference,
        external,
        sample_rate=1000,
        external_path=Path("recorder.wav"),
        reference_duration_seconds=2.0,
    )

    assert segment.path == Path("recorder.wav")
    assert segment.start_seconds == pytest.approx(2.0, abs=0.15)
    assert segment.duration_seconds == pytest.approx(2.0)
    assert segment.confidence > 0.7


def test_estimate_external_audio_segment_respects_search_start() -> None:
    """다음 클립 매칭은 이전 클립 이후부터 검색할 수 있다."""
    reference = _multi_pulse_samples(length=2000, pulse_indexes=(300, 1200))
    external = [0.01] * 7000
    for base in (1000, 4000):
        external[base + 300] = 1.0
        external[base + 301] = 0.7
        external[base + 1200] = 1.0
        external[base + 1201] = 0.7

    segment = estimate_external_audio_segment(
        reference,
        external,
        sample_rate=1000,
        external_path=Path("recorder.wav"),
        reference_duration_seconds=2.0,
        search_start_seconds=3.0,
    )

    assert segment.start_seconds == pytest.approx(4.0, abs=0.15)
