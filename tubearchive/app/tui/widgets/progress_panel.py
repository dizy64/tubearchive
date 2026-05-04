"""파이프라인 진행률 패널 위젯.

ffmpeg 실행 중 진행률 바와 실시간 로그를 표시한다.
``append_log()``, ``set_status()``, ``set_progress()`` 는
worker 스레드에서 ``app.call_from_thread()`` 를 통해 안전하게 호출된다.
"""

from __future__ import annotations

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Label, ProgressBar, RichLog, Static


class ProgressPanel(Static):
    """ffmpeg 진행률 바 + 실시간 로그 패널."""

    DEFAULT_CSS = """
    ProgressPanel {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    #progress-header {
        height: 3;
        align: left middle;
        margin-bottom: 1;
    }
    #progress-status {
        width: 1fr;
        color: $text-muted;
    }
    #progress-percent {
        width: 6;
        text-align: right;
        color: $accent;
        text-style: bold;
    }
    #progress-bar {
        width: 1fr;
        margin-bottom: 1;
    }
    #progress-log {
        height: 1fr;
        border: solid $panel;
    }
    """

    def __init__(self, id: str | None = None) -> None:  # noqa: A002
        super().__init__(id=id)
        self._status_label: Label | None = None
        self._percent_label: Label | None = None
        self._bar: ProgressBar | None = None
        self._log_widget: RichLog | None = None

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="progress-header"):
                yield Label("준비 중...", id="progress-status")
                yield Label("0%", id="progress-percent")
            yield ProgressBar(total=None, id="progress-bar", show_eta=False)
            yield RichLog(id="progress-log", highlight=True, markup=True, max_lines=500)

    def on_mount(self) -> None:
        self._status_label = self.query_one("#progress-status", Label)
        self._percent_label = self.query_one("#progress-percent", Label)
        self._bar = self.query_one("#progress-bar", ProgressBar)
        self._log_widget = self.query_one("#progress-log", RichLog)

    # ------------------------------------------------------------------
    # 공개 API (call_from_thread 경유 호출)
    # ------------------------------------------------------------------

    def set_status(self, text: str) -> None:
        """상태 라벨 갱신."""
        if self._status_label is None:
            self._status_label = self.query_one("#progress-status", Label)
        self._status_label.update(text)

    def set_progress(self, percent: int) -> None:
        """진행률 갱신 (0-100)."""
        if self._bar is None:
            self._bar = self.query_one("#progress-bar", ProgressBar)
        if self._bar.total is None:
            self._bar.update(total=100)
        self._bar.update(progress=float(percent))
        if self._percent_label is None:
            self._percent_label = self.query_one("#progress-percent", Label)
        self._percent_label.update(f"{percent}%")

    def start(self, label: str = "처리 중...") -> None:
        """실행 시작 상태로 초기화."""
        self.set_status(label)
        if self._bar is None:
            self._bar = self.query_one("#progress-bar", ProgressBar)
        self._bar.update(total=None)  # indeterminate
        if self._percent_label is None:
            self._percent_label = self.query_one("#progress-percent", Label)
        self._percent_label.update("")
        if self._log_widget is None:
            self._log_widget = self.query_one("#progress-log", RichLog)
        self._log_widget.clear()

    def finish(self, output_path: str) -> None:
        """완료 상태로 갱신."""
        if self._bar is None:
            self._bar = self.query_one("#progress-bar", ProgressBar)
        self._bar.update(total=100, progress=100.0)
        if self._percent_label is None:
            self._percent_label = self.query_one("#progress-percent", Label)
        self._percent_label.update("100%")
        self.set_status(f"[green]완료:[/green] {escape(output_path)}")

    def error(self, message: str) -> None:
        """오류 상태로 갱신."""
        if self._bar is None:
            self._bar = self.query_one("#progress-bar", ProgressBar)
        self._bar.update(total=100, progress=0.0)
        if self._percent_label is None:
            self._percent_label = self.query_one("#progress-percent", Label)
        self._percent_label.update("")
        self.set_status(f"[red]오류:[/red] {escape(message)}")

    def append_log(self, text: str) -> None:
        """로그 한 줄 추가."""
        if self._log_widget is None:
            self._log_widget = self.query_one("#progress-log", RichLog)
        self._log_widget.write(text.rstrip())
