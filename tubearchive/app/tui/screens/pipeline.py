"""Pipeline 탭 화면.

파일 선택 + 옵션 패널로 구성된 파이프라인 실행 준비 화면.
실제 실행(ffmpeg)은 Phase 3에서 구현한다.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Static

from tubearchive.app.tui.widgets.file_browser import FileBrowserPane
from tubearchive.app.tui.widgets.option_panels import OptionsPane


class PipelinePane(Static):
    """파이프라인 실행 패널.

    - 좌측(40%): 파일 브라우저 (DirectoryTree + 선택 경로)
    - 우측(60%): 옵션 패널 (카테고리별 Collapsible)
    - 하단: Run 버튼 + 상태 메시지
    """

    DEFAULT_CSS = """
    PipelinePane {
        height: 1fr;
    }
    #pipeline-body {
        height: 1fr;
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

    def __init__(self, initial_path: Path | None = None) -> None:
        super().__init__()
        self.initial_path = initial_path

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal(id="pipeline-body"):
                yield FileBrowserPane(initial_path=self.initial_path)
                yield OptionsPane()
            with Horizontal(id="pipeline-footer"):
                yield Button("실행 (Phase 3)", id="run-button", variant="primary", disabled=True)
                yield Label(
                    "파일을 선택한 후 실행 버튼을 누르세요. (실행은 Phase 3에서 구현)",
                    id="pipeline-status",
                )

    def on_mount(self) -> None:
        # 초기 경로가 있으면 Run 버튼 활성화 여부 갱신
        self._refresh_run_button()

    def on_directory_tree_file_selected(self) -> None:
        self._refresh_run_button()

    def on_directory_tree_directory_selected(self) -> None:
        self._refresh_run_button()

    def _refresh_run_button(self) -> None:
        browser = self.query_one(FileBrowserPane)
        has_target = bool(browser.get_selected_targets())
        btn = self.query_one("#run-button", Button)
        btn.disabled = not has_target
        status = self.query_one("#pipeline-status", Label)
        if has_target:
            targets = browser.get_selected_targets()
            status.update(f"준비됨: {targets[0]}")
        else:
            status.update("파일을 선택한 후 실행 버튼을 누르세요.")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-button":
            self._preview_args()

    def _preview_args(self) -> None:
        """ValidatedArgs 미리보기 (Phase 3 실행 전 검증용)."""
        from tubearchive.app.tui.bridge import build_validated_args

        browser = self.query_one(FileBrowserPane)
        options = self.query_one(OptionsPane)
        targets = browser.get_selected_targets()

        try:
            state = options.collect_state()
            args = build_validated_args(targets, state)
            status = self.query_one("#pipeline-status", Label)
            from tubearchive.app.cli.main import ValidatedArgs

            if isinstance(args, ValidatedArgs):
                count = len(args.targets)
                flags = []
                if args.normalize_audio:
                    flags.append("loudnorm")
                if args.denoise:
                    flags.append("denoise")
                if args.stabilize:
                    flags.append("stabilize")
                if args.upload:
                    flags.append("upload")
                flag_str = ", ".join(flags) if flags else "기본"
                status.update(f"준비됨: {count}개 대상 | 옵션: {flag_str}")
        except ValueError as exc:
            status = self.query_one("#pipeline-status", Label)
            status.update(f"[red]{exc}[/]")
