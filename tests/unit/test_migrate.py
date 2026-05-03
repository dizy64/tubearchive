"""--export-db / --import-db 마이그레이션 커맨드 단위 테스트."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tubearchive.app.queries.migrate import (
    FORMAT_VERSION,
    _insert_all,
    _remap_paths,
    cmd_export_db,
    cmd_import_db,
)

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _make_db(path: Path) -> sqlite3.Connection:
    """스키마가 초기화된 SQLite 연결을 반환한다."""
    from tubearchive.infra.db.schema import SCHEMA

    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def _insert_video(conn: sqlite3.Connection, vid_id: int, original_path: str) -> None:
    conn.execute(
        "INSERT INTO videos (id, original_path, creation_time) VALUES (?, ?, ?)",
        (vid_id, original_path, "2026-01-01T00:00:00"),
    )
    conn.commit()


def _insert_merge_job(conn: sqlite3.Connection, job_id: int, output_path: str) -> None:
    conn.execute(
        "INSERT INTO merge_jobs (id, output_path, video_ids, status) VALUES (?, ?, ?, ?)",
        (job_id, output_path, "[1]", "completed"),
    )
    conn.commit()


def _insert_split_job(
    conn: sqlite3.Connection, job_id: int, merge_job_id: int, output_files: list[str]
) -> None:
    conn.execute(
        "INSERT INTO split_jobs (id, merge_job_id, split_criterion, split_value, output_files)"
        " VALUES (?, ?, ?, ?, ?)",
        (job_id, merge_job_id, "duration", "3600", json.dumps(output_files)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# export 테스트
# ---------------------------------------------------------------------------


def test_export_creates_json_with_format_header(tmp_path: Path) -> None:
    """export 결과 JSON에 format_version, exported_at, tubearchive_version 헤더가 있다."""
    import os

    db_path = tmp_path / "tubearchive.db"
    conn = _make_db(db_path)
    conn.close()

    os.environ["TUBEARCHIVE_DB_PATH"] = str(db_path)
    try:
        out = tmp_path / "export.json"
        cmd_export_db(out)
        data = json.loads(out.read_text())
    finally:
        del os.environ["TUBEARCHIVE_DB_PATH"]

    assert data["format_version"] == FORMAT_VERSION
    assert "exported_at" in data
    assert "tubearchive_version" in data
    assert "tables" in data


def test_export_includes_all_tables(tmp_path: Path) -> None:
    """export JSON에 _IMPORT_ORDER 테이블 키가 모두 포함된다."""
    import os

    from tubearchive.app.queries.migrate import _IMPORT_ORDER

    db_path = tmp_path / "tubearchive.db"
    conn = _make_db(db_path)
    conn.close()

    os.environ["TUBEARCHIVE_DB_PATH"] = str(db_path)
    try:
        out = tmp_path / "export.json"
        cmd_export_db(out)
        data = json.loads(out.read_text())
    finally:
        del os.environ["TUBEARCHIVE_DB_PATH"]

    for table in _IMPORT_ORDER:
        assert table in data["tables"]


def test_export_captures_inserted_rows(tmp_path: Path) -> None:
    """insert 한 videos 행이 export JSON에 포함된다."""
    import os

    db_path = tmp_path / "tubearchive.db"
    conn = _make_db(db_path)
    _insert_video(conn, 1, "/old/video.mp4")
    conn.close()

    os.environ["TUBEARCHIVE_DB_PATH"] = str(db_path)
    try:
        out = tmp_path / "export.json"
        cmd_export_db(out)
        data = json.loads(out.read_text())
    finally:
        del os.environ["TUBEARCHIVE_DB_PATH"]

    videos = data["tables"]["videos"]
    assert len(videos) == 1
    assert videos[0]["original_path"] == "/old/video.mp4"


def test_export_uses_atomic_write(tmp_path: Path) -> None:
    """export는 .tmp 임시 파일을 거쳐 원자적으로 교체한다 (tmp 파일이 남지 않음)."""
    import os

    db_path = tmp_path / "tubearchive.db"
    conn = _make_db(db_path)
    conn.close()

    os.environ["TUBEARCHIVE_DB_PATH"] = str(db_path)
    try:
        out = tmp_path / "export.json"
        cmd_export_db(out)
    finally:
        del os.environ["TUBEARCHIVE_DB_PATH"]

    assert out.exists()
    assert not (tmp_path / "export.tmp").exists()


def test_export_raises_if_db_missing(tmp_path: Path) -> None:
    """DB 파일이 없으면 FileNotFoundError를 발생시킨다."""
    import os

    os.environ["TUBEARCHIVE_DB_PATH"] = str(tmp_path / "nonexistent.db")
    try:
        with pytest.raises(FileNotFoundError):
            cmd_export_db(tmp_path / "out.json")
    finally:
        del os.environ["TUBEARCHIVE_DB_PATH"]


# ---------------------------------------------------------------------------
# import 테스트
# ---------------------------------------------------------------------------


def test_import_restores_data_round_trip(tmp_path: Path) -> None:
    """export → 새 DB에 import 하면 같은 데이터가 복원된다."""
    import os

    src_db = tmp_path / "src.db"
    conn = _make_db(src_db)
    _insert_video(conn, 1, "/videos/clip.mp4")
    conn.close()

    os.environ["TUBEARCHIVE_DB_PATH"] = str(src_db)
    out = tmp_path / "export.json"
    cmd_export_db(out)
    del os.environ["TUBEARCHIVE_DB_PATH"]

    # 빈 DB로 import
    dst_db = tmp_path / "dst.db"
    os.environ["TUBEARCHIVE_DB_PATH"] = str(dst_db)
    try:
        cmd_import_db(out)
    finally:
        del os.environ["TUBEARCHIVE_DB_PATH"]

    conn2 = sqlite3.connect(dst_db)
    rows = conn2.execute("SELECT original_path FROM videos").fetchall()
    conn2.close()
    assert rows == [("/videos/clip.mp4",)]


def test_import_skips_existing_by_default(tmp_path: Path) -> None:
    """overwrite=False(기본)이면 충돌 행을 skip하고 기존 값이 유지된다."""
    import os

    src_db = tmp_path / "src.db"
    conn = _make_db(src_db)
    _insert_video(conn, 1, "/old/video.mp4")
    conn.close()

    os.environ["TUBEARCHIVE_DB_PATH"] = str(src_db)
    out = tmp_path / "export.json"
    cmd_export_db(out)
    del os.environ["TUBEARCHIVE_DB_PATH"]

    # 같은 id=1 이지만 다른 경로로 먼저 삽입
    dst_db = tmp_path / "dst.db"
    conn2 = _make_db(dst_db)
    _insert_video(conn2, 1, "/kept/video.mp4")
    conn2.close()

    os.environ["TUBEARCHIVE_DB_PATH"] = str(dst_db)
    try:
        cmd_import_db(out, overwrite=False)
    finally:
        del os.environ["TUBEARCHIVE_DB_PATH"]

    conn3 = sqlite3.connect(dst_db)
    row = conn3.execute("SELECT original_path FROM videos WHERE id=1").fetchone()
    conn3.close()
    assert row == ("/kept/video.mp4",)


def test_import_overwrite_replaces_existing(tmp_path: Path) -> None:
    """overwrite=True이면 충돌 행을 덮어쓴다."""
    import os

    src_db = tmp_path / "src.db"
    conn = _make_db(src_db)
    _insert_video(conn, 1, "/new/video.mp4")
    conn.close()

    os.environ["TUBEARCHIVE_DB_PATH"] = str(src_db)
    out = tmp_path / "export.json"
    cmd_export_db(out)
    del os.environ["TUBEARCHIVE_DB_PATH"]

    dst_db = tmp_path / "dst.db"
    conn2 = _make_db(dst_db)
    _insert_video(conn2, 1, "/old/video.mp4")
    conn2.close()

    os.environ["TUBEARCHIVE_DB_PATH"] = str(dst_db)
    try:
        cmd_import_db(out, overwrite=True)
    finally:
        del os.environ["TUBEARCHIVE_DB_PATH"]

    conn3 = sqlite3.connect(dst_db)
    row = conn3.execute("SELECT original_path FROM videos WHERE id=1").fetchone()
    conn3.close()
    assert row == ("/new/video.mp4",)


def test_import_raises_if_json_missing(tmp_path: Path) -> None:
    """JSON 파일이 없으면 FileNotFoundError를 발생시킨다."""
    with pytest.raises(FileNotFoundError):
        cmd_import_db(tmp_path / "nonexistent.json")


def test_import_raises_on_wrong_format_version(tmp_path: Path) -> None:
    """format_version이 다르면 ValueError를 발생시킨다."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text(
        json.dumps({"format_version": 99, "tables": {}}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="format_version"):
        cmd_import_db(bad_json)


def test_import_raises_on_missing_tables_key(tmp_path: Path) -> None:
    """'tables' 키가 없으면 ValueError를 발생시킨다."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text(
        json.dumps({"format_version": FORMAT_VERSION}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="tables"):
        cmd_import_db(bad_json)


# ---------------------------------------------------------------------------
# 경로 remapping 테스트
# ---------------------------------------------------------------------------


def test_remap_paths_simple_column(tmp_path: Path) -> None:
    """videos.original_path에서 src 접두사가 dst로 치환된다."""
    tables = {
        "videos": [{"id": 1, "original_path": "/old/clip.mp4", "creation_time": "2026-01-01"}]
    }
    result = _remap_paths(tables, "/old", "/new")
    assert result["videos"][0]["original_path"] == "/new/clip.mp4"


def test_remap_paths_none_value_skipped(tmp_path: Path) -> None:
    """경로 컬럼 값이 None이면 치환하지 않고 그대로 유지한다."""
    tables: dict[str, list[dict[str, object]]] = {
        "transcoding_jobs": [{"id": 1, "video_id": 1, "temp_file_path": None}]
    }
    result = _remap_paths(tables, "/old", "/new")
    assert result["transcoding_jobs"][0]["temp_file_path"] is None


def test_remap_paths_split_jobs_output_files(tmp_path: Path) -> None:
    """split_jobs.output_files JSON 배열의 각 경로 항목이 치환된다."""
    files = ["/old/part1.mp4", "/old/part2.mp4"]
    tables = {
        "split_jobs": [
            {
                "id": 1,
                "merge_job_id": 1,
                "split_criterion": "duration",
                "split_value": "3600",
                "output_files": json.dumps(files),
            }
        ]
    }
    result = _remap_paths(tables, "/old", "/new")
    remapped = json.loads(str(result["split_jobs"][0]["output_files"]))
    assert remapped == ["/new/part1.mp4", "/new/part2.mp4"]


def test_remap_paths_non_path_table_unchanged(tmp_path: Path) -> None:
    """경로 컬럼이 없는 테이블은 변경 없이 그대로 반환된다."""
    tables = {
        "projects": [{"id": 1, "name": "제주도", "description": None}]
    }
    result = _remap_paths(tables, "/old", "/new")
    assert result["projects"] == tables["projects"]


def test_remap_paths_no_match_unchanged(tmp_path: Path) -> None:
    """경로가 src 접두사로 시작하지 않으면 변경되지 않는다."""
    tables = {
        "videos": [{"id": 1, "original_path": "/other/clip.mp4", "creation_time": "2026-01-01"}]
    }
    result = _remap_paths(tables, "/old", "/new")
    assert result["videos"][0]["original_path"] == "/other/clip.mp4"


def test_import_with_path_remapping(tmp_path: Path) -> None:
    """--src-prefix / --dst-prefix가 실제 import 경로에 반영된다."""
    import os

    src_db = tmp_path / "src.db"
    conn = _make_db(src_db)
    _insert_video(conn, 1, "/old/machine/clip.mp4")
    conn.close()

    os.environ["TUBEARCHIVE_DB_PATH"] = str(src_db)
    out = tmp_path / "export.json"
    cmd_export_db(out)
    del os.environ["TUBEARCHIVE_DB_PATH"]

    dst_db = tmp_path / "dst.db"
    os.environ["TUBEARCHIVE_DB_PATH"] = str(dst_db)
    try:
        cmd_import_db(out, src_prefix="/old/machine", dst_prefix="/new/machine")
    finally:
        del os.environ["TUBEARCHIVE_DB_PATH"]

    conn2 = sqlite3.connect(dst_db)
    row = conn2.execute("SELECT original_path FROM videos WHERE id=1").fetchone()
    conn2.close()
    assert row == ("/new/machine/clip.mp4",)


# ---------------------------------------------------------------------------
# _insert_all 단위 테스트
# ---------------------------------------------------------------------------


def test_insert_all_returns_stats(tmp_path: Path) -> None:
    """삽입 건수가 stats 딕셔너리로 반환된다."""
    db_path = tmp_path / "test.db"
    conn = _make_db(db_path)

    tables = {
        "videos": [
            {"id": 1, "original_path": "/a.mp4", "creation_time": "2026-01-01"},
            {"id": 2, "original_path": "/b.mp4", "creation_time": "2026-01-01"},
        ]
    }
    stats = _insert_all(conn, tables, overwrite=False)
    conn.commit()
    conn.close()

    assert stats["videos"] == 2


def test_insert_all_foreign_keys_off_allows_fk_violation(tmp_path: Path) -> None:
    """_insert_all 중 FK OFF이므로 FK 위반 행도 삽입된다."""
    db_path = tmp_path / "test.db"
    conn = _make_db(db_path)

    tables = {
        "transcoding_jobs": [
            {
                "id": 1,
                "video_id": 999,  # 존재하지 않는 video_id
                "status": "completed",
                "progress_percent": 100,
            }
        ]
    }
    stats = _insert_all(conn, tables, overwrite=False)
    conn.commit()

    row = conn.execute("SELECT id FROM transcoding_jobs WHERE id=1").fetchone()
    conn.close()

    assert stats["transcoding_jobs"] == 1
    assert row is not None
