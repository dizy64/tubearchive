"""Projects 탭 화면.

프로젝트 목록을 DataTable로 표시한다.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Label, Static

from tubearchive.app.queries.catalog import format_duration
from tubearchive.infra.db import database_session
from tubearchive.shared.progress import format_size


class ProjectsPane(Static):
    """프로젝트 목록 패널."""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[bold]프로젝트 목록[/]", classes="section-title")
            yield DataTable(id="projects-table")

    def on_mount(self) -> None:
        table = self.query_one("#projects-table", DataTable)
        table.add_columns("ID", "이름", "날짜 범위", "영상 수", "총 시간", "크기", "업로드")
        self.load_data()

    def load_data(self) -> None:
        """DB에서 프로젝트 목록을 조회하고 테이블을 갱신한다."""
        table = self.query_one("#projects-table", DataTable)
        table.clear()
        try:
            from tubearchive.infra.db.repository import ProjectRepository

            with database_session() as conn:
                rows = ProjectRepository(conn).get_all_with_stats()

            if not rows:
                table.add_row("-", "[dim]프로젝트 없음[/]", "-", "-", "-", "-", "-")
                return

            for project, stats in rows:
                if project.id is None:
                    continue
                if project.date_range_start and project.date_range_end:
                    if project.date_range_start == project.date_range_end:
                        date_range = project.date_range_start
                    else:
                        date_range = f"{project.date_range_start} ~ {project.date_range_end}"
                else:
                    date_range = "-"

                table.add_row(
                    str(project.id),
                    project.name,
                    date_range,
                    str(stats.total_count),
                    format_duration(stats.total_duration_seconds)
                    if stats.total_duration_seconds
                    else "-",
                    format_size(stats.total_size_bytes) if stats.total_size_bytes else "-",
                    f"{stats.uploaded_count}/{stats.total_count}",
                )
        except Exception as exc:
            table.clear()
            table.add_row("ERR", f"[red]{exc}[/]", "-", "-", "-", "-", "-")
