"""외부 오디오 clap sync 단위 테스트."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.domain.media.audio_sync import (
    AudioSyncError,
    ExternalAudioSegment,
    WavInfo,
    _score_external_audio_candidate,
    calculate_external_audio_segments,
    calculate_external_audio_segments_from_timestamps,
    calculate_external_audio_segments_from_wav_dir,
    estimate_clap_sync_offset,
    estimate_clap_sync_with_drift,
    estimate_external_audio_segment,
    estimate_segment_by_transient,
    extract_mono_pcm_samples,
    find_transient_candidates,
    fine_tune_bext_offset_by_correlation,
    probe_media_duration,
    scan_wav_dir_bext,
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
    video_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

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

    video_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
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


def test_calculate_external_audio_segments_from_timestamps_negative_offset_raises() -> None:
    """wav_start_offset_seconds가 음수여서 첫 클립의 WAV 시작 위치가 음수가 되면 AudioSyncError."""
    from tubearchive.domain.media.audio_sync import AudioSyncError

    base = datetime(2026, 6, 5, 10, 0, 0)
    clips = [Path("clip1.mp4")]
    timestamps = {clips[0]: base}
    durations = {clips[0]: 60.0}

    with pytest.raises(AudioSyncError, match="음수"):
        calculate_external_audio_segments_from_timestamps(
            clips,
            Path("recorder.wav"),
            reference_durations=durations,
            reference_timestamps=timestamps,
            wav_start_offset_seconds=-5.0,
        )


# ---------------------------------------------------------------------------
# WavInfo / scan_wav_dir_bext / calculate_external_audio_segments_from_wav_dir
# ---------------------------------------------------------------------------


class TestScanWavDirBext:
    def test_returns_sorted_by_start_utc(self, tmp_path: Path) -> None:
        """BEXT가 있는 WAV 2개를 시작 시각 오름차순으로 반환한다."""
        wav_a = tmp_path / "0006.wav"
        wav_b = tmp_path / "0007.wav"
        wav_a.touch()
        wav_b.touch()

        utc_a = datetime(2026, 6, 5, 10, 3, 36)  # 19:03:36 KST
        utc_b = datetime(2026, 6, 5, 11, 36, 46)  # 20:36:46 KST

        with (
            patch(
                "tubearchive.domain.media.audio_sync.get_audio_bext_start_utc",
                side_effect=lambda p, tz: utc_b if "0007" in p.name else utc_a,
            ),
            patch(
                "tubearchive.domain.media.audio_sync.probe_media_duration",
                side_effect=lambda p, **_: 841.0 if "0007" in p.name else 5589.0,
            ),
        ):
            result = scan_wav_dir_bext(tmp_path, tz_offset_seconds=32400)

        assert len(result) == 2
        assert result[0].path == wav_a
        assert result[0].start_utc == utc_a
        assert result[1].path == wav_b

    def test_skips_files_without_bext(self, tmp_path: Path) -> None:
        """BEXT time_reference가 없는 WAV는 결과에서 제외된다."""
        (tmp_path / "no_bext.wav").touch()

        with patch(
            "tubearchive.domain.media.audio_sync.get_audio_bext_start_utc",
            return_value=None,
        ):
            result = scan_wav_dir_bext(tmp_path, tz_offset_seconds=32400)

        assert result == []

    def test_skips_non_wav_files(self, tmp_path: Path) -> None:
        """WAV 이외 파일은 스캔 대상에서 제외된다."""
        (tmp_path / "video.mp4").touch()
        (tmp_path / "readme.txt").touch()

        with (
            patch(
                "tubearchive.domain.media.audio_sync.get_audio_bext_start_utc",
                return_value=datetime(2026, 6, 5, 10, 0, 0),
            ),
            patch(
                "tubearchive.domain.media.audio_sync.probe_media_duration",
                return_value=100.0,
            ),
        ):
            result = scan_wav_dir_bext(tmp_path, tz_offset_seconds=32400)

        assert result == []


class TestCalculateExternalAudioSegmentsFromWavDir:
    """calculate_external_audio_segments_from_wav_dir 단위 테스트."""

    def _make_wav_infos(self) -> list[WavInfo]:
        """0006(19:03:36~20:36:45) + 0007(20:36:46~20:50:49) 타임라인."""
        utc_a = datetime(2026, 6, 5, 10, 3, 36)  # 19:03:36 KST → UTC+9
        utc_b = datetime(2026, 6, 5, 11, 36, 46)  # 20:36:46 KST
        return [
            WavInfo(path=Path("/wav/0006.wav"), start_utc=utc_a, duration_seconds=5589.0),
            WavInfo(path=Path("/wav/0007.wav"), start_utc=utc_b, duration_seconds=841.0),
        ]

    def test_each_clip_in_single_wav(self, tmp_path: Path) -> None:
        """클립 0001~0003이 단일 WAV(0006) 안에 있을 때 올바른 ss를 반환한다."""
        clip0001 = Path("DJI_20260605190810_0001_D.MP4")
        clip0002 = Path("DJI_20260605191013_0002_D.MP4")
        clip0003 = Path("DJI_20260605195039_0003_D.MP4")

        # DJI 파일명 UTC: KST - 9h
        ts = {
            clip0001: datetime(2026, 6, 5, 10, 8, 10),  # 19:08:10 KST
            clip0002: datetime(2026, 6, 5, 10, 10, 13),  # 19:10:13 KST
            clip0003: datetime(2026, 6, 5, 10, 50, 39),  # 19:50:39 KST
        }
        durations = {clip0001: 62.0, clip0002: 2423.0, clip0003: 2424.0}
        wav_infos = self._make_wav_infos()

        with patch(
            "tubearchive.domain.media.audio_sync.scan_wav_dir_bext",
            return_value=wav_infos,
        ):
            result = calculate_external_audio_segments_from_wav_dir(
                list(ts.keys()),
                Path("/wav"),
                reference_timestamps=ts,
                reference_durations=durations,
                tz_offset_seconds=32400,
                temp_dir=tmp_path,
                fine_tune=False,  # 단위 테스트에서는 BEXT 계산만 검증
            )

        # 0001: (10:08:10 - 10:03:36) = 274s
        assert result[clip0001].path == Path("/wav/0006.wav")
        assert result[clip0001].start_seconds == pytest.approx(274.0, abs=0.01)
        # 0002: (10:10:13 - 10:03:36) = 397s
        assert result[clip0002].start_seconds == pytest.approx(397.0, abs=0.01)
        # 0003: (10:50:39 - 10:03:36) = 2823s
        assert result[clip0003].start_seconds == pytest.approx(2823.0, abs=0.01)

    def test_clip_spanning_two_wavs_creates_temp_file(self, tmp_path: Path) -> None:
        """클립이 두 WAV 파일 경계를 넘을 때 임시 concat WAV를 생성한다."""
        clip0004 = Path("DJI_20260605203103_0004_D.MP4")
        # 0004: 20:31:03 KST = 11:31:03 UTC, 길이 1009s → 종료 20:47:52 KST (0007에 걸침)
        ts = {clip0004: datetime(2026, 6, 5, 11, 31, 3)}
        durations = {clip0004: 1009.0}
        wav_infos = self._make_wav_infos()

        captured_cmd: list[list[str]] = []

        def fake_run(cmd: list[str], **_: object) -> MagicMock:
            captured_cmd.append(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with (
            patch(
                "tubearchive.domain.media.audio_sync.scan_wav_dir_bext",
                return_value=wav_infos,
            ),
            patch("subprocess.run", side_effect=fake_run),
        ):
            result = calculate_external_audio_segments_from_wav_dir(
                [clip0004],
                Path("/wav"),
                reference_timestamps=ts,
                reference_durations=durations,
                tz_offset_seconds=32400,
                temp_dir=tmp_path,
                fine_tune=False,  # subprocess.run mock과 충돌 방지
            )

        # 결과 path는 tmp_path 아래 임시 파일
        assert result[clip0004].path.parent == tmp_path
        assert result[clip0004].start_seconds == pytest.approx(0.0)
        assert result[clip0004].duration_seconds == pytest.approx(1009.0)
        # FFmpeg가 concat 명령으로 호출됐는지 확인
        assert any("ffmpeg" in " ".join(cmd) for cmd in captured_cmd)

    def test_clip_not_covered_by_any_wav_raises(self, tmp_path: Path) -> None:
        """어느 WAV에도 없는 시각의 클립은 AudioSyncError를 발생시킨다."""
        clip = Path("clip_outside.mp4")
        ts = {clip: datetime(2025, 1, 1, 0, 0, 0)}  # WAV 타임라인 밖
        durations = {clip: 60.0}

        with (
            patch(
                "tubearchive.domain.media.audio_sync.scan_wav_dir_bext",
                return_value=self._make_wav_infos(),
            ),
            pytest.raises(AudioSyncError, match="WAV 타임라인에 포함되지 않음"),
        ):
            calculate_external_audio_segments_from_wav_dir(
                [clip],
                Path("/wav"),
                reference_timestamps=ts,
                reference_durations=durations,
                tz_offset_seconds=32400,
                temp_dir=tmp_path,
            )


class TestFineTuneBextOffsetByCorrelation:
    """fine_tune_bext_offset_by_correlation 단위 테스트."""

    def _make_signal(self, length: int, peak_index: int, amplitude: float = 1.0) -> list[float]:
        samples = [0.02] * length
        samples[peak_index] = amplitude
        samples[peak_index + 1] = amplitude * 0.7
        samples[peak_index + 2] = amplitude * 0.4
        return samples

    def test_finds_known_offset_within_search_range(self) -> None:
        """합성 신호에서 2초 오차를 보정해 실제 오프셋을 찾는다.

        BEXT offset=20s, search_range=8s, 실제 offset=22s(2초 오차).
        클립 peak(0.1s)가 WAV 22s 위치와 매치 → refined ≈ 21.9s
        """
        sample_rate = 100
        sample_duration = 30
        search_range = 8
        # 클립 오디오: 30s * 100 = 3000 샘플, 피크는 처음 0.1s에
        clip_audio = self._make_signal(3000, peak_index=10)
        # WAV 구간(12~58s, 46s): 4600 샘플, WAV 22s = 구간 내 10s → index=1000
        wav_audio = self._make_signal(4600, peak_index=10 * sample_rate)

        with patch(
            "tubearchive.domain.media.audio_sync._extract_mono_pcm_segment",
            # 클립 먼저 추출, WAV 나중 추출 (코드 호출 순서와 일치)
            side_effect=[clip_audio, wav_audio],
        ):
            refined, confidence = fine_tune_bext_offset_by_correlation(
                clip_path=Path("clip.mp4"),
                wav_path=Path("wav.wav"),
                bext_offset_seconds=20.0,
                search_range_seconds=float(search_range),
                sample_duration_seconds=float(sample_duration),
                sample_rate=sample_rate,
            )

        # wav_start=12, lag≈+1.9 → refined≈21.9 (clip peak at 0.1s → WAV 22s)
        assert abs(refined - 22.0) < 1.0
        assert confidence > 0.0

    def test_returns_bext_offset_on_empty_samples(self) -> None:
        """오디오 추출에 실패하면 원래 BEXT 오프셋과 confidence=0을 반환한다."""
        with patch(
            "tubearchive.domain.media.audio_sync._extract_mono_pcm_segment",
            return_value=[],
        ):
            refined, confidence = fine_tune_bext_offset_by_correlation(
                clip_path=Path("clip.mp4"),
                wav_path=Path("wav.wav"),
                bext_offset_seconds=275.0,
            )

        assert refined == pytest.approx(275.0)
        assert confidence == pytest.approx(0.0)

    def test_returns_bext_offset_on_low_confidence(self) -> None:
        """모든 샘플이 동일(무음)이면 correlation이 낮아 BEXT 오프셋을 유지한다."""
        flat_signal = [0.0] * 3000

        with patch(
            "tubearchive.domain.media.audio_sync._extract_mono_pcm_segment",
            side_effect=[flat_signal, flat_signal * 2],
        ):
            refined, _confidence = fine_tune_bext_offset_by_correlation(
                clip_path=Path("clip.mp4"),
                wav_path=Path("wav.wav"),
                bext_offset_seconds=100.0,
                min_confidence=0.25,
            )

        # 무음 신호는 correlation이 낮으므로 BEXT 오프셋 유지
        assert refined == pytest.approx(100.0)


class TestApplyClipAdjustments:
    """_apply_clip_adjustments 단위 테스트."""

    def _seg(self, start: float) -> ExternalAudioSegment:
        return ExternalAudioSegment(
            path=Path("dummy.wav"),
            start_seconds=start,
            duration_seconds=100.0,
            confidence=0.5,
            tempo_ratio=1.0,
        )

    def test_pattern_matches_filename(self) -> None:
        from tubearchive.app.cli.pipeline import _apply_clip_adjustments

        clips = {
            Path("/a/DJI_0001.MP4"): self._seg(10.0),
            Path("/a/DJI_0004.MP4"): self._seg(50.0),
        }
        result = _apply_clip_adjustments(clips, {"0004": 4.0})
        assert result[Path("/a/DJI_0001.MP4")].start_seconds == pytest.approx(10.0)
        assert result[Path("/a/DJI_0004.MP4")].start_seconds == pytest.approx(54.0)

    def test_negative_adjustment_clamped_to_zero(self) -> None:
        from tubearchive.app.cli.pipeline import _apply_clip_adjustments

        clips = {Path("/a/clip.MP4"): self._seg(1.0)}
        result = _apply_clip_adjustments(clips, {"clip": -5.0})
        assert result[Path("/a/clip.MP4")].start_seconds == pytest.approx(0.0)

    def test_no_match_leaves_segment_unchanged(self) -> None:
        from tubearchive.app.cli.pipeline import _apply_clip_adjustments

        clips = {Path("/a/DJI_0001.MP4"): self._seg(10.0)}
        result = _apply_clip_adjustments(clips, {"0004": 4.0})
        assert result[Path("/a/DJI_0001.MP4")].start_seconds == pytest.approx(10.0)
