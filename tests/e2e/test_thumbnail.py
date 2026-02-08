"""썸네일 추출 E2E 테스트.

파이프라인(트랜스코딩+병합) 이후 extract_thumbnails를 실행하여
기본 지점 및 커스텀 타임스탬프 썸네일 생성을 검증한다.
"""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import run_pipeline
from tubearchive.ffmpeg.thumbnail import extract_thumbnails

from .conftest import (
    create_test_video,
    make_pipeline_args,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard3,
]


class TestThumbnail:
    """썸네일 추출 E2E 테스트."""

    def test_thumbnail_default(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """10초 영상 → 파이프라인 → 기본 썸네일(10%, 33%, 50%) → 3개 JPEG."""
        create_test_video(e2e_video_dir / "clip.mov", duration=10.0)

        output_file = e2e_output_dir / "merged.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
        )
        merged = run_pipeline(args)

        thumbnails = extract_thumbnails(
            video_path=merged,
            timestamps=None,
        )

        assert len(thumbnails) == 3
        for thumb in thumbnails:
            assert thumb.exists()
            assert thumb.suffix == ".jpg"
            assert thumb.stat().st_size > 0

    def test_thumbnail_custom_timestamp(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """10초 영상 → 파이프라인 → 커스텀 시점(2.0초) 썸네일 → 1개 JPEG."""
        create_test_video(e2e_video_dir / "clip.mov", duration=10.0)

        output_file = e2e_output_dir / "merged.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
        )
        merged = run_pipeline(args)

        thumbnails = extract_thumbnails(
            video_path=merged,
            timestamps=[2.0],
        )

        assert len(thumbnails) == 1
        assert thumbnails[0].exists()
        assert thumbnails[0].suffix == ".jpg"
        assert thumbnails[0].stat().st_size > 0
