"""프로젝트 관리 커맨드.

프로젝트 목록 조회, 상세 조회, 날짜별 그룹핑 등의 기능을 제공한다.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from typing import TextIO

from tubearchive.app.cli.main import database_session
from tubearchive.app.queries.catalog import (
    format_duration,
    render_table,
)
from tubearchive.domain.models.job import Project
from tubearchive.infra.db.repository import ProjectRepository
from tubearchive.shared import truncate_path
from tubearchive.shared.progress import format_size

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 프로젝트 목록 출력
# ---------------------------------------------------------------------------


def _format_date_range(project: Project) -> str:
    """프로젝트 날짜 범위를 포맷팅한다."""
    if project.date_range_start and project.date_range_end:
        if project.date_range_start == project.date_range_end:
            return project.date_range_start
        return f"{project.date_range_start} ~ {project.date_range_end}"
    return "-"


def _format_project_status(total_count: int, uploaded_count: int) -> str:
    """프로젝트 상태 요약 문자열 생성."""
    if total_count == 0:
        return "빈 프로젝트"
    if uploaded_count == total_count:
        return f"전체 업로드 ({total_count}개)"
    if uploaded_count > 0:
        return f"부분 업로드 ({uploaded_count}/{total_count})"
    return f"영상 {total_count}개"


def print_project_list(
    conn: sqlite3.Connection,
    output_format: str = "table",
    stream: TextIO = sys.stdout,
) -> None:
    """프로젝트 목록을 출력한다.

    Args:
        conn: DB 연결
        output_format: 출력 형식 (``table``, ``json``)
        stream: 출력 대상
    """
    repo = ProjectRepository(conn)
    # 단일 쿼리로 모든 프로젝트와 통계를 조회 (N+1 방지)
    projects_with_stats = repo.get_all_with_stats()

    if output_format == "json":
        items = []
        for project, stats in projects_with_stats:
            items.append(
                {
                    "id": project.id,
                    "name": project.name,
                    "description": project.description,
                    "date_range_start": project.date_range_start,
                    "date_range_end": project.date_range_end,
                    "playlist_id": project.playlist_id,
                    "merge_job_count": stats.total_count,
                    "total_duration_seconds": stats.total_duration_seconds,
                    "uploaded_count": stats.uploaded_count,
                    "created_at": project.created_at.isoformat(),
                }
            )
        print(json.dumps(items, ensure_ascii=False, indent=2), file=stream)
        return

    if not projects_with_stats:
        print("📋 프로젝트 없음", file=stream)
        print('  "tubearchive --project 이름" 으로 프로젝트를 생성하세요.', file=stream)
        return

    headers = ["ID", "이름", "날짜 범위", "영상 수", "총 시간", "상태"]
    aligns = ["right", "left", "left", "right", "right", "left"]
    rows: list[list[str]] = []

    for project, stats in projects_with_stats:
        if project.id is None:
            continue
        status = _format_project_status(stats.total_count, stats.uploaded_count)
        rows.append(
            [
                str(project.id),
                project.name,
                _format_date_range(project),
                str(stats.total_count),
                format_duration(stats.total_duration_seconds)
                if stats.total_duration_seconds > 0
                else "-",
                status,
            ]
        )

    print(f"\n📁 프로젝트 목록 ({len(projects_with_stats)}개)\n", file=stream)
    render_table(headers, rows, aligns)


# ---------------------------------------------------------------------------
# 프로젝트 상세 출력
# ---------------------------------------------------------------------------


def print_project_detail(
    conn: sqlite3.Connection,
    project_id: int,
    output_format: str = "table",
    stream: TextIO = sys.stdout,
) -> None:
    """프로젝트 상세 정보를 출력한다.

    포함된 영상 목록, 총 시간, 업로드 상태, 날짜별 그룹핑을 표시한다.

    Args:
        conn: DB 연결
        project_id: 프로젝트 ID
        output_format: 출력 형식 (``table``, ``json``)
        stream: 출력 대상
    """
    repo = ProjectRepository(conn)
    detail = repo.get_detail(project_id)

    if detail is None:
        print(f"프로젝트 ID {project_id}을(를) 찾을 수 없습니다.", file=sys.stderr)
        return

    project = detail.project
    merge_jobs = detail.merge_jobs
    date_groups = detail.date_groups

    if output_format == "json":
        payload: dict[str, object] = {
            "project": {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "date_range_start": project.date_range_start,
                "date_range_end": project.date_range_end,
                "playlist_id": project.playlist_id,
                "created_at": project.created_at.isoformat(),
                "updated_at": project.updated_at.isoformat(),
            },
            "summary": {
                "total_count": detail.total_count,
                "uploaded_count": detail.uploaded_count,
                "total_duration_seconds": detail.total_duration_seconds,
                "total_size_bytes": detail.total_size_bytes,
            },
            "merge_jobs": [
                {
                    "id": job.id,
                    "title": job.title,
                    "date": job.date,
                    "duration_seconds": job.total_duration_seconds,
                    "size_bytes": job.total_size_bytes,
                    "youtube_id": job.youtube_id,
                    "output_path": str(job.output_path),
                }
                for job in merge_jobs
            ],
            "date_groups": {
                date_key: [job.id for job in jobs] for date_key, jobs in date_groups.items()
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=stream)
        return

    # 헤더
    print(f"\n📁 프로젝트: {project.name}", file=stream)
    if project.description:
        print(f"   {project.description}", file=stream)
    print(f"   날짜 범위: {_format_date_range(project)}", file=stream)
    if project.playlist_id:
        print(f"   플레이리스트: {project.playlist_id}", file=stream)

    # 요약
    print(file=stream)
    print(f"   총 영상: {detail.total_count}개", file=stream)
    if detail.total_duration_seconds > 0:
        print(f"   총 시간: {format_duration(detail.total_duration_seconds)}", file=stream)
    if detail.total_size_bytes > 0:
        print(f"   총 크기: {format_size(detail.total_size_bytes)}", file=stream)
    upload_status = (
        f"{detail.uploaded_count}/{detail.total_count} 업로드됨" if detail.total_count > 0 else "-"
    )
    print(f"   업로드: {upload_status}", file=stream)

    # 날짜별 그룹핑
    if not date_groups:
        print("\n   영상 없음", file=stream)
        return

    headers = ["ID", "제목", "시간", "크기", "업로드"]
    aligns = ["right", "left", "right", "right", "left"]

    for date_key in sorted(date_groups.keys()):
        jobs = date_groups[date_key]
        print(f"\n  📅 {date_key} ({len(jobs)}개)", file=stream)

        rows: list[list[str]] = []
        for job in jobs:
            duration_str = (
                format_duration(job.total_duration_seconds) if job.total_duration_seconds else "-"
            )
            size_str = format_size(job.total_size_bytes) if job.total_size_bytes else "-"
            yt_status = f"✅ {job.youtube_id}" if job.youtube_id else "❌"
            rows.append(
                [
                    str(job.id),
                    job.title or truncate_path(str(job.output_path), max_len=40),
                    duration_str,
                    size_str,
                    yt_status,
                ]
            )
        render_table(headers, rows, aligns)


# ---------------------------------------------------------------------------
# CLI 커맨드 진입점
# ---------------------------------------------------------------------------


def cmd_project_list(output_json: bool = False) -> None:
    """``--project-list`` CLI 옵션을 처리한다."""
    with database_session() as conn:
        print_project_list(conn, output_format="json" if output_json else "table")


def cmd_project_detail(project_id: int, output_json: bool = False) -> None:
    """``--project-detail`` CLI 옵션을 처리한다."""
    with database_session() as conn:
        print_project_detail(conn, project_id, output_format="json" if output_json else "table")
