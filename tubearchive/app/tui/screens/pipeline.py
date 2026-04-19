"""Pipeline 탭 화면.

파일 선택 + 옵션 패널로 구성된 파이프라인 실행 화면.
실행 시 ``@work(thread=True)`` 로 동기 ``run_pipeline()`` 을 백그라운드에서 처리하고
``app.call_from_thread()`` 로 TUI를 안전하게 갱신한다.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Static

from tubearchive.app.tui.models import TuiOptionState
from tubearchive.app.tui.widgets.file_browser import FileBrowserPane
from tubearchive.app.tui.widgets.option_panels import OptionsPane
from tubearchive.app.tui.widgets.progress_panel import ProgressPanel

# ---------------------------------------------------------------------------
# stdout/stderr 인터셉터
# ---------------------------------------------------------------------------


class _TuiWriter(io.TextIOBase):
    """print() 출력을 줄 단위로 캡처해 콜백으로 전달하는 writer."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self._callback = callback
        self._buf: str = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            stripped = line.rstrip()
            if stripped:
                self._callback(stripped)
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self._callback(self._buf.strip())
            self._buf = ""


class _TuiLogHandler(logging.Handler):
    """logging 출력을 TUI 로그 패널로 전달하는 핸들러."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self._callback = callback
        self.setFormatter(logging.Formatter("%(levelname)s  %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._callback(msg)
        except Exception:
            self.handleError(record)


# ---------------------------------------------------------------------------
# PipelinePane
# ---------------------------------------------------------------------------


class PipelinePane(Static):
    """파이프라인 실행 패널.

    - 좌측(40%): 파일 브라우저 (DirectoryTree + 선택 경로)
    - 우측(60%): 옵션 패널 (카테고리별 Collapsible)
    - 하단: Run 버튼 + 상태 메시지

    실행 버튼을 누르면 옵션 뷰가 ProgressPanel 로 전환되고,
    완료/오류 시 옵션 뷰로 복귀한다.
    """

    DEFAULT_CSS = """
    PipelinePane {
        height: 1fr;
    }
    #pipeline-body {
        height: 1fr;
        overflow: hidden;
    }
    #pipeline-progress {
        height: 1fr;
        display: none;
    }
    #pipeline-footer {
        height: 5;
        border-top: solid $accent;
        padding: 1 2;
        align: left middle;
    }
    #run-button {
        margin-right: 2;
    }
    #pipeline-status {
        color: $text-muted;
    }
    """

    def __init__(
        self,
        initial_path: Path | None = None,
        initial_state: TuiOptionState | None = None,
    ) -> None:
        super().__init__()
        self.initial_path = initial_path
        self._initial_state = initial_state
        self._running: bool = False

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="pipeline-body"):
                yield FileBrowserPane(
                    initial_path=self.initial_path,
                    on_change=self._refresh_run_button,
                )
                yield OptionsPane(initial_state=self._initial_state)
            yield ProgressPanel(id="pipeline-progress")
            with Horizontal(id="pipeline-footer"):
                yield Button("실행", id="run-button", variant="primary", disabled=True)
                yield Label(
                    "파일을 선택한 후 실행 버튼을 누르세요.",
                    id="pipeline-status",
                )

    def on_mount(self) -> None:
        self._refresh_run_button()

    # ------------------------------------------------------------------
    # 프리셋 메시지 이벤트
    # ------------------------------------------------------------------

    def on_options_pane_preset_action(self, event: OptionsPane.PresetAction) -> None:
        """OptionsPane 프리셋 버튼 → 앱 레벨 액션으로 위임한다."""
        from tubearchive.app.tui.app import TubeArchiveApp

        app = self.app
        if not isinstance(app, TubeArchiveApp):
            return
        if event.action == "preset-save":
            app.action_save_preset()
        elif event.action == "preset-load":
            app.action_load_preset()

    # ------------------------------------------------------------------
    # 버튼 이벤트
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-button":
            self._launch_pipeline()

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _refresh_run_button(self) -> None:
        if self._running:
            return
        browser = self.query_one(FileBrowserPane)
        has_target = bool(browser.get_selected_targets())
        btn = self.query_one("#run-button", Button)
        btn.disabled = not has_target
        status = self.query_one("#pipeline-status", Label)
        if has_target:
            status.update(f"준비됨: {browser.get_selected_targets()[0]}")
        else:
            status.update("파일을 선택한 후 실행 버튼을 누르세요.")

    def _show_progress_view(self) -> None:
        """옵션 뷰 → 진행률 뷰로 전환."""
        self.query_one("#pipeline-body").display = False
        self.query_one("#pipeline-progress").display = True
        self.query_one("#run-button", Button).disabled = True

    def _show_options_view(self) -> None:
        """진행률 뷰 → 옵션 뷰로 복귀."""
        self.query_one("#pipeline-progress").display = False
        self.query_one("#pipeline-body").display = True
        self._running = False
        self._refresh_run_button()

    def _launch_pipeline(self) -> None:
        """ValidatedArgs 빌드 → worker 실행."""
        from tubearchive.app.tui.bridge import build_validated_args

        browser = self.query_one(FileBrowserPane)
        options = self.query_one(OptionsPane)
        targets = browser.get_selected_targets()

        try:
            state = options.collect_state()
            validated_args = build_validated_args(targets, state)
        except ValueError as exc:
            self.query_one("#pipeline-status", Label).update(f"[red]{exc}[/]")
            return

        self._running = True
        self._show_progress_view()

        target_label = targets[0].name if targets else "?"
        panel = self.query_one(ProgressPanel)
        panel.start(f"처리 중: {target_label}")
        self.query_one("#pipeline-status", Label).update("실행 중...")

        self._run_pipeline_worker(validated_args)

    @work(thread=True, exclusive=True)
    def _run_pipeline_worker(self, validated_args: object) -> None:
        """worker 스레드에서 run_pipeline() 실행.

        stdout/stderr를 캡처하고 logging 핸들러를 임시 추가해
        모든 출력을 ProgressPanel.append_log() 로 전달한다.
        """
        import contextlib

        from tubearchive.app.cli.main import run_pipeline

        panel = self.query_one(ProgressPanel)

        def _safe_append(text: str) -> None:
            self.app.call_from_thread(panel.append_log, text)

        writer = _TuiWriter(_safe_append)
        log_handler = _TuiLogHandler(_safe_append)

        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        try:
            with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
                output_path = run_pipeline(validated_args, notifier=None)  # type: ignore[arg-type]
            self.app.call_from_thread(self._on_pipeline_done, output_path)
        except Exception as exc:
            self.app.call_from_thread(self._on_pipeline_error, str(exc))
        finally:
            root_logger.removeHandler(log_handler)

    # ------------------------------------------------------------------
    # 완료/오류 콜백 (call_from_thread 경유, メインスレッド)
    # ------------------------------------------------------------------

    def _on_pipeline_done(self, output_path: Path) -> None:
        panel = self.query_one(ProgressPanel)
        output_str = str(output_path) if output_path != Path() else "(완료)"
        panel.finish(output_str)
        self.query_one("#pipeline-status", Label).update(f"완료: {output_str}")
        # 옵션 뷰로 복귀하지 않고 진행률 패널 유지 (사용자가 결과 확인 가능)
        self._running = False
        btn = self.query_one("#run-button", Button)
        btn.label = "다시 실행"
        btn.disabled = False

    def _on_pipeline_error(self, message: str) -> None:
        panel = self.query_one(ProgressPanel)
        panel.error(message)
        self.query_one("#pipeline-status", Label).update(f"[red]오류: {message}[/]")
        self._running = False
        btn = self.query_one("#run-button", Button)
        btn.label = "다시 실행"
        btn.disabled = False
