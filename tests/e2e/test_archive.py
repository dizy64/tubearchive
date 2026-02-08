"""원본 파일 아카이브 E2E 테스트.

아카이브 정책(MOVE/KEEP)에 따른 원본 파일 처리를 검증한다.

실행:
    uv run pytest tests/e2e/test_archive.py -v
"""

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from tubearchive.core.archiver import ArchivePolicy, Archiver
from tubearchive.database.repository import ArchiveHistoryRepository, VideoRepository
from tubearchive.database.schema import init_database
from tubearchive.models.video import VideoFile, VideoMetadata

from .conftest import create_test_video

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard3,
]


def _register_video(conn: sqlite3.Connection, video_path: Path) -> int:
    """DB에 비디오 레코드를 등록하고 video_id를 반환."""
    repo = VideoRepository(conn)
    video = VideoFile(
        path=video_path,
        size_bytes=video_path.stat().st_size,
        creation_time=datetime.now(),
    )
    metadata = VideoMetadata(
        width=1920,
        height=1080,
        duration_seconds=2.0,
        fps=30.0,
        codec="h264",
        pixel_format="yuv420p",
        is_portrait=False,
        is_vfr=False,
        device_model=None,
        color_space="bt709",
        color_transfer="bt709",
        color_primaries="bt709",
    )
    return repo.insert(video, metadata)


class TestArchive:
    """원본 파일 아카이브 E2E 테스트."""

    def test_archive_move_policy(self, tmp_path: Path) -> None:
        """MOVE 정책 → 원본 파일이 destination으로 이동."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        dest_dir = tmp_path / "archive"
        dest_dir.mkdir()

        video_file = create_test_video(source_dir / "clip.mov", duration=2.0)
        assert video_file.exists()

        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # FK constraint를 위해 videos 테이블에 등록
        video_id = _register_video(conn, video_file)

        repo = ArchiveHistoryRepository(conn)
        archiver = Archiver(
            repo=repo,
            policy=ArchivePolicy.MOVE,
            destination=dest_dir,
        )

        stats = archiver.archive_files([(video_id, video_file)])
        conn.close()

        # 원본 위치에서 사라짐
        assert not video_file.exists()
        # destination에 이동됨
        moved_file = dest_dir / "clip.mov"
        assert moved_file.exists()
        assert moved_file.stat().st_size > 0
        assert stats.moved == 1

    def test_archive_keep_policy(self, tmp_path: Path) -> None:
        """KEEP 정책 → 원본 파일 그대로 유지."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()

        video_file = create_test_video(source_dir / "clip.mov", duration=2.0)
        original_size = video_file.stat().st_size
        assert video_file.exists()

        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        video_id = _register_video(conn, video_file)

        repo = ArchiveHistoryRepository(conn)
        archiver = Archiver(
            repo=repo,
            policy=ArchivePolicy.KEEP,
        )

        stats = archiver.archive_files([(video_id, video_file)])
        conn.close()

        # 원본 파일 그대로
        assert video_file.exists()
        assert video_file.stat().st_size == original_size
        assert stats.kept == 1
        assert stats.moved == 0
        assert stats.deleted == 0
