"""History 탭 화면.

영상 카탈로그를 DataTable로 표시하고, 기기/상태 필터를 제공한다.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, Label, Select, Static

from tubearchive.infra.db import database_session

_STATUS_OPTIONS: list[tuple[str, str]] = [
    ("전체", ""),
    ("완료", "completed"),
    ("병합됨", "merged"),
    ("실패", "failed"),
    ("대기", "pending"),
    ("미추적", "untracked"),
]


def _fmt_dur(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m >= 60:
        return f"{m // 60}h{m % 60}m"
    return f"{m}m{s:02d}s"


class HistoryPane(Static):
    """영상 카탈로그 패널."""

    def __init__(self) -> None:
        super().__init__()
        self._device_filter: str = ""
        self._status_filter: str = ""
        self._search: str = ""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[bold]영상 카탈로그[/]", classes="section-title")
            with Horizontal(id="history-filters"):
                yield Input(
                    placeholder="기기 검색 (예: nikon)...",
                    id="history-device-input",
                )
                yield Select(
                    [(label, value) for label, value in _STATUS_OPTIONS],
                    value="",
                    id="history-status-select",
                    allow_blank=False,
                )
            yield DataTable(id="history-table")

    def on_mount(self) -> None:
        table = self.query_one("#history-table", DataTable)
        table.add_columns("날짜", "기기", "길이", "상태", "경로")
        self.load_data()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "history-device-input":
            self._device_filter = event.value
            self.load_data()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "history-status-select":
            self._status_filter = str(event.value) if event.value else ""
            self.load_data()

    def load_data(self) -> None:
        """DB에서 카탈로그 항목을 조회하고 테이블을 갱신한다."""
        table = self.query_one("#history-table", DataTable)
        table.clear()
        try:
            from tubearchive.app.queries.catalog import fetch_catalog_items

            with database_session() as conn:
                items = fetch_catalog_items(
                    conn,
                    search_pattern=None,
                    device_filter=self._device_filter or None,
                    status_filter=self._status_filter or None,
                    group_by_device=False,
                )

            if not items:
                table.add_row("-", "[dim]항목 없음[/]", "-", "-", "-")
                return

            for item in items:
                # 경로를 짧게 표시
                path_display = item.path
                if len(path_display) > 50:
                    path_display = "..." + path_display[-47:]
                table.add_row(
                    item.creation_date,
                    item.device,
                    _fmt_dur(item.duration_seconds),
                    item.status,
                    path_display,
                )
        except Exception as exc:
            table.clear()
            table.add_row("ERR", f"[red]{exc}[/]", "-", "-", "-")
