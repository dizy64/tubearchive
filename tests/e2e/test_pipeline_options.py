"""
파이프라인 옵션 E2E 테스트.

dry-run, parallel 등 파이프라인 옵션의 동작을 실제 ffmpeg로 검증한다.

실행:
    uv run pytest tests/e2e/test_pipeline_options.py -v
"""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import _cmd_dry_run, run_pipeline

from .conftest import create_test_video, make_pipeline_args, probe_video

# ffmpeg 없으면 전체 모듈 스킵
pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard1,
]


class TestDryRun:
    """dry-run 옵션 테스트."""

    def test_dry_run_no_output(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dry_run=True → _cmd_dry_run 호출 시 출력 파일 미생성."""
        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)

        output_file = e2e_output_dir / "dry_run_output.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            dry_run=True,
        )

        # dry_run은 main()에서 _cmd_dry_run을 호출하고 run_pipeline을 스킵함
        _cmd_dry_run(args)

        assert not output_file.exists(), "dry-run에서는 출력 파일이 생성되면 안 됨"


class TestParallel:
    """parallel 옵션 테스트."""

    def test_parallel_transcoding(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """parallel=2로 2개 영상을 병렬 트랜스코딩 후 병합."""
        create_test_video(e2e_video_dir / "clip_001.mov", duration=2.0)
        create_test_video(e2e_video_dir / "clip_002.mov", duration=2.0)

        output_file = e2e_output_dir / "parallel_output.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            parallel=2,
        )

        result_path = run_pipeline(args)

        assert result_path.exists(), "병렬 트랜스코딩 결과 파일이 존재해야 함"
        assert result_path.stat().st_size > 0

        info = probe_video(result_path)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert video_stream["codec_name"] == "hevc"


class TestWatermark:
    """워터마크 옵션 테스트."""

    def test_pipeline_with_watermark(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """워터마크 옵션으로 run_pipeline 실행."""
        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)

        output_file = e2e_output_dir / "watermark_output.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            watermark=True,
            watermark_pos="top-left",
            watermark_size=36,
            watermark_color="yellow",
            watermark_alpha=0.7,
        )

        result_path = run_pipeline(args)

        assert result_path == output_file
        assert result_path.exists()
        assert result_path.stat().st_size > 0
