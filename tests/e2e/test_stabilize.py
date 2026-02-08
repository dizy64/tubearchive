"""영상 안정화(vidstab) E2E 테스트.

vidstab 필터를 사용한 2-pass 영상 안정화 파이프라인을 검증한다.
vidstab 미설치 시 모듈 전체 스킵.

실행:
    uv run pytest tests/e2e/test_stabilize.py -v
"""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import run_pipeline

from .conftest import (
    create_test_video,
    has_vidstab_filter,
    make_pipeline_args,
    probe_video,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.skipif(not has_vidstab_filter(), reason="vidstab filter not available in ffmpeg"),
    pytest.mark.e2e_shard3,
]


class TestStabilization:
    """영상 안정화 E2E 테스트."""

    def test_stabilize_pipeline(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """stabilize=True → 2-pass 완료, 출력 파일 존재."""
        create_test_video(e2e_video_dir / "clip.mov", duration=3.0)

        args = make_pipeline_args(
            targets=[e2e_video_dir],
            output=e2e_output_dir / "stabilized.mp4",
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            stabilize=True,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0

    def test_stabilize_strength_heavy(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """stabilize_strength='heavy' → 출력 파일 존재."""
        create_test_video(e2e_video_dir / "clip.mov", duration=3.0)

        args = make_pipeline_args(
            targets=[e2e_video_dir],
            output=e2e_output_dir / "stabilized_heavy.mp4",
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            stabilize=True,
            stabilize_strength="heavy",
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0

    def test_stabilize_crop_expand(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """stabilize_crop='expand' → 출력 해상도 3840x2160 유지."""
        create_test_video(e2e_video_dir / "clip.mov", duration=3.0)

        args = make_pipeline_args(
            targets=[e2e_video_dir],
            output=e2e_output_dir / "stabilized_expand.mp4",
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            stabilize=True,
            stabilize_crop="expand",
        )

        result_path = run_pipeline(args)

        assert result_path.exists()

        info = probe_video(result_path)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert int(video_stream["width"]) == 3840
        assert int(video_stream["height"]) == 2160
