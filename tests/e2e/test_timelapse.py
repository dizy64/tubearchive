"""타임랩스 생성 E2E 테스트.

파이프라인(트랜스코딩+병합) 이후 TimelapseGenerator를 실행하여
배속, 오디오 유지, 해상도 변환을 검증한다.
"""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import run_pipeline
from tubearchive.core.timelapse import TimelapseGenerator

from .conftest import (
    create_test_video,
    get_audio_stream_count,
    get_video_duration,
    make_pipeline_args,
    probe_video,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard3,
]


class TestTimelapse:
    """타임랩스 생성 E2E 테스트."""

    def test_timelapse_basic(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """10초 영상 → 파이프라인 → 10x 타임랩스 → 약 1초 출력."""
        create_test_video(e2e_video_dir / "clip.mov", duration=10.0)

        output_file = e2e_output_dir / "merged.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
        )
        merged = run_pipeline(args)

        timelapse_out = e2e_output_dir / "timelapse.mp4"
        generator = TimelapseGenerator()
        generator.generate(
            input_path=merged,
            output_path=timelapse_out,
            speed=10,
            keep_audio=False,
        )

        assert timelapse_out.exists()
        duration = get_video_duration(timelapse_out)
        # 10초 / 10배속 ≈ 1초 (±0.5초 허용)
        assert abs(duration - 1.0) < 0.5

    def test_timelapse_with_audio(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """6초 영상 → 파이프라인 → 2x 타임랩스(오디오 유지) → 오디오 스트림 존재."""
        create_test_video(e2e_video_dir / "clip.mov", duration=6.0, audio=True)

        output_file = e2e_output_dir / "merged.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
        )
        merged = run_pipeline(args)

        timelapse_out = e2e_output_dir / "timelapse_audio.mp4"
        generator = TimelapseGenerator()
        generator.generate(
            input_path=merged,
            output_path=timelapse_out,
            speed=2,
            keep_audio=True,
        )

        assert timelapse_out.exists()
        assert get_audio_stream_count(timelapse_out) >= 1

    def test_timelapse_resolution(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """5초 영상 → 파이프라인 → 5x 타임랩스(720p) → 1280x720 출력."""
        create_test_video(
            e2e_video_dir / "clip.mov",
            duration=5.0,
            width=1920,
            height=1080,
        )

        output_file = e2e_output_dir / "merged.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
        )
        merged = run_pipeline(args)

        timelapse_out = e2e_output_dir / "timelapse_720p.mp4"
        generator = TimelapseGenerator()
        generator.generate(
            input_path=merged,
            output_path=timelapse_out,
            speed=5,
            resolution="720p",
        )

        assert timelapse_out.exists()
        info = probe_video(timelapse_out)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert int(video_stream["width"]) == 1280
        assert int(video_stream["height"]) == 720
