"""파일별 트랜스코딩 진행률 패널 위젯.

파이프라인 실행 중 파일별 행(아이콘 + 파일명 + 퍼센트 + ETA)과
전체 진행 바, 실시간 로그를 표시한다.

``handle_event()``, ``append_log()``, ``finish()``, ``error()`` 는
worker 스레드에서 ``app.call_from_thread()`` 를 통해 안전하게 호출된다.
"""

from __future__ import annotations

import time
from typing import Literal

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Label, ProgressBar, RichLog, Static

from tubearchive.app.cli.context import (
    FileDoneEvent,
    FileProgressEvent,
    FileStartEvent,
    ProgressEvent,
)
from tubearchive.shared.progress import ProgressInfo, format_time

_Status = Literal["pending", "processing", "done", "error"]

_ICONS: dict[_Status, str] = {
    "pending": "·",
    "processing": "→",
    "done": "✓",
    "error": "✗",
}

_COLORS: dict[_Status, str] = {
    "pending": "dim",
    "processing": "yellow",
    "done": "green",
    "error": "red",
}


class _FileRow(Static):
    """파일 한 줄 행 — 아이콘 + 파일명 + 진행률 + ETA."""

    # FFmpeg stderr는 10-25 Hz로 진행률 라인을 emit한다.
    # TUI 렌더링 루프 포화를 방지하기 위해 ~10 Hz로 스로틀한다.
    _REFRESH_INTERVAL = 0.1

    DEFAULT_CSS = """
    _FileRow {
        height: 1;
        padding: 0 1;
    }
    """

    def __init__(self, filename: str) -> None:
        super().__init__()
        self._filename = filename
        self._status: _Status = "pending"
        self._percent = 0
        self._eta = ""
        self._last_refresh = 0.0

    def render(self) -> str:
        icon = _ICONS[self._status]
        color = _COLORS[self._status]
        name = escape(self._filename[:40])
        if self._status == "processing":
            pct = f"{self._percent:3d}%"
            eta = f"  ETA {self._eta}" if self._eta else ""
            return f"[{color}]{icon}[/]  {name:<42} [{color}]{pct}{eta}[/]"
        if self._status == "done":
            return f"[{color}]{icon}[/]  {name:<42} [{color}]완료[/]"
        if self._status == "error":
            return f"[{color}]{icon}[/]  {name:<42} [{color}]오류[/]"
        return f"[{color}]{icon}[/]  {name}"

    def mark_processing(self) -> None:
        self._status = "processing"
        self.refresh()

    def update_progress(self, percent: int, eta: str) -> None:
        self._status = "processing"
        self._percent = percent
        self._eta = eta
        now = time.monotonic()
        if now - self._last_refresh >= self._REFRESH_INTERVAL:
            self._last_refresh = now
            self.refresh()

    def mark_done(self, success: bool) -> None:
        self._status = "done" if success else "error"
        if success:
            self._percent = 100
        self.refresh()


class FileProgressPanel(Widget):
    """파이프라인 진행률 패널 — 파일별 행 + 전체 진행 바 + 로그."""

    DEFAULT_CSS = """
    FileProgressPanel {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    #fp-header {
        height: 2;
        align: left middle;
        margin-bottom: 1;
    }
    #fp-files {
        height: auto;
        max-height: 12;
    }
    #fp-overall-bar {
        width: 1fr;
        margin: 1 0;
    }
    #fp-log {
        height: 1fr;
        border: solid $panel;
    }
    """

    def __init__(self, id: str | None = None) -> None:  # noqa: A002
        super().__init__(id=id)
        self._file_rows: dict[int, _FileRow] = {}
        self._done_count = 0
        self._total_files = 0
        self._header: Label | None = None
        self._files_container: Vertical | None = None
        self._bar: ProgressBar | None = None
        self._log_widget: RichLog | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("준비 중...", id="fp-header")
            yield Vertical(id="fp-files")
            yield ProgressBar(total=None, id="fp-overall-bar", show_eta=False)
            yield RichLog(id="fp-log", highlight=True, markup=False, max_lines=500)

    def on_mount(self) -> None:
        self._header = self.query_one("#fp-header", Label)
        self._files_container = self.query_one("#fp-files", Vertical)
        self._bar = self.query_one("#fp-overall-bar", ProgressBar)
        self._log_widget = self.query_one("#fp-log", RichLog)

    # ------------------------------------------------------------------
    # 공개 API (call_from_thread 경유 호출)
    # ------------------------------------------------------------------

    def handle_event(self, event: ProgressEvent) -> None:
        if isinstance(event, FileStartEvent):
            self._total_files = event.total_files
            if self._header is None:
                self._header = self.query_one("#fp-header", Label)
            self._header.update(f"처리 중: {event.total_files}개 파일")
            row = _FileRow(filename=event.filename)
            self._file_rows[event.file_index] = row
            if self._files_container is None:
                self._files_container = self.query_one("#fp-files", Vertical)
            self._files_container.mount(row)
            row.mark_processing()

        elif isinstance(event, FileProgressEvent):
            target = self._file_rows.get(event.file_index)
            if target is not None:
                eta = _format_eta(event.info)
                target.update_progress(event.info.percent, eta)

        elif isinstance(event, FileDoneEvent):
            target = self._file_rows.get(event.file_index)
            if target is not None:
                target.mark_done(event.success)
            self._done_count += 1
            self._update_overall_bar()

    def append_log(self, text: str) -> None:
        if self._log_widget is None:
            self._log_widget = self.query_one("#fp-log", RichLog)
        self._log_widget.write(text.rstrip())

    def start(self, label: str = "처리 중...") -> None:
        """실행 시작 상태로 초기화."""
        self._file_rows.clear()
        self._done_count = 0
        self._total_files = 0
        if self._header is None:
            self._header = self.query_one("#fp-header", Label)
        self._header.update(label)
        if self._files_container is None:
            self._files_container = self.query_one("#fp-files", Vertical)
        self._files_container.remove_children()
        if self._bar is None:
            self._bar = self.query_one("#fp-overall-bar", ProgressBar)
        self._bar.update(total=None)
        if self._log_widget is None:
            self._log_widget = self.query_one("#fp-log", RichLog)
        self._log_widget.clear()

    def finish(self, output_path: str) -> None:
        """완료 상태로 갱신."""
        if self._bar is None:
            self._bar = self.query_one("#fp-overall-bar", ProgressBar)
        self._bar.update(total=100, progress=100.0)
        if self._header is None:
            self._header = self.query_one("#fp-header", Label)
        self._header.update(f"[green]완료:[/green] {escape(output_path)}")

    def error(self, message: str) -> None:
        """오류 상태로 갱신."""
        if self._header is None:
            self._header = self.query_one("#fp-header", Label)
        self._header.update(f"[red]오류:[/red] {escape(message)}")

    def _update_overall_bar(self) -> None:
        if self._total_files == 0:
            return
        if self._bar is None:
            self._bar = self.query_one("#fp-overall-bar", ProgressBar)
        if self._bar.total is None:
            self._bar.update(total=self._total_files)
        self._bar.update(progress=float(self._done_count))


def _format_eta(info: ProgressInfo) -> str:
    eta_seconds = info.calculate_eta()
    if eta_seconds is None or eta_seconds <= 0:
        return ""
    return format_time(eta_seconds)
