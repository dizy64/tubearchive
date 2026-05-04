"""파일별 트랜스코딩 진행률 패널 위젯.

파이프라인 실행 중 파일별 행(아이콘 + 파일명 + 퍼센트 + ETA)과
전체 진행 바, 실시간 로그를 표시한다.

``handle_event()``, ``append_log()``, ``finish()``, ``error()`` 는
worker 스레드에서 ``app.call_from_thread()`` 를 통해 안전하게 호출된다.
"""

from __future__ import annotations

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

_ICONS: dict[str, str] = {
    "pending": "·",
    "processing": "→",
    "done": "✓",
    "error": "✗",
}

_COLORS: dict[str, str] = {
    "pending": "dim",
    "processing": "yellow",
    "done": "green",
    "error": "red",
}


class _FileRow(Static):
    """파일 한 줄 행 — 아이콘 + 파일명 + 진행률 + ETA."""

    DEFAULT_CSS = """
    _FileRow {
        height: 1;
        padding: 0 1;
    }
    """

    def __init__(self, filename: str) -> None:
        super().__init__()
        self._filename = filename
        self._status = "pending"
        self._percent = 0
        self._eta = ""

    def render(self) -> str:
        icon = _ICONS[self._status]
        color = _COLORS[self._status]
        name = self._filename[:40]
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

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("준비 중...", id="fp-header")
            yield Vertical(id="fp-files")
            yield ProgressBar(total=None, id="fp-overall-bar", show_eta=False)
            yield RichLog(id="fp-log", highlight=True, markup=False, max_lines=500)

    # ------------------------------------------------------------------
    # 공개 API (call_from_thread 경유 호출)
    # ------------------------------------------------------------------

    def handle_event(self, event: ProgressEvent) -> None:
        """ProgressEvent 수신 → 해당 행 갱신."""
        if isinstance(event, FileStartEvent):
            self._total_files = event.total_files
            self.query_one("#fp-header", Label).update(f"처리 중: {event.total_files}개 파일")
            row = _FileRow(filename=event.filename)
            self._file_rows[event.file_index] = row
            self.query_one("#fp-files", Vertical).mount(row)
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
        """로그 한 줄 추가."""
        self.query_one("#fp-log", RichLog).write(text.rstrip())

    def start(self, label: str = "처리 중...") -> None:
        """실행 시작 상태로 초기화."""
        self._file_rows.clear()
        self._done_count = 0
        self._total_files = 0
        self.query_one("#fp-header", Label).update(label)
        files_container = self.query_one("#fp-files", Vertical)
        files_container.remove_children()
        bar = self.query_one("#fp-overall-bar", ProgressBar)
        bar.update(total=None)
        self.query_one("#fp-log", RichLog).clear()

    def finish(self, output_path: str) -> None:
        """완료 상태로 갱신."""
        bar = self.query_one("#fp-overall-bar", ProgressBar)
        bar.update(total=100, progress=100.0)
        self.query_one("#fp-header", Label).update(f"[green]완료:[/green] {escape(output_path)}")

    def error(self, message: str) -> None:
        """오류 상태로 갱신."""
        self.query_one("#fp-header", Label).update(f"[red]오류:[/red] {escape(message)}")

    def _update_overall_bar(self) -> None:
        if self._total_files == 0:
            return
        bar = self.query_one("#fp-overall-bar", ProgressBar)
        if bar.total is None:
            bar.update(total=self._total_files)
        bar.update(progress=float(self._done_count))


def _format_eta(info: object) -> str:
    """ProgressInfo.calculate_eta() 결과를 포맷된 문자열로 반환.

    ``calculate_eta`` 속성이 없거나 값이 None/0 이하이면 빈 문자열을 반환한다.
    """
    calculate_eta = getattr(info, "calculate_eta", None)
    if calculate_eta is None or not callable(calculate_eta):
        return ""
    try:
        eta_seconds = calculate_eta()
    except Exception:
        return ""
    if eta_seconds is None or eta_seconds <= 0:
        return ""
    try:
        from tubearchive.shared.progress import format_time

        return format_time(eta_seconds)
    except Exception:
        minutes = int(eta_seconds // 60)
        secs = int(eta_seconds % 60)
        return f"{minutes}:{secs:02d}"
