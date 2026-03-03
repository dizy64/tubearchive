"""영상 메타데이터 카탈로그 조회 및 검색 커맨드.

DB에 등록된 영상 메타데이터를 기기별·날짜별·상태별로 조회하고,
테이블/JSON/CSV 형식으로 출력한다.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from typing import TextIO, TypedDict

from tubearchive.infra.db.schema import init_database
from tubearchive.shared import truncate_path

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

STATUS_ICONS: dict[str, str] = {
    "pending": "⏳ 대기",
    "processing": "🔄 진행",
    "completed": "✅ 완료",
    "failed": "❌ 실패",
    "merged": "📦 병합됨",
}
"""작업 상태별 아이콘 매핑. ``cmd_status`` 등에서도 공용으로 사용."""

CATALOG_STATUS_SENTINEL = "__show__"
"""``--status`` 값 없이 호출 시 사용되는 센티널 값."""

CATALOG_UNKNOWN_STATUS = "untracked"
"""트랜스코딩 기록이 없는 영상의 상태 라벨."""

CATALOG_UNKNOWN_DEVICE = "미상"
"""기기 정보가 없는 영상에 표시되는 라벨."""


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VideoCatalogItem:
    """영상 메타데이터 카탈로그 항목.

    DB에서 조회한 영상 한 건의 메타데이터를 담는다.

    Attributes:
        video_id: DB videos 테이블의 PK.
        creation_time: 원본 ISO datetime 문자열.
        creation_date: ``creation_time`` 에서 추출한 날짜(YYYY-MM-DD).
        device: 촬영 기기 라벨.
        duration_seconds: 재생 시간(초). 알 수 없으면 None.
        status: 트랜스코딩 작업 상태 또는 ``'untracked'``.
        progress_percent: 진행률(0-100). 없으면 None.
        path: 원본 파일 경로 문자열.
    """

    video_id: int
    creation_time: str
    creation_date: str
    device: str
    duration_seconds: float | None
    status: str
    progress_percent: int | None
    path: str


class CatalogDeviceStat(TypedDict):
    """카탈로그 기기별 통계 (기기명, 파일 수)."""

    device: str
    count: int


class CatalogDateRange(TypedDict):
    """카탈로그 날짜 범위 (시작일, 종료일)."""

    start: str
    end: str


class CatalogSummary(TypedDict):
    """카탈로그 요약 통계.

    Attributes:
        total: 전체 영상 수.
        devices: 기기별 영상 수 리스트.
        date_range: 촬영 날짜 범위(최소~최대). 날짜 정보가 없으면 None.
    """

    total: int
    devices: list[CatalogDeviceStat]
    date_range: CatalogDateRange | None


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------


def format_duration(seconds: float) -> str:
    """초를 사람이 읽기 좋은 시간 문자열로 변환한다.

    예: ``90`` → ``'1m 30s'``, ``3720`` → ``'1h 2m'``

    Args:
        seconds: 변환할 초 값.

    Returns:
        포맷된 문자열.
    """
    total = int(seconds)
    if total >= 3600:
        return f"{total // 3600}h {(total % 3600) // 60}m"
    if total >= 60:
        return f"{total // 60}m {total % 60}s"
    return f"{total}s"


def parse_creation_date(creation_time: str) -> str:
    """ISO datetime 문자열에서 날짜(YYYY-MM-DD)만 추출한다.

    Args:
        creation_time: ISO 포맷 datetime 문자열.

    Returns:
        날짜 문자열. 빈 문자열이면 ``'-'``, 파싱 실패 시 앞 10자.
    """
    if not creation_time:
        return "-"
    try:
        return datetime.fromisoformat(creation_time).date().isoformat()
    except ValueError:
        return creation_time[:10]


def normalize_device_label(device: str | None) -> str:
    """기기 라벨을 정규화한다.

    None이거나 공백뿐이면 :data:`CATALOG_UNKNOWN_DEVICE` 를 반환한다.

    Args:
        device: 원본 기기명.

    Returns:
        정규화된 기기 라벨.
    """
    if not device:
        return CATALOG_UNKNOWN_DEVICE
    stripped = device.strip()
    return stripped if stripped else CATALOG_UNKNOWN_DEVICE


def normalize_status_filter(status: str | None) -> str | None:
    """상태 필터 값을 소문자로 정규화한다.

    ``None`` 이거나 센티널 값이면 ``None`` 을 반환한다.

    Args:
        status: 원본 상태 문자열.

    Returns:
        정규화된 상태 문자열 또는 None.
    """
    if status is None or status == CATALOG_STATUS_SENTINEL:
        return None
    return status.strip().lower()


def format_catalog_status(status: str) -> str:
    """카탈로그 출력용 상태 문자열을 포맷한다.

    ``'untracked'`` 이면 ``'-'`` 를 반환하고,
    그 외에는 :data:`STATUS_ICONS` 에서 아이콘을 찾아 반환한다.

    Args:
        status: 작업 상태 문자열.

    Returns:
        포맷된 상태 문자열.
    """
    if status == CATALOG_UNKNOWN_STATUS:
        return "-"
    return STATUS_ICONS.get(status, status)


# ---------------------------------------------------------------------------
# 데이터 조회
# ---------------------------------------------------------------------------


def fetch_catalog_items(
    conn: sqlite3.Connection,
    search_pattern: str | None,
    device_filter: str | None,
    status_filter: str | None,
    group_by_device: bool,
) -> list[VideoCatalogItem]:
    """영상 메타데이터 카탈로그를 DB에서 조회한다.

    ``videos`` 테이블과 ``transcoding_jobs`` 테이블을 조인하여
    각 영상의 최신 작업 상태를 함께 반환한다.

    Args:
        conn: SQLite DB 연결.
        search_pattern: 촬영 시각 LIKE 패턴 (None이면 전체).
        device_filter: 기기명 LIKE 패턴 (None이면 전체).
        status_filter: 작업 상태 필터 (None이면 전체).
        group_by_device: True이면 기기명 우선 정렬.

    Returns:
        조건에 맞는 :class:`VideoCatalogItem` 리스트.
    """
    where_clauses: list[str] = []
    params: list[str] = []

    if search_pattern:
        where_clauses.append("v.creation_time LIKE ?")
        params.append(f"%{search_pattern}%")

    if device_filter:
        where_clauses.append("v.device_model LIKE ? COLLATE NOCASE")
        params.append(f"%{device_filter}%")

    if status_filter:
        if status_filter == CATALOG_UNKNOWN_STATUS:
            where_clauses.append("lj.status IS NULL")
        else:
            where_clauses.append("lj.status = ?")
            params.append(status_filter)

    where_sql = ""
    if where_clauses:
        where_sql = "WHERE " + " AND ".join(where_clauses)

    if group_by_device:
        order_sql = (
            "ORDER BY CASE WHEN v.device_model IS NULL OR v.device_model = '' THEN 1 ELSE 0 END, "
            "v.device_model, v.creation_time DESC, v.id DESC"
        )
    else:
        order_sql = "ORDER BY v.creation_time DESC, v.id DESC"

    query = f"""
        WITH latest_jobs AS (
            SELECT video_id, status, progress_percent
            FROM (
                SELECT video_id, status, progress_percent, created_at, id,
                       ROW_NUMBER() OVER (
                           PARTITION BY video_id
                           ORDER BY created_at DESC, id DESC
                       ) AS rn
                FROM transcoding_jobs
            )
            WHERE rn = 1
        )
        SELECT
            v.id,
            v.original_path,
            v.creation_time,
            v.duration_seconds,
            v.device_model,
            lj.status AS transcode_status,
            lj.progress_percent AS transcode_progress
        FROM videos v
        LEFT JOIN latest_jobs lj ON v.id = lj.video_id
        {where_sql}
        {order_sql}
    """

    cursor = conn.execute(query, params)
    items: list[VideoCatalogItem] = []
    for row in cursor.fetchall():
        creation_time = row["creation_time"] or ""
        status = row["transcode_status"] or CATALOG_UNKNOWN_STATUS
        device = normalize_device_label(row["device_model"])
        items.append(
            VideoCatalogItem(
                video_id=row["id"],
                creation_time=creation_time,
                creation_date=parse_creation_date(creation_time),
                device=device,
                duration_seconds=row["duration_seconds"],
                status=status,
                progress_percent=row["transcode_progress"],
                path=row["original_path"],
            )
        )
    return items


def build_catalog_summary(items: list[VideoCatalogItem]) -> CatalogSummary:
    """카탈로그 아이템 리스트에서 요약 통계를 생성한다.

    기기별 영상 수와 촬영 날짜 범위를 집계한다.

    Args:
        items: 카탈로그 아이템 리스트.

    Returns:
        요약 통계 딕셔너리.
    """
    device_counts: dict[str, int] = {}
    date_values: list[date] = []

    for item in items:
        device_counts[item.device] = device_counts.get(item.device, 0) + 1
        try:
            date_values.append(date.fromisoformat(item.creation_date))
        except ValueError:
            continue

    devices_sorted = sorted(device_counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    device_stats: list[CatalogDeviceStat] = [
        {"device": device, "count": count} for device, count in devices_sorted
    ]

    date_range: CatalogDateRange | None = None
    if date_values:
        date_range = {
            "start": min(date_values).isoformat(),
            "end": max(date_values).isoformat(),
        }

    return {"total": len(items), "devices": device_stats, "date_range": date_range}


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------


def render_table(
    headers: list[str],
    rows: list[list[str]],
    aligns: list[str] | None = None,
) -> None:
    """고정폭 텍스트 테이블을 stdout에 렌더링한다.

    각 열의 최대 너비를 계산하여 정렬된 테이블을 출력한다.

    Args:
        headers: 열 헤더 리스트.
        rows: 행 데이터 리스트. 각 행은 문자열 리스트.
        aligns: 각 열의 정렬 방향 (``'left'`` 또는 ``'right'``).
                None이면 모두 왼쪽 정렬.
    """
    if not rows:
        print("📋 결과 없음")
        return

    aligns = aligns or ["left"] * len(headers)
    widths: list[int] = []
    for col_idx, header in enumerate(headers):
        max_cell_width = len(header)
        for row in rows:
            max_cell_width = max(max_cell_width, len(row[col_idx]))
        widths.append(max_cell_width)

    header_line = "  ".join(header.ljust(widths[col_idx]) for col_idx, header in enumerate(headers))
    print(header_line)
    print("-" * len(header_line))

    for row in rows:
        parts: list[str] = []
        for col_idx, cell in enumerate(row):
            if aligns[col_idx] == "right":
                parts.append(cell.rjust(widths[col_idx]))
            else:
                parts.append(cell.ljust(widths[col_idx]))
        print("  ".join(parts))


def _print_catalog_table(items: list[VideoCatalogItem], group_by_device: bool) -> None:
    """카탈로그 아이템을 테이블 형식으로 출력한다.

    ``group_by_device`` 가 True이면 기기별로 그룹화하여 출력한다.

    Args:
        items: 카탈로그 아이템 리스트.
        group_by_device: 기기별 그룹화 여부.
    """
    headers = ["ID", "Date", "Device", "Duration", "Status", "Path"]
    aligns = ["right", "left", "left", "right", "left", "left"]

    def to_row(item: VideoCatalogItem) -> list[str]:
        duration = (
            format_duration(item.duration_seconds) if item.duration_seconds is not None else "-"
        )
        status = format_catalog_status(item.status)
        return [
            str(item.video_id),
            item.creation_date,
            item.device,
            duration,
            status,
            truncate_path(item.path, max_len=60),
        ]

    if not items:
        print("📋 결과 없음")
        return

    if group_by_device:
        groups: dict[str, list[VideoCatalogItem]] = {}
        for item in items:
            groups.setdefault(item.device, []).append(item)

        for device, group_items in groups.items():
            print(f"\n📷 기기: {device} ({len(group_items)}개)")
            rows = [to_row(item) for item in group_items]
            render_table(headers, rows, aligns)
        return

    rows = [to_row(item) for item in items]
    render_table(headers, rows, aligns)


def _print_catalog_summary(summary: CatalogSummary, stream: TextIO = sys.stdout) -> None:
    """요약 통계를 지정된 스트림에 출력한다.

    Args:
        summary: 카탈로그 요약 통계.
        stream: 출력 대상(기본: stdout).
    """
    total = summary["total"]
    devices = summary["devices"]
    date_range = summary["date_range"]

    print(f"\n📊 요약: 총 영상 {total}개", file=stream)

    if devices:
        parts = [f"{d['device']} {d['count']}개" for d in devices]
        print(f"📷 기기 분포: {', '.join(parts)}", file=stream)

    if date_range:
        print(
            f"📅 날짜 범위: {date_range['start']} ~ {date_range['end']}",
            file=stream,
        )


def output_catalog(
    items: list[VideoCatalogItem],
    summary: CatalogSummary,
    output_format: str,
    group_by_device: bool,
) -> None:
    """지정된 형식으로 카탈로그를 출력한다.

    ``output_format`` 에 따라 JSON / CSV / 텍스트 테이블 중 하나를 선택한다.

    Args:
        items: 카탈로그 아이템 리스트.
        summary: 요약 통계.
        output_format: ``'json'``, ``'csv'``, 또는 ``'table'``.
        group_by_device: 기기별 그룹화 여부.
    """
    if output_format == "json":
        payload: dict[str, object] = {
            "summary": summary,
            "items": [
                {
                    "id": item.video_id,
                    "creation_time": item.creation_time,
                    "creation_date": item.creation_date,
                    "device": item.device,
                    "duration_seconds": item.duration_seconds,
                    "status": item.status,
                    "progress_percent": item.progress_percent,
                    "path": item.path,
                }
                for item in items
            ],
        }
        if group_by_device:
            payload["grouped_by_device"] = True
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if output_format == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(
            [
                "id",
                "creation_date",
                "creation_time",
                "device",
                "duration_seconds",
                "status",
                "progress_percent",
                "path",
            ]
        )
        for item in items:
            writer.writerow(
                [
                    item.video_id,
                    item.creation_date,
                    item.creation_time,
                    item.device,
                    item.duration_seconds if item.duration_seconds is not None else "",
                    item.status,
                    item.progress_percent if item.progress_percent is not None else "",
                    item.path,
                ]
            )
        _print_catalog_summary(summary, stream=sys.stderr)
        return

    _print_catalog_table(items, group_by_device)
    _print_catalog_summary(summary)


# ---------------------------------------------------------------------------
# 커맨드 진입점
# ---------------------------------------------------------------------------


def cmd_catalog(args: argparse.Namespace) -> None:
    """``--catalog`` / ``--search`` CLI 옵션을 처리한다.

    DB에서 영상 메타데이터를 조회하고,
    ``--json`` / ``--csv`` / 기본(테이블) 형식으로 출력한다.

    Args:
        args: 파싱된 CLI 인자.

    Raises:
        ValueError: 잘못된 상태 필터가 지정된 경우.
    """
    output_format = "table"
    if args.json:
        output_format = "json"
    elif args.csv:
        output_format = "csv"

    search_pattern = args.search
    if search_pattern is not None:
        search_pattern = search_pattern.strip()
        if not search_pattern:
            search_pattern = None

    device_filter = args.device.strip() if args.device else None
    status_filter = normalize_status_filter(args.status)

    if status_filter:
        allowed = {
            "pending",
            "processing",
            "completed",
            "failed",
            "merged",
            CATALOG_UNKNOWN_STATUS,
        }
        if status_filter not in allowed:
            raise ValueError(
                "잘못된 상태 필터입니다. "
                "pending/processing/completed/failed/merged/untracked 중 하나를 사용하세요."
            )

    conn = init_database()
    items = fetch_catalog_items(
        conn,
        search_pattern=search_pattern,
        device_filter=device_filter,
        status_filter=status_filter,
        group_by_device=bool(args.catalog),
    )
    summary = build_catalog_summary(items)
    output_catalog(items, summary, output_format, group_by_device=bool(args.catalog))
    conn.close()
