"""
오디오 처리 E2E 테스트.

실제 ffmpeg를 사용하여 loudnorm, denoise, 오디오 없는 영상 파이프라인을 검증한다.

실행:
    uv run pytest tests/e2e/test_audio_processing.py -v
"""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import run_pipeline

from .conftest import (
    create_no_audio_video,
    create_silent_video,
    create_test_video,
    get_audio_stream_count,
    make_pipeline_args,
    probe_video,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard2,
]


class TestLoudnorm:
    """EBU R128 loudnorm 정규화 E2E 테스트."""

    def test_normalize_audio_pipeline(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """오디오가 있는 영상에 loudnorm 적용 시 출력 파일이 생성되고 오디오 스트림이 존재."""
        create_test_video(e2e_video_dir / "clip_001.mov", duration=3.0)

        output_file = e2e_output_dir / "loudnorm_output.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            normalize_audio=True,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0

        # 오디오 스트림이 유지되어야 함
        assert get_audio_stream_count(result_path) >= 1

        # HEVC 출력 확인
        info = probe_video(result_path)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert video_stream["codec_name"] == "hevc"

    def test_normalize_audio_silent_video(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """완전 무음 오디오 영상에 loudnorm 적용 시 크래시 없이 처리."""
        create_silent_video(e2e_video_dir / "silent_001.mov", duration=3.0)

        output_file = e2e_output_dir / "silent_loudnorm.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            normalize_audio=True,
        )

        # loudnorm -inf 처리: 크래시 없이 완료되어야 함
        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0


class TestDenoise:
    """오디오 노이즈 제거 E2E 테스트."""

    def test_denoise_pipeline(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """denoise 활성화 시 출력 파일이 정상 생성."""
        create_test_video(e2e_video_dir / "clip_001.mov", duration=3.0)

        output_file = e2e_output_dir / "denoise_output.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            denoise=True,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0


class TestNoAudioVideo:
    """오디오 없는 영상 처리 E2E 테스트."""

    def test_no_audio_video_pipeline(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """오디오 트랙이 없는 영상도 크래시 없이 트랜스코딩+병합 성공."""
        create_no_audio_video(e2e_video_dir / "noaudio_001.mov", duration=2.0)

        output_file = e2e_output_dir / "noaudio_output.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0

    def test_no_audio_mixed_with_audio(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """오디오 있는 영상과 없는 영상을 혼합 병합 시 성공."""
        create_test_video(e2e_video_dir / "with_audio_001.mov", duration=2.0)
        create_no_audio_video(e2e_video_dir / "no_audio_002.mov", duration=2.0)

        output_file = e2e_output_dir / "mixed_audio_output.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0
