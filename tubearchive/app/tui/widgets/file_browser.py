"""파일 브라우저 위젯.

``DirectoryTree`` 를 사용하여 디렉토리/파일을 탐색하고 선택한다.
선택된 경로는 ``get_selected_targets()`` 로 조회한다.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DirectoryTree, Label, Static


class FileBrowserPane(Static):
    """디렉토리 트리 + 선택 파일 목록 패널.

    Attributes:
        _selected_path: 현재 선택된 디렉토리/파일 경로.
    """

    DEFAULT_CSS = """
    FileBrowserPane {
        width: 40%;
        border-right: solid $accent;
        padding: 0 1;
    }
    #browser-tree {
        height: 1fr;
    }
    #selected-label {
        color: $text-muted;
        margin-top: 1;
    }
    #selected-path {
        color: $accent;
        text-style: bold;
        height: auto;
        margin-bottom: 1;
    }
    """

    def __init__(self, initial_path: Path | None = None) -> None:
        super().__init__()
        self._selected_path: Path | None = initial_path

    def compose(self) -> ComposeResult:
        # 루트를 / 로 시작해 외장하드(/Volumes) 등 어디든 탐색 가능하게 한다.
        # initial_path가 있으면 해당 파일의 부모 디렉토리를 초기 선택 경로로 유지.
        root = Path("/")
        with Vertical():
            yield Label("[bold]대상 경로 선택[/]", classes="section-title")
            yield DirectoryTree(str(root), id="browser-tree")
            yield Label("선택됨:", id="selected-label")
            yield Label(
                str(self._selected_path) if self._selected_path else "(없음)",
                id="selected-path",
            )

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """파일 선택 시 경로 갱신."""
        self._selected_path = event.path
        self._update_selected_label()
        event.stop()

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        """디렉토리 선택 시 경로 갱신."""
        self._selected_path = event.path
        self._update_selected_label()
        event.stop()

    def _update_selected_label(self) -> None:
        label = self.query_one("#selected-path", Label)
        if self._selected_path:
            display = str(self._selected_path)
            # 경로가 길면 말줄임
            if len(display) > 45:
                display = "..." + display[-42:]
            label.update(display)
        else:
            label.update("(없음)")

    def get_selected_targets(self) -> list[Path]:
        """선택된 대상 경로 목록을 반환한다.

        Returns:
            선택된 경로가 있으면 단일 요소 목록, 없으면 빈 목록.
        """
        if self._selected_path is None:
            return []
        return [self._selected_path]
