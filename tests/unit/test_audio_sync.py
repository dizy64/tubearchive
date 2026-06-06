"""외부 오디오 clap sync 단위 테스트."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.domain.media.audio_sync import (
    AudioSyncError,
    ExternalAudioSegment,
    _score_external_audio_candidate,
    calculate_external_audio_segments_from_timestamps,
    estimate_clap_sync_offset,
    estimate_clap_sync_with_drift,
    estimate_external_audio_segment,
    estimate_segment_by_transient,
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


def test_estimate_segment_by_transient_finds_start_from_peak() -> None:
    """외부 오디오 검색창 내에서 가장 강한 transient로 시작점을 계산한다."""
    # reference: 1초 지점에 강한 피크
    reference = [0.01] * 2000
    reference[1000] = 5.0
    reference[1001] = 3.5

    # external: 3초 지점에 같은 강도 피크 (reference 피크가 1초에 있으므로 segment_start = 2.0s)
    external = [0.01] * 8000
    external[3000] = 5.0
    external[3001] = 3.5

    segment = estimate_segment_by_transient(
        reference,
        external,
        sample_rate=1000,
        external_path=Path("recorder.wav"),
        reference_duration_seconds=2.0,
    )

    assert segment.path == Path("recorder.wav")
    assert segment.start_seconds == pytest.approx(2.0, abs=0.1)
    assert segment.duration_seconds == pytest.approx(2.0)
    assert segment.confidence > 0.35


def test_estimate_segment_by_transient_respects_search_start() -> None:
    """search_start_seconds 이후 slack 창에서만 transient를 찾는다."""
    reference = [0.01] * 1000
    reference[100] = 6.0

    # 1초에도 피크, 5초에도 피크 — search_start=3s, slack=4s → 5초 피크가 창 내 있음
    external = [0.01] * 10000
    external[1000] = 6.0  # 창 밖 (search_start=3s 이전)
    external[5000] = 6.0  # 창 내 (3~7s)

    segment = estimate_segment_by_transient(
        reference,
        external,
        sample_rate=1000,
        external_path=Path("recorder.wav"),
        reference_duration_seconds=1.0,
        search_start_seconds=3.0,
        search_slack_seconds=4.0,
    )

    # external 피크 = 5초, reference 피크 = 0.1초 → segment_start ≈ 4.9s
    assert segment.start_seconds == pytest.approx(4.9, abs=0.15)


def test_estimate_segment_by_transient_raises_when_no_transient_in_reference() -> None:
    """reference에 transient가 없으면 AudioSyncError가 발생한다."""
    reference = [0.01] * 1000  # 모두 균일한 노이즈, 피크 없음
    external = [0.01] * 3000
    external[1500] = 5.0

    with pytest.raises(AudioSyncError, match="transients"):
        estimate_segment_by_transient(
            reference,
            external,
            sample_rate=1000,
            external_path=Path("recorder.wav"),
            reference_duration_seconds=1.0,
        )


def test_calculate_external_audio_segments_uses_clap_fallback_on_low_confidence() -> None:
    """envelope 신뢰도가 낮을 때 clap_sync_fallback=True이면 transient 매칭으로 재시도한다."""
    from unittest.mock import patch

    from tubearchive.domain.media.audio_sync import (
        AudioSyncError,
        calculate_external_audio_segments,
    )

    # envelope 분석은 항상 실패, transient 매칭은 성공 시나리오
    fake_segment = ExternalAudioSegment(
        path=Path("ext.wav"),
        start_seconds=5.0,
        duration_seconds=2.0,
        confidence=0.7,
    )
    with (
        patch(
            "tubearchive.domain.media.audio_sync.extract_mono_pcm_samples",
            return_value=[0.0] * 400,
        ),
        patch(
            "tubearchive.domain.media.audio_sync.estimate_external_audio_segment",
            side_effect=AudioSyncError("low confidence"),
        ),
        patch(
            "tubearchive.domain.media.audio_sync.estimate_segment_by_transient",
            return_value=fake_segment,
        ) as mock_transient,
    ):
        result = calculate_external_audio_segments(
            [Path("clip.mp4")],
            Path("ext.wav"),
            reference_durations={Path("clip.mp4"): 2.0},
            clap_sync_fallback=True,
        )

    mock_transient.assert_called_once()
    assert result[Path("clip.mp4")] == fake_segment


def test_calculate_external_audio_segments_raises_without_clap_fallback() -> None:
    """clap_sync_fallback=False이면 envelope 실패 시 예외가 전파된다."""
    from unittest.mock import patch

    from tubearchive.domain.media.audio_sync import calculate_external_audio_segments

    with (
        patch(
            "tubearchive.domain.media.audio_sync.extract_mono_pcm_samples",
            return_value=[0.0] * 400,
        ),
        patch(
            "tubearchive.domain.media.audio_sync.estimate_external_audio_segment",
            side_effect=AudioSyncError("low confidence"),
        ),
        pytest.raises(AudioSyncError),
    ):
        calculate_external_audio_segments(
            [Path("clip.mp4")],
            Path("ext.wav"),
            reference_durations={Path("clip.mp4"): 2.0},
            clap_sync_fallback=False,
        )


def test_calculate_external_audio_segments_from_timestamps_basic() -> None:
    """타임스탬프 기반으로 WAV 위치가 클립 간 시각 차이로 계산된다."""
    base = datetime(2026, 6, 5, 10, 8, 10)
    clips = [Path("clip1.mp4"), Path("clip2.mp4"), Path("clip3.mp4")]
    timestamps = {
        clips[0]: base,
        clips[1]: datetime(2026, 6, 5, 10, 10, 14),  # +124s
        clips[2]: datetime(2026, 6, 5, 10, 50, 39),  # +2549s
    }
    durations = {clips[0]: 62.0, clips[1]: 2423.0, clips[2]: 2424.0}

    result = calculate_external_audio_segments_from_timestamps(
        clips,
        Path("recorder.wav"),
        reference_durations=durations,
        reference_timestamps=timestamps,
    )

    assert result[clips[0]].start_seconds == pytest.approx(0.0)
    assert result[clips[1]].start_seconds == pytest.approx(124.0)
    assert result[clips[2]].start_seconds == pytest.approx(2549.0)
    assert all(seg.confidence == 1.0 for seg in result.values())


def test_calculate_external_audio_segments_from_timestamps_wav_offset() -> None:
    """wav_start_offset_seconds가 양수이면 WAV가 클립1보다 먼저 시작한 만큼 보정된다."""
    base = datetime(2026, 6, 5, 10, 0, 0)
    clips = [Path("clip1.mp4"), Path("clip2.mp4")]
    timestamps = {
        clips[0]: base,
        clips[1]: datetime(2026, 6, 5, 10, 1, 0),  # +60s
    }
    durations = {clips[0]: 60.0, clips[1]: 60.0}

    result = calculate_external_audio_segments_from_timestamps(
        clips,
        Path("recorder.wav"),
        reference_durations=durations,
        reference_timestamps=timestamps,
        wav_start_offset_seconds=10.0,  # WAV가 10초 먼저 시작
    )

    assert result[clips[0]].start_seconds == pytest.approx(10.0)
    assert result[clips[1]].start_seconds == pytest.approx(70.0)


def test_calculate_external_audio_segments_from_timestamps_negative_offset_clamped() -> None:
    """wav_start_offset_seconds가 음수이더라도 start_seconds는 0 미만으로 내려가지 않는다."""
    base = datetime(2026, 6, 5, 10, 0, 0)
    clips = [Path("clip1.mp4")]
    timestamps = {clips[0]: base}
    durations = {clips[0]: 60.0}

    result = calculate_external_audio_segments_from_timestamps(
        clips,
        Path("recorder.wav"),
        reference_durations=durations,
        reference_timestamps=timestamps,
        wav_start_offset_seconds=-5.0,  # WAV가 5초 늦게 시작 → 클립1의 시작은 0으로 클램프
    )

    assert result[clips[0]].start_seconds == pytest.approx(0.0)
