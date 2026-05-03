"""DB 내보내기 / 가져오기 커맨드.

``--export-db`` : DB 전체를 JSON 파일로 직렬화한다.
``--import-db`` : JSON 파일에서 DB로 복원한다. ``--src-prefix`` / ``--dst-prefix``로
경로를 remapping 할 수 있고, ``--overwrite`` 로 충돌 레코드를 덮어쓸 수 있다.

JSON 포맷 ::

    {
      "format_version": 1,
      "exported_at": "2026-05-04T12:00:00",
      "tubearchive_version": "...",
      "tables": {
        "videos": [...],
        "transcoding_jobs": [...],
        ...
      }
    }

import 순서: FK 의존성을 고려해 projects → videos → transcoding_jobs
→ merge_jobs → split_jobs → archive_history → backup_history
→ project_merge_jobs 순으로 삽입한다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# (table_name, [path_column, ...]) — 경로 remapping 대상 컬럼
_PATH_COLUMNS: list[tuple[str, list[str]]] = [
    ("videos", ["original_path"]),
    ("transcoding_jobs", ["temp_file_path"]),
    ("merge_jobs", ["output_path"]),
    ("split_jobs", ["output_files"]),  # JSON 배열 → 아이템별 remapping
    ("archive_history", ["original_path", "destination_path"]),
    ("backup_history", ["source_path"]),
]

# FK 의존성 기준 삽입 순서 (import 시)
_IMPORT_ORDER = [
    "projects",
    "videos",
    "transcoding_jobs",
    "merge_jobs",
    "split_jobs",
    "archive_history",
    "backup_history",
    "project_merge_jobs",
]

FORMAT_VERSION = 1


# ---------------------------------------------------------------------------
# 내보내기
# ---------------------------------------------------------------------------


def cmd_export_db(output_path: Path) -> None:
    """DB 전체를 ``output_path`` JSON 파일로 내보낸다."""
    from tubearchive import __version__
    from tubearchive.infra.db.schema import get_default_db_path

    db_path = get_default_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"DB 파일이 없습니다: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        tables = _dump_all_tables(conn)
    finally:
        conn.close()

    payload = {
        "format_version": FORMAT_VERSION,
        "exported_at": datetime.now(tz=UTC).isoformat(),
        "tubearchive_version": __version__,
        "tables": tables,
    }

    output_path = Path(output_path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(output_path)

    total = sum(len(rows) for rows in tables.values())
    print(f"내보내기 완료: {output_path}")
    for name, rows in tables.items():
        if rows:
            print(f"  {name}: {len(rows)}건")
    print(f"  합계: {total}건")


def _dump_all_tables(conn: sqlite3.Connection) -> dict[str, list[dict[str, object]]]:
    """모든 테이블을 dict 목록으로 읽는다."""
    tables: dict[str, list[dict[str, object]]] = {}
    for table in _IMPORT_ORDER:
        try:
            cursor = conn.execute(f"SELECT * FROM {table}")
            tables[table] = [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            tables[table] = []
    return tables


# ---------------------------------------------------------------------------
# 가져오기
# ---------------------------------------------------------------------------


def cmd_import_db(
    input_path: Path,
    src_prefix: str | None = None,
    dst_prefix: str | None = None,
    overwrite: bool = False,
) -> None:
    """``input_path`` JSON 파일에서 DB로 데이터를 복원한다.

    Args:
        input_path: 내보낸 JSON 파일 경로.
        src_prefix: 경로 remapping 원본 접두사 (예: ``/Users/old``).
        dst_prefix: 경로 remapping 대상 접두사 (예: ``/Users/new``).
        overwrite: True이면 충돌 레코드를 덮어쓴다. False이면 skip.
    """
    input_path = Path(input_path).expanduser()
    if not input_path.exists():
        raise FileNotFoundError(f"JSON 파일이 없습니다: {input_path}")

    raw = json.loads(input_path.read_text(encoding="utf-8"))
    _validate_format(raw)

    tables: dict[str, list[dict[str, object]]] = raw["tables"]

    if src_prefix and dst_prefix:
        tables = _remap_paths(tables, src_prefix, dst_prefix)

    from tubearchive.infra.db.schema import init_database

    conn = init_database()
    try:
        stats = _insert_all(conn, tables, overwrite=overwrite)
        conn.commit()
    finally:
        conn.close()

    print(f"가져오기 완료: {input_path}")
    total_inserted = sum(v for v in stats.values())
    for table in _IMPORT_ORDER:
        count = stats.get(table, 0)
        if count:
            print(f"  {table}: {count}건 삽입")
    print(f"  합계: {total_inserted}건 삽입")


def _validate_format(raw: object) -> None:
    if not isinstance(raw, dict):
        raise ValueError("올바르지 않은 형식입니다.")
    if raw.get("format_version") != FORMAT_VERSION:
        raise ValueError(
            f"지원하지 않는 format_version: {raw.get('format_version')} (지원: {FORMAT_VERSION})"
        )
    if "tables" not in raw:
        raise ValueError("'tables' 키가 없습니다.")


def _remap_paths(
    tables: dict[str, list[dict[str, object]]],
    src: str,
    dst: str,
) -> dict[str, list[dict[str, object]]]:
    """모든 경로 컬럼의 src 접두사를 dst로 치환한다."""
    path_col_map = dict(_PATH_COLUMNS)
    result: dict[str, list[dict[str, object]]] = {}

    for table, rows in tables.items():
        cols = path_col_map.get(table, [])
        if not cols:
            result[table] = rows
            continue

        remapped: list[dict[str, object]] = []
        for row in rows:
            new_row = dict(row)
            for col in cols:
                val = new_row.get(col)
                if val is None:
                    continue
                if col == "output_files":
                    # JSON 배열: 각 경로 항목을 개별 치환
                    try:
                        paths: list[str] = json.loads(str(val))
                        new_row[col] = json.dumps(
                            [p.replace(src, dst) if isinstance(p, str) else p for p in paths],
                            ensure_ascii=False,
                        )
                    except (json.JSONDecodeError, TypeError):
                        pass
                elif isinstance(val, str):
                    new_row[col] = val.replace(src, dst)
            remapped.append(new_row)
        result[table] = remapped

    return result


def _insert_all(
    conn: sqlite3.Connection,
    tables: dict[str, list[dict[str, object]]],
    overwrite: bool,
) -> dict[str, int]:
    """FK 순서대로 각 테이블에 레코드를 삽입하고 삽입 건수를 반환한다."""
    conflict = "REPLACE" if overwrite else "IGNORE"
    stats: dict[str, int] = {}

    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for table in _IMPORT_ORDER:
            rows = tables.get(table, [])
            if not rows:
                stats[table] = 0
                continue

            cols = list(rows[0].keys())
            placeholders = ", ".join("?" * len(cols))
            col_list = ", ".join(cols)
            sql = f"INSERT OR {conflict} INTO {table} ({col_list}) VALUES ({placeholders})"

            inserted = 0
            for row in rows:
                values = [row[c] for c in cols]
                cursor = conn.execute(sql, values)
                inserted += cursor.rowcount
            stats[table] = inserted
    finally:
        conn.execute("PRAGMA foreign_keys = ON")

    return stats
