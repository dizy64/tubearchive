"""
오디오 검증 E2E 테스트.

2026-04-18 첫 합주 인시던트 재발 방지 검증:
  - loudnorm 96kHz 업샘플링 발생 시에도 출력 파일의 오디오 샘플레이트가 48000 Hz임을 보장
  - 병합 전 파일 간 샘플레이트 불일치 감지 및 경고 로깅 확인
  - 병합 후 오디오/비디오 길이 차이 감지 및 경고 로깅 확인
  - probe_audio_sample_rate / probe_stream_durations 실제 ffprobe 동작 확인

실행:
    uv run pytest tests/e2e/test_audio_validation.py -v
"""

import logging
import shutil
from pathlib import Path

import pytest

from tubearchive.app.cli.main import run_pipeline
from tubearchive.domain.media.merger import (
    Merger,
    probe_audio_sample_rate,
    probe_stream_durations,
)

from .conftest import (
    create_audio_truncated_video,
    create_no_audio_video,
    create_test_video,
    create_test_video_with_sample_rate,
    make_pipeline_args,
    probe_video,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard2,
]

# ---------- probe 함수 직접 검증 ----------


class TestProbeAudioSampleRate:
    """probe_audio_sample_rate: 실제 ffprobe 사용 통합 테스트."""

    def test_returns_48000_for_standard_audio(self, tmp_path: Path) -> None:
        """48kHz로 생성된 파일에서 48000 반환."""
        video = create_test_video_with_sample_rate(
            tmp_path / "video_48k.mp4",
            sample_rate=48000,
        )
        result = probe_audio_sample_rate(video)
        assert result == 48000

    def test_returns_96000_for_upsampled_audio(self, tmp_path: Path) -> None:
        """loudnorm 업샘플링 재현: 96kHz 파일에서 96000 반환."""
        video = create_test_video_with_sample_rate(
            tmp_path / "video_96k.mp4",
            sample_rate=96000,
        )
        result = probe_audio_sample_rate(video)
        assert result == 96000

    def test_returns_44100_for_44100hz_audio(self, tmp_path: Path) -> None:
        """44100Hz 파일에서 44100 반환."""
        video = create_test_video_with_sample_rate(
            tmp_path / "video_44k.mp4",
            sample_rate=44100,
        )
        result = probe_audio_sample_rate(video)
        assert result == 44100

    def test_returns_none_for_no_audio_video(self, tmp_path: Path) -> None:
        """오디오 스트림 없는 영상에서 None 반환."""
        video = create_no_audio_video(tmp_path / "no_audio.mp4", duration=2.0)
        result = probe_audio_sample_rate(video)
        assert result is None


class TestProbeStreamDurations:
    """probe_stream_durations: 실제 ffprobe 사용 통합 테스트."""

    def test_returns_both_durations_for_normal_video(self, tmp_path: Path) -> None:
        """정상 파일에서 video, audio 길이 모두 반환."""
        video = create_test_video(tmp_path / "normal.mp4", duration=3.0)
        result = probe_stream_durations(video)
        assert "video" in result
        assert "audio" in result
        # 허용 오차 0.5초
        assert abs(result["video"] - 3.0) < 0.5
        assert abs(result["audio"] - 3.0) < 0.5

    def test_returns_only_video_for_no_audio(self, tmp_path: Path) -> None:
        """오디오 스트림 없는 파일에서 video만 반환."""
        video = create_no_audio_video(tmp_path / "no_audio.mp4", duration=3.0)
        result = probe_stream_durations(video)
        assert "video" in result
        assert "audio" not in result

    def test_detects_duration_mismatch(self, tmp_path: Path) -> None:
        """오디오가 비디오보다 크게 짧은 파일: 실제 길이 차이 감지."""
        video = create_audio_truncated_video(
            tmp_path / "truncated.mp4",
            video_duration=10.0,
            audio_duration=3.0,
        )
        result = probe_stream_durations(video)
        assert "video" in result
        assert "audio" in result
        diff = abs(result["video"] - result["audio"])
        # 7초 차이 (10 - 3) → 임계값(5초)보다 훨씬 큼
        assert diff > 5.0


# ---------- Merger 샘플레이트 검증 ----------


class TestMergerSampleRateWarning:
    """Merger._check_sample_rates: 샘플레이트 불일치 시 경고 로깅."""

    def test_warns_when_sample_rates_differ(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """인시던트 재현: 48kHz + 96kHz 혼재 시 logger.warning 출력."""
        file_48k = create_test_video_with_sample_rate(
            tmp_path / "file_48k.mp4",
            sample_rate=48000,
            duration=2.0,
        )
        file_96k = create_test_video_with_sample_rate(
            tmp_path / "file_96k.mp4",
            sample_rate=96000,
            duration=2.0,
        )
        merger = Merger(temp_dir=tmp_path / "tmp")

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_sample_rates([file_48k, file_96k])

        assert any(
            "mismatch" in r.message.lower() for r in caplog.records if r.levelno >= logging.WARNING
        )

    def test_no_warning_when_rates_consistent(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """같은 샘플레이트 파일들에서 경고 없음."""
        files = [
            create_test_video_with_sample_rate(
                tmp_path / f"file_{i}.mp4",
                sample_rate=48000,
                duration=2.0,
            )
            for i in range(3)
        ]
        merger = Merger(temp_dir=tmp_path / "tmp")

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_sample_rates(files)

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_no_warning_for_no_audio_files(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """오디오 스트림 없는 파일만 있으면 경고 없음."""
        files = [
            create_no_audio_video(tmp_path / f"no_audio_{i}.mp4", duration=2.0) for i in range(2)
        ]
        merger = Merger(temp_dir=tmp_path / "tmp")

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_sample_rates(files)

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------- Merger 병합 후 길이 검증 ----------


class TestMergerDurationValidation:
    """Merger._check_merged_durations: 오디오/비디오 길이 차이 감지."""

    def test_warns_when_audio_significantly_shorter(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """오디오가 비디오보다 7초 짧은 경우 경고 (임계값 5초 초과)."""
        truncated = create_audio_truncated_video(
            tmp_path / "truncated.mp4",
            video_duration=10.0,
            audio_duration=3.0,
        )
        merger = Merger(temp_dir=tmp_path / "tmp")

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_merged_durations(truncated)

        assert any(r.levelno >= logging.WARNING for r in caplog.records), (
            "오디오 누락 경고가 출력되어야 함"
        )

    def test_no_warning_for_normal_merged_video(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """정상적으로 생성된 영상에서는 경고 없음."""
        normal = create_test_video(tmp_path / "normal.mp4", duration=5.0)
        merger = Merger(temp_dir=tmp_path / "tmp")

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_merged_durations(normal)

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------- Merger.merge() 통합 시나리오 ----------


class TestMergerMergeWithValidation:
    """Merger.merge()가 probe + 병합 + 검증을 올바른 순서로 수행하는지 확인."""

    def test_merge_two_same_rate_videos_succeeds(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """동일 샘플레이트 2개 파일 병합: 성공 + 경고 없음."""
        files = [
            create_test_video_with_sample_rate(
                tmp_path / f"clip_{i:03d}.mp4",
                sample_rate=48000,
                duration=2.0,
            )
            for i in range(2)
        ]
        merger = Merger(temp_dir=tmp_path / "merge_tmp")
        output = tmp_path / "merged.mp4"

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            result = merger.merge(files, output)

        assert result.exists()
        assert result.stat().st_size > 0
        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_merge_rate_mismatch_warns_but_produces_output(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """샘플레이트 불일치 병합: 경고를 로깅하되 출력 파일은 생성됨."""
        file_48k = create_test_video_with_sample_rate(
            tmp_path / "clip_48k.mp4",
            sample_rate=48000,
            duration=2.0,
        )
        file_96k = create_test_video_with_sample_rate(
            tmp_path / "clip_96k.mp4",
            sample_rate=96000,
            duration=2.0,
        )
        merger = Merger(temp_dir=tmp_path / "merge_tmp")
        output = tmp_path / "merged_mismatch.mp4"

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            result = merger.merge([file_48k, file_96k], output)

        # 출력 파일은 생성됨 (경고만, 오류 아님)
        assert result.exists()
        assert result.stat().st_size > 0
        # 샘플레이트 불일치 경고가 발생해야 함
        assert any(
            "mismatch" in r.message.lower() for r in caplog.records if r.levelno >= logging.WARNING
        )

    def test_merge_output_audio_stream_present(
        self,
        tmp_path: Path,
    ) -> None:
        """병합 결과 파일에 오디오 스트림이 존재함."""
        files = [
            create_test_video_with_sample_rate(
                tmp_path / f"clip_{i:03d}.mp4",
                sample_rate=48000,
                duration=2.0,
            )
            for i in range(2)
        ]
        merger = Merger(temp_dir=tmp_path / "merge_tmp")
        output = tmp_path / "merged.mp4"

        merger.merge(files, output)

        info = probe_video(output)
        audio_streams = [s for s in info["streams"] if s["codec_type"] == "audio"]
        assert len(audio_streams) >= 1


# ---------- Loudnorm 샘플레이트 회귀 테스트 (핵심) ----------


class TestLoudnormSampleRateRegression:
    """loudnorm 2-pass 사용 시 출력 오디오 샘플레이트가 반드시 48000 Hz임을 보장.

    인시던트 원인: loudnorm 필터가 내부적으로 96000 Hz로 업샘플링한 뒤 그대로 출력.
    수정 내용: EncodingProfile에 -ar 48000을 명시적으로 지정.
    이 테스트는 수정이 유지되는 한 반드시 통과해야 한다.
    """

    def test_loudnorm_output_sample_rate_is_48000(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--normalize-audio 활성화 시 출력 파일의 오디오 샘플레이트가 48000 Hz."""
        create_test_video(e2e_video_dir / "clip_001.mov", duration=3.0)

        output_file = e2e_output_dir / "loudnorm_sr_check.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            normalize_audio=True,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        info = probe_video(result_path)
        audio_stream = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
        assert audio_stream is not None, "출력 파일에 오디오 스트림이 없음"
        actual_rate = int(audio_stream["sample_rate"])
        assert actual_rate == 48000, (
            f"loudnorm 후 샘플레이트가 {actual_rate}Hz — "
            "96kHz 업샘플링이 출력에 반영됨. -ar 48000 수정이 누락되었을 수 있음."
        )

    def test_loudnorm_two_clips_both_48000(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """2개 클립에 loudnorm 적용 후 병합 → 결과 파일의 샘플레이트가 48000 Hz."""
        create_test_video(e2e_video_dir / "clip_001.mov", duration=2.0)
        create_test_video(e2e_video_dir / "clip_002.mov", duration=2.0)

        output_file = e2e_output_dir / "loudnorm_merge_sr_check.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            normalize_audio=True,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        info = probe_video(result_path)
        audio_stream = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
        assert audio_stream is not None, "병합 출력 파일에 오디오 스트림이 없음"
        actual_rate = int(audio_stream["sample_rate"])
        assert actual_rate == 48000, (
            f"병합 후 샘플레이트가 {actual_rate}Hz — "
            "두 클립 중 하나라도 96kHz로 트랜스코딩되었을 가능성이 있음."
        )

    def test_without_loudnorm_output_sample_rate_is_48000(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--normalize-audio 없이도 출력 오디오 샘플레이트가 48000 Hz (프로파일 기본값)."""
        create_test_video(e2e_video_dir / "clip_001.mov", duration=3.0)

        output_file = e2e_output_dir / "no_loudnorm_sr_check.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            normalize_audio=False,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        info = probe_video(result_path)
        audio_stream = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
        assert audio_stream is not None
        actual_rate = int(audio_stream["sample_rate"])
        assert actual_rate == 48000
