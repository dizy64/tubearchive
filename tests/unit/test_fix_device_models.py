"""cmd_fix_device_models 단위 테스트."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from tubearchive.domain.models.video import VideoFile, VideoMetadata
from tubearchive.infra.db.repository import VideoRepository
from tubearchive.infra.db.schema import init_database


def _null_meta(path: Path) -> tuple[VideoFile, VideoMetadata]:
    """device_model=None 인 VideoFile/VideoMetadata 쌍 생성."""
    return VideoFile(path=path, creation_time=datetime(2024, 1, 1), size_bytes=1), VideoMetadata(
        width=3840,
        height=2160,
        duration_seconds=30.0,
        fps=60.0,
        codec="h264",
        pixel_format="yuv420p",
        is_portrait=False,
        is_vfr=False,
        device_model=None,
        color_space=None,
        color_transfer=None,
        color_primaries=None,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    conn = init_database(db)
    conn.close()
    return db


class TestCmdFixDeviceModels:
    """cmd_fix_device_models 동작 검증."""

    def _insert_null(self, conn: sqlite3.Connection, path: Path) -> int:
        video_file, meta = _null_meta(path)
        return VideoRepository(conn).insert(video_file, meta)

    def test_no_records_prints_empty_message(
        self, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """NULL 레코드가 없으면 안내 메시지만 출력."""
        with patch("tubearchive.app.cli.main.database_session") as mock_session:
            conn = init_database(db_path)
            mock_session.return_value.__enter__ = lambda s: conn
            mock_session.return_value.__exit__ = lambda s, *a: conn.close()

            from tubearchive.app.cli.main import cmd_fix_device_models

            cmd_fix_device_models()

        captured = capsys.readouterr()
        assert "없습니다" in captured.out

    def test_updates_detected_model(
        self, tmp_path: Path, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """ffprobe 결과로 모델 감지되면 DB를 갱신한다."""
        gopro_path = tmp_path / "GH010001.MP4"
        gopro_path.write_text("")

        conn = init_database(db_path)
        video_id = self._insert_null(conn, gopro_path)
        conn.close()

        probe_result = {
            "streams": [{"codec_type": "video", "tags": {"handler_name": "GoPro AVC"}}],
            "format": {"tags": {"firmware": "HD9.01.01.72.00"}},
        }

        call_count = [0]

        def mock_session():
            import contextlib

            @contextlib.contextmanager
            def _ctx():
                c = sqlite3.connect(str(db_path))
                c.row_factory = sqlite3.Row
                try:
                    yield c
                finally:
                    c.close()

            call_count[0] += 1
            return _ctx()

        with (
            patch("tubearchive.app.cli.main.database_session", mock_session),
            patch("tubearchive.app.cli.main.Path", wraps=Path),
            patch("tubearchive.domain.media.detector._run_ffprobe", return_value=probe_result),
        ):
            from tubearchive.app.cli.main import cmd_fix_device_models

            cmd_fix_device_models()

        captured = capsys.readouterr()
        assert "1개 갱신" in captured.out

        conn2 = sqlite3.connect(str(db_path))
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT device_model FROM videos WHERE id = ?", (video_id,)).fetchone()
        conn2.close()
        assert row["device_model"] == "GoPro HERO 9"

    def test_skips_missing_file(
        self, tmp_path: Path, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """파일이 존재하지 않으면 건너뛴다."""
        missing_path = tmp_path / "nonexistent.MP4"
        missing_path.write_text("")  # VideoFile 생성을 위해 임시 생성 후 삭제

        conn = init_database(db_path)
        self._insert_null(conn, missing_path)
        conn.close()

        missing_path.unlink()  # 파일 삭제 — cmd_fix_device_models에서 건너뜀 대상

        def mock_session():
            import contextlib

            @contextlib.contextmanager
            def _ctx():
                c = sqlite3.connect(str(db_path))
                c.row_factory = sqlite3.Row
                try:
                    yield c
                finally:
                    c.close()

            return _ctx()

        with patch("tubearchive.app.cli.main.database_session", mock_session):
            from tubearchive.app.cli.main import cmd_fix_device_models

            cmd_fix_device_models()

        captured = capsys.readouterr()
        assert "1개 파일 없음" in captured.out
        assert "0개 갱신" in captured.out

    def test_skips_undetectable_model(
        self, tmp_path: Path, db_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """모델 감지 불가 파일은 건너뛴다."""
        unknown_path = tmp_path / "unknown_camera.mp4"
        unknown_path.write_text("")

        conn = init_database(db_path)
        self._insert_null(conn, unknown_path)
        conn.close()

        probe_result = {
            "streams": [{"codec_type": "video", "tags": {}}],
            "format": {"tags": {}},
        }

        def mock_session():
            import contextlib

            @contextlib.contextmanager
            def _ctx():
                c = sqlite3.connect(str(db_path))
                c.row_factory = sqlite3.Row
                try:
                    yield c
                finally:
                    c.close()

            return _ctx()

        with (
            patch("tubearchive.app.cli.main.database_session", mock_session),
            patch("tubearchive.domain.media.detector._run_ffprobe", return_value=probe_result),
        ):
            from tubearchive.app.cli.main import cmd_fix_device_models

            cmd_fix_device_models()

        captured = capsys.readouterr()
        assert "0개 갱신" in captured.out
        assert "1개 모델 감지 불가" in captured.out
