"""
BGM 믹싱 E2E 테스트.

실제 ffmpeg를 사용하여 영상에 BGM을 믹싱하는 파이프라인을 검증한다.

실행:
    uv run pytest tests/e2e/test_bgm.py -v
"""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import run_pipeline

from .conftest import (
    create_bgm_audio,
    create_no_audio_video,
    create_test_video,
    get_audio_stream_count,
    get_video_duration,
    make_pipeline_args,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard2,
]


class TestBGMMixing:
    """BGM 믹싱 파이프라인 E2E 테스트."""

    def test_bgm_shorter_than_video(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BGM이 영상보다 짧을 때: BGM 길이만큼 믹싱, 출력 길이는 영상 길이 유지."""
        video = create_test_video(e2e_video_dir / "clip.mov", duration=5.0)
        bgm = create_bgm_audio(e2e_output_dir / "bgm_short.mp3", duration=2.0)

        output = e2e_output_dir / "bgm_short_output.mp4"
        args = make_pipeline_args(
            [video],
            output,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            bgm_path=bgm,
            bgm_volume=0.2,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert get_audio_stream_count(result_path) >= 1
        duration = get_video_duration(result_path)
        assert abs(duration - 5.0) < 1.0, f"Expected ~5s, got {duration:.2f}s"

    def test_bgm_longer_than_video(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BGM이 영상보다 길 때: BGM이 영상 길이에 맞춰 트리밍."""
        video = create_test_video(e2e_video_dir / "clip.mov", duration=3.0)
        bgm = create_bgm_audio(e2e_output_dir / "bgm_long.mp3", duration=8.0)

        output = e2e_output_dir / "bgm_long_output.mp4"
        args = make_pipeline_args(
            [video],
            output,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            bgm_path=bgm,
            bgm_volume=0.2,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        duration = get_video_duration(result_path)
        assert abs(duration - 3.0) < 1.0, f"Expected ~3s, got {duration:.2f}s"

    def test_bgm_loop(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """BGM 루프: 짧은 BGM이 영상 길이만큼 반복 재생."""
        video = create_test_video(e2e_video_dir / "clip.mov", duration=5.0)
        bgm = create_bgm_audio(e2e_output_dir / "bgm_loop.mp3", duration=2.0)

        output = e2e_output_dir / "bgm_loop_output.mp4"
        args = make_pipeline_args(
            [video],
            output,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            bgm_path=bgm,
            bgm_volume=0.2,
            bgm_loop=True,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        duration = get_video_duration(result_path)
        assert abs(duration - 5.0) < 1.0, f"Expected ~5s, got {duration:.2f}s"

    def test_bgm_on_no_audio_video(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """오디오 없는 영상에 BGM 믹싱: BGM만 오디오 트랙으로 포함."""
        video = create_no_audio_video(e2e_video_dir / "no_audio.mov", duration=3.0)
        bgm = create_bgm_audio(e2e_output_dir / "bgm_only.mp3", duration=3.0)

        output = e2e_output_dir / "bgm_noaudio_output.mp4"
        args = make_pipeline_args(
            [video],
            output,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            bgm_path=bgm,
            bgm_volume=0.2,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert get_audio_stream_count(result_path) >= 1
