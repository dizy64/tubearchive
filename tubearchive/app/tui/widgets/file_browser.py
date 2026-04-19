"""파일 브라우저 위젯.

``DirectoryTree`` 로 탐색하고 경로를 선택 목록에 추가한다.
경로 직접 입력 필드도 제공하므로 외장 드라이브 등 깊은 경로에 바로 접근 가능하다.
선택 목록은 멀티 선택을 지원하며, x 버튼으로 개별 제거할 수 있다.
``get_selected_targets()`` 로 최종 경로 목록을 조회한다.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.widgets import Button, DirectoryTree, Input, Label, Static


class FileBrowserPane(Static):
    """디렉토리 트리 + 선택 목록 + 경로 직접 입력 패널.

    - DirectoryTree: 파일/디렉토리 탐색 (클릭 시 경로 입력창에 반영)
    - 경로 입력창 + [추가] 버튼: 직접 입력 또는 트리에서 선택 후 추가
    - 선택 목록: 추가된 경로 표시, x 버튼으로 개별 제거
    """

    class SelectionChanged(Message):
        """선택 목록이 변경될 때 부모 위젯으로 전달하는 메시지."""

    DEFAULT_CSS = """
    FileBrowserPane {
        width: 40%;
        border-right: solid $accent;
        padding: 0 1;
    }
    #browser-tree {
        height: 1fr;
        min-height: 8;
    }
    #path-input-row {
        height: 3;
        margin-top: 1;
        align: left middle;
    }
    #path-input {
        width: 1fr;
    }
    #path-add-btn {
        width: 8;
        margin-left: 1;
    }
    #selected-label {
        color: $text-muted;
        margin-top: 1;
    }
    #selected-list {
        height: 6;
        border: solid $surface;
    }
    .selected-item {
        height: 1;
        align: left middle;
    }
    .selected-item-path {
        width: 1fr;
        color: $accent;
    }
    .remove-btn {
        width: 3;
        min-width: 3;
        background: transparent;
        border: none;
        color: $text-muted;
    }
    """

    def __init__(self, initial_path: Path | None = None) -> None:
        super().__init__()
        self.initial_path = initial_path
        self._selected: list[Path] = []
        if initial_path is not None:
            self._selected.append(initial_path)

    def compose(self) -> ComposeResult:
        root = Path("/")
        with Vertical():
            yield Label("[bold]대상 경로 선택[/]", classes="section-title")
            yield DirectoryTree(str(root), id="browser-tree")
            with Horizontal(id="path-input-row"):
                yield Input(
                    value=str(self.initial_path) if self.initial_path else "",
                    placeholder="/Volumes/... 또는 ~/Videos/",
                    id="path-input",
                )
                yield Button("추가", id="path-add-btn", variant="primary")
            yield Label("선택된 파일/폴더:", id="selected-label")
            yield ScrollableContainer(id="selected-list")

    def on_mount(self) -> None:
        self._rebuild_list()

    # ------------------------------------------------------------------
    # 트리 이벤트: 클릭 시 입력 필드에 경로 반영 (추가는 [추가] 버튼)
    # ------------------------------------------------------------------

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.query_one("#path-input", Input).value = str(event.path)
        event.stop()

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.query_one("#path-input", Input).value = str(event.path)
        event.stop()

    # ------------------------------------------------------------------
    # 추가 / 제거
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "path-add-btn":
            event.stop()
            self._add_from_input()
        elif event.button.id and event.button.id.startswith("rm-"):
            event.stop()
            try:
                idx = int(event.button.id[3:])
            except ValueError:
                return
            if 0 <= idx < len(self._selected):
                self._selected.pop(idx)
                self._rebuild_list()
                self._emit_change()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "path-input":
            event.stop()
            self._add_from_input()

    def _add_from_input(self) -> None:
        raw = self.query_one("#path-input", Input).value.strip()
        if not raw:
            return
        path = Path(raw).expanduser().resolve()
        if path not in self._selected:
            self._selected.append(path)
            self._rebuild_list()
            self._emit_change()

    def _rebuild_list(self) -> None:
        """선택 목록 컨테이너를 현재 _selected로 다시 그린다."""
        container = self.query_one("#selected-list", ScrollableContainer)
        container.remove_children()
        if not self._selected:
            container.mount(Label("(없음)", classes="placeholder"))
            return
        rows = []
        for i, path in enumerate(self._selected):
            display = str(path)
            if len(display) > 38:
                display = "\u2026" + display[-37:]
            rows.append(
                Horizontal(
                    Label(display, classes="selected-item-path"),
                    Button("\u00d7", id=f"rm-{i}", classes="remove-btn"),
                    classes="selected-item",
                )
            )
        container.mount(*rows)

    def _emit_change(self) -> None:
        """선택 목록 변경을 부모 위젯에 알린다."""
        self.post_message(self.SelectionChanged())

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def get_selected_targets(self) -> list[Path]:
        """선택된 대상 경로 목록을 반환한다."""
        return list(self._selected)
