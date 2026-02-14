"""썸네일 추출 E2E 테스트.

파이프라인(트랜스코딩+병합) 이후 extract_thumbnails를 실행하여
기본 지점 및 커스텀 타임스탬프 썸네일 생성을 검증한다.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tubearchive.cli import run_pipeline
from tubearchive.ffmpeg.thumbnail import extract_thumbnails, prepare_thumbnail_for_youtube

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

    def test_prepare_thumbnail_for_youtube_keeps_valid_image(
        self,
        e2e_output_dir: Path,
    ) -> None:
        """규격 준수 이미지(1280x720)는 원본 경로 그대로 반환된다."""
        thumbnail = e2e_output_dir / "valid.jpg"
        _create_test_image(thumbnail, 1280, 720)

        prepared = prepare_thumbnail_for_youtube(thumbnail)

        assert prepared == thumbnail

    def test_prepare_thumbnail_for_youtube_scales_small_image(
        self,
        e2e_output_dir: Path,
    ) -> None:
        """작은 썸네일은 YouTube 규격에 맞게 리사이즈되어 _youtube 파일이 생성된다."""
        source = e2e_output_dir / "small.jpg"
        _create_test_image(source, 640, 360)

        prepared = prepare_thumbnail_for_youtube(source)

        assert prepared != source
        assert prepared.name == "small_youtube.jpg"
        assert prepared.exists()

        width, height = _probe_image_size(prepared)
        assert width >= 1280
        assert height >= 720


def _create_test_image(path: Path, width: int, height: int) -> None:
    """ffmpeg로 테스트용 정적 이미지를 생성한다."""
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=blue:s={width}x{height}:d=1",
            "-frames:v",
            "1",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


def _probe_image_size(path: Path) -> tuple[int, int]:
    """ffprobe로 이미지의 가로/세로를 읽는다."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    payload = json.loads(result.stdout or "{}")
    stream = payload["streams"][0]
    return int(stream["width"]), int(stream["height"])
