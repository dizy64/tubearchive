"""통계 대시보드 커맨드.

DB에 축적된 영상 처리 이력을 집계하여 텍스트 기반 통계 대시보드를 출력한다.

기능:
    - 전체 요약: 총 영상 수, 처리 시간, 출력 크기
    - 트랜스코딩: 상태별 집계, 성공/실패 비율, 평균 인코딩 속도
    - 병합/업로드: 병합 수, 업로드 수, 총 출력 크기
    - 기기별 분포: 텍스트 기반 막대 차트
    - 아카이브: 이동/삭제 이력 집계
    - 기간 필터: ``--period`` 로 특정 기간만 조회
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import cast

from tubearchive.app.queries.catalog import format_duration
from tubearchive.infra.db.repository import (
    ArchiveHistoryRepository,
    MergeJobRepository,
    TranscodingJobRepository,
    VideoRepository,
)
from tubearchive.shared.progress import format_size

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

BAR_CHAR = "█"
"""막대 차트 채움 문자."""

BAR_MAX_WIDTH = 30
"""막대 차트 최대 너비(문자 수)."""


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceStat:
    """기기별 영상 통계.

    Attributes:
        device: 기기 모델명.
        count: 영상 수.
    """

    device: str
    count: int


@dataclass(frozen=True)
class TranscodingStats:
    """트랜스코딩 통계.

    Attributes:
        completed: 완료(completed + merged) 수.
        failed: 실패 수.
        pending: 대기 수.
        processing: 진행 수.
        total: 전체 수.
        avg_encoding_speed: 평균 인코딩 속도 (배속). None이면 데이터 없음.
    """

    completed: int
    failed: int
    pending: int
    processing: int
    total: int
    avg_encoding_speed: float | None


@dataclass(frozen=True)
class MergeStats:
    """병합 작업 통계.

    Attributes:
        total: 전체 병합 수.
        completed: 완료 수.
        failed: 실패 수.
        uploaded: YouTube 업로드 수.
        total_size_bytes: 총 출력 파일 크기(바이트).
        total_duration: 총 출력 재생 시간(초).
    """

    total: int
    completed: int
    failed: int
    uploaded: int
    total_size_bytes: int
    total_duration: float


@dataclass(frozen=True)
class ArchiveStats:
    """아카이브 통계.

    Attributes:
        moved: 이동된 파일 수.
        deleted: 삭제된 파일 수.
    """

    moved: int
    deleted: int


@dataclass(frozen=True)
class StatsData:
    """통계 대시보드 전체 데이터.

    Attributes:
        period: 조회 기간 라벨. None이면 전체 기간.
        total_videos: 등록 영상 수.
        total_duration: 총 원본 재생 시간(초).
        devices: 기기별 분포 목록.
        transcoding: 트랜스코딩 통계.
        merge: 병합 통계.
        archive: 아카이브 통계.
    """

    period: str | None
    total_videos: int
    total_duration: float
    devices: list[DeviceStat]
    transcoding: TranscodingStats
    merge: MergeStats
    archive: ArchiveStats


# ---------------------------------------------------------------------------
# 데이터 조회
# ---------------------------------------------------------------------------


def fetch_stats(conn: sqlite3.Connection, period: str | None = None) -> StatsData:
    """DB에서 통계 데이터를 수집한다.

    각 Repository의 ``get_stats()`` 메서드를 호출하여
    :class:`StatsData` 로 조합한다.

    Args:
        conn: SQLite DB 연결.
        period: 기간 필터 (예: ``'2026-01'``). None이면 전체.

    Returns:
        통합된 통계 데이터.
    """
    video_stats = VideoRepository(conn).get_stats(period)
    tc_stats = TranscodingJobRepository(conn).get_stats(period)
    merge_stats = MergeJobRepository(conn).get_stats(period)
    archive_stats = ArchiveHistoryRepository(conn).get_stats(period)

    # Repository는 dict[str, object]를 반환하므로 mypy strict 모드에서
    # 타입 안전을 위해 cast()로 실제 타입을 명시한다.
    status_counts = cast(dict[str, int], tc_stats["status_counts"])

    # "merged"는 트랜스코딩 완료 후 병합까지 끝난 상태이므로
    # 통계상 "completed"에 합산한다.
    tc_completed = status_counts.get("completed", 0) + status_counts.get("merged", 0)
    tc_failed = status_counts.get("failed", 0)
    tc_pending = status_counts.get("pending", 0)
    tc_processing = status_counts.get("processing", 0)
    tc_total = sum(status_counts.values())

    raw_devices = cast(list[tuple[str, int]], video_stats["devices"])
    devices = [DeviceStat(device=d, count=c) for d, c in raw_devices]

    avg_encoding_speed = cast(float | None, tc_stats["avg_encoding_speed"])

    return StatsData(
        period=period,
        total_videos=cast(int, video_stats["total"]),
        total_duration=cast(float, video_stats["total_duration"]),
        devices=devices,
        transcoding=TranscodingStats(
            completed=tc_completed,
            failed=tc_failed,
            pending=tc_pending,
            processing=tc_processing,
            total=tc_total,
            avg_encoding_speed=avg_encoding_speed,
        ),
        merge=MergeStats(
            total=cast(int, merge_stats["total"]),
            completed=cast(int, merge_stats["completed"]),
            failed=cast(int, merge_stats["failed"]),
            uploaded=cast(int, merge_stats["uploaded"]),
            total_size_bytes=cast(int, merge_stats["total_size_bytes"]),
            total_duration=cast(float, merge_stats["total_duration"]),
        ),
        archive=ArchiveStats(
            moved=archive_stats["moved"],
            deleted=archive_stats["deleted"],
        ),
    )


# ---------------------------------------------------------------------------
# 출력
# ---------------------------------------------------------------------------


def render_bar_chart(items: list[DeviceStat], max_width: int = BAR_MAX_WIDTH) -> list[str]:
    """기기별 분포를 텍스트 기반 막대 차트로 렌더링한다.

    Args:
        items: 기기별 통계 목록 (count 내림차순 권장).
        max_width: 막대 최대 너비(문자 수).

    Returns:
        렌더링된 줄 목록. 빈 목록이면 데이터 없음.
    """
    if not items:
        return []

    total = sum(item.count for item in items)
    if total == 0:
        return []

    max_count = max(item.count for item in items)
    label_width = max(len(item.device) for item in items)

    lines: list[str] = []
    for item in items:
        bar_len = int(item.count / max_count * max_width) if max_count > 0 else 0
        bar_len = max(bar_len, 1)  # 최소 1칸 — 0건이 아닌 항목이 보이지 않는 것 방지
        bar = BAR_CHAR * bar_len
        pct = item.count / total * 100
        label = item.device.ljust(label_width)
        lines.append(f"  {label}  {bar} {item.count} ({pct:.0f}%)")

    return lines


def _format_success_rate(completed: int, total: int) -> str:
    """성공률을 포맷한다.

    Args:
        completed: 성공 수.
        total: 전체 수.

    Returns:
        포맷된 문자열 (예: ``'95.0%'``). 전체가 0이면 ``'-'``.
    """
    if total == 0:
        return "-"
    rate = completed / total * 100
    return f"{rate:.1f}%"


def render_stats(data: StatsData) -> str:
    """통계 데이터를 사람이 읽기 좋은 텍스트로 렌더링한다.

    Args:
        data: 통합 통계 데이터.

    Returns:
        렌더링된 전체 텍스트.
    """
    lines: list[str] = []

    # 제목
    title = "TubeArchive 통계 대시보드"
    if data.period:
        title += f" (기간: {data.period})"
    lines.append(f"\n📊 {title}\n")
    lines.append("=" * 60)

    # 전체 요약
    lines.append("")
    lines.append("📹 전체 요약")
    lines.append("-" * 40)
    lines.append(f"  등록 영상: {data.total_videos}개")
    lines.append(f"  총 원본 재생 시간: {format_duration(data.total_duration)}")
    if data.merge.total_size_bytes > 0:
        lines.append(f"  총 출력 크기: {format_size(data.merge.total_size_bytes)}")
    if data.merge.total_duration > 0:
        lines.append(f"  총 출력 재생 시간: {format_duration(data.merge.total_duration)}")

    # 트랜스코딩 통계
    tc = data.transcoding
    if tc.total > 0:
        lines.append("")
        lines.append("🔄 트랜스코딩")
        lines.append("-" * 40)
        lines.append(f"  전체: {tc.total}건")
        lines.append(
            f"  완료: {tc.completed}건 | 실패: {tc.failed}건"
            f" | 대기: {tc.pending}건 | 진행: {tc.processing}건"
        )
        lines.append(f"  성공률: {_format_success_rate(tc.completed, tc.total)}")
        if tc.avg_encoding_speed is not None:
            lines.append(f"  평균 인코딩 속도: {tc.avg_encoding_speed:.1f}x")

    # 병합 통계
    mg = data.merge
    if mg.total > 0:
        lines.append("")
        lines.append("📦 병합")
        lines.append("-" * 40)
        lines.append(f"  전체: {mg.total}건 (완료: {mg.completed} | 실패: {mg.failed})")
        lines.append(f"  YouTube 업로드: {mg.uploaded}건")

    # 기기별 분포
    if data.devices:
        lines.append("")
        lines.append("📷 기기별 분포")
        lines.append("-" * 40)
        chart_lines = render_bar_chart(data.devices)
        lines.extend(chart_lines)

    # 아카이브 통계
    arc = data.archive
    if arc.moved > 0 or arc.deleted > 0:
        lines.append("")
        lines.append("🗂️ 아카이브")
        lines.append("-" * 40)
        lines.append(f"  이동: {arc.moved}건 | 삭제: {arc.deleted}건")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 커맨드 진입점
# ---------------------------------------------------------------------------


def cmd_stats(conn: sqlite3.Connection, period: str | None = None) -> None:
    """``--stats`` CLI 옵션을 처리한다.

    DB에서 통계를 수집하고 대시보드를 출력한다.

    Args:
        conn: SQLite DB 연결.
        period: 기간 필터 (예: ``'2026-01'``). None이면 전체.
    """
    data = fetch_stats(conn, period)
    print(render_stats(data))
