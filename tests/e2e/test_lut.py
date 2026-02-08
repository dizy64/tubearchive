"""LUT 컬러 그레이딩 E2E 테스트.

Identity LUT 적용 및 잘못된 LUT 경로 처리를 검증한다.

실행:
    uv run pytest tests/e2e/test_lut.py -v
"""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import run_pipeline

from .conftest import (
    create_identity_lut,
    create_test_video,
    make_pipeline_args,
    probe_video,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard3,
]


class TestLUT:
    """LUT 컬러 그레이딩 E2E 테스트."""

    def test_lut_identity_cube(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Identity LUT 적용 → 파이프라인 성공, 출력 파일 존재."""
        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)
        lut_file = e2e_output_dir / "identity.cube"
        create_identity_lut(lut_file)

        args = make_pipeline_args(
            targets=[e2e_video_dir],
            output=e2e_output_dir / "lut_output.mp4",
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            lut_path=lut_file,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0

        info = probe_video(result_path)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert video_stream["codec_name"] == "hevc"

    def test_lut_invalid_path(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """존재하지 않는 LUT 경로 → 적절한 에러 처리."""
        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)

        args = make_pipeline_args(
            targets=[e2e_video_dir],
            output=e2e_output_dir / "lut_invalid.mp4",
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            lut_path=Path("/nonexistent/fake.cube"),
        )

        with pytest.raises((FileNotFoundError, RuntimeError)):
            run_pipeline(args)
