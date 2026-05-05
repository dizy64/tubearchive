"""파일 브라우저 위젯.

``DirectoryTree`` 로 탐색하고 경로를 선택 목록에 추가한다.
경로 직접 입력 필드도 제공하므로 외장 드라이브 등 깊은 경로에 바로 접근 가능하다.
선택 목록은 멀티 선택을 지원하며, [x] 버튼으로 개별 제거하거나
[전체 삭제]로 일괄 제거할 수 있다.
``get_selected_targets()`` 로 최종 경로 목록을 조회한다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, DirectoryTree, Input, Label

logger = logging.getLogger(__name__)

_LAST_DIR_FILE = Path("~/.tubearchive/.tui_last_dir").expanduser()


def _load_last_dir() -> Path | None:
    """마지막으로 사용한 디렉토리를 읽는다."""
    try:
        text = _LAST_DIR_FILE.read_text(encoding="utf-8").strip()
        if text:
            p = Path(text)
            if p.is_dir():
                return p
    except Exception:  # noqa: S110
        pass
    return None


def _save_last_dir(directory: Path) -> None:
    """디렉토리 경로를 저장한다."""
    try:
        _LAST_DIR_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LAST_DIR_FILE.write_text(str(directory), encoding="utf-8")
    except Exception:  # noqa: S110
        pass


class FilteredDirectoryTree(DirectoryTree):
    """숨김 파일(.으로 시작)을 제외하는 DirectoryTree."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [p for p in paths if not p.name.startswith(".")]


class FileBrowserPane(Widget):
    """디렉토리 트리 + 선택 목록 + 경로 직접 입력 패널.

    - DirectoryTree: 파일/디렉토리 탐색 (클릭 시 경로 입력창에 반영)
    - 경로 입력창 + [추가] 버튼: 직접 입력 또는 트리에서 선택 후 추가
    - 선택 목록: 추가된 경로 표시, [x] 개별 제거, [전체 삭제] 일괄 제거

    ``target_count`` reactive를 watch하면 선택 변경을 감지할 수 있다.
    """

    target_count: reactive[int] = reactive(0)

    DEFAULT_CSS = """
    FileBrowserPane {
        width: 40%;
        min-width: 40;
        max-width: 50%;
        border-right: solid $accent;
        padding: 0 1;
        overflow: hidden;
    }
    #tree-header {
        height: auto;
        align: left middle;
    }
    #tree-header Label {
        width: 1fr;
    }
    #tree-up-btn {
        width: 8;
        min-width: 8;
    }
    #browser-tree {
        height: 1fr;
        min-height: 8;
    }
    #path-input-row {
        height: auto;
        margin-top: 1;
    }
    #path-input {
        width: 1fr;
    }
    #path-add-btn {
        width: 8;
        margin-left: 1;
    }
    #selected-header {
        height: auto;
        margin-top: 1;
    }
    #selected-label {
        color: $text-muted;
        width: 1fr;
    }
    #clear-all-btn {
        min-width: 10;
        color: $error;
    }
    #selected-list {
        height: auto;
        max-height: 8;
        border: solid $surface;
        overflow-x: hidden;
        overflow-y: auto;
    }
    .selected-row {
        height: 1;
        align: left middle;
        overflow: hidden;
    }
    .selected-row Label {
        width: 1fr;
        color: $accent;
        overflow: hidden;
    }
    .selected-row .rm-btn {
        width: 5;
        min-width: 5;
        color: $error;
    }
    """

    def __init__(self, initial_path: Path | None = None) -> None:
        super().__init__()
        self.initial_path = initial_path
        self._tree_root: Path = Path.home()
        self._focus_target: Path | None = None
        self._selected: list[Path] = []
        if initial_path is not None:
            self._selected.append(initial_path)
            self.target_count = 1

    def compose(self) -> ComposeResult:
        # 트리 루트 결정:
        #   initial_path 있음 → 부모를 루트로, initial_path 노드 포커싱
        #   last_dir 있음    → 부모를 루트로, last_dir 노드 포커싱
        #   없음             → 홈 디렉토리
        if self.initial_path:
            p = self.initial_path
            self._tree_root = p.parent
            self._focus_target = p
            initial_input = str(p)
        else:
            last_dir = _load_last_dir()
            if last_dir:
                self._tree_root = last_dir.parent
                self._focus_target = last_dir
            else:
                self._tree_root = Path.home()
                self._focus_target = None
            initial_input = str(last_dir or self._tree_root)

        with Vertical():
            with Horizontal(id="tree-header"):
                yield Label("[bold]대상 경로 선택[/]", classes="section-title")
                yield Button("↑", id="tree-up-btn", variant="default")
            yield FilteredDirectoryTree(str(self._tree_root), id="browser-tree")
            with Horizontal(id="path-input-row"):
                yield Input(
                    value=initial_input,
                    placeholder="/Volumes/... 또는 ~/Videos/",
                    id="path-input",
                )
                yield Button("추가", id="path-add-btn", variant="primary")
            with Horizontal(id="selected-header"):
                yield Label("선택된 파일/폴더:", id="selected-label")
                yield Button("전체 삭제", id="clear-all-btn", variant="default", disabled=True)
            yield ScrollableContainer(id="selected-list")

    def on_mount(self) -> None:
        self._rebuild_list()
        if self._focus_target:
            self.set_timer(0.25, self._focus_initial_node)

    def _focus_initial_node(self) -> None:
        """트리에서 _focus_target 노드를 찾아 커서를 이동한다."""
        target = self._focus_target
        if not target:
            return
        tree = self.query_one("#browser-tree", FilteredDirectoryTree)
        for node in tree.root.children:
            try:
                if Path(node.data.path) == target:  # type: ignore[union-attr]
                    tree.move_cursor(node)
                    return
            except (AttributeError, TypeError):
                pass

    # ------------------------------------------------------------------
    # 트리 이벤트: 클릭 시 입력 필드에 경로 반영
    # ------------------------------------------------------------------

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.query_one("#path-input", Input).value = str(event.path)

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.query_one("#path-input", Input).value = str(event.path)

    # ------------------------------------------------------------------
    # 추가 / 제거 / 상위 이동
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tree-up-btn":
            event.stop()
            self._go_up()
        elif event.button.id == "path-add-btn":
            event.stop()
            self._add_from_input()
        elif event.button.id == "clear-all-btn":
            event.stop()
            self._selected.clear()
            self._rebuild_list()
            self._notify_pipeline()
        elif event.button.id and event.button.id.startswith("rm-"):
            event.stop()
            try:
                idx = int(event.button.id[3:])
            except ValueError:
                return
            if 0 <= idx < len(self._selected):
                self._selected.pop(idx)
                self._rebuild_list()
                self._notify_pipeline()

    def _go_up(self) -> None:
        """트리를 한 단계 상위 디렉토리로 이동한다."""
        parent = self._tree_root.parent
        if parent == self._tree_root:
            return  # 이미 루트(/)
        self._tree_root = parent
        self.query_one("#browser-tree", FilteredDirectoryTree).path = self._tree_root

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
            self._notify_pipeline()
            _save_last_dir(path.parent if path.is_file() else path)

    def _rebuild_list(self) -> None:
        """선택 목록 컨테이너를 현재 _selected로 다시 그린다."""
        container = self.query_one("#selected-list", ScrollableContainer)
        container.remove_children()

        clear_btn = self.query_one("#clear-all-btn", Button)
        clear_btn.disabled = not self._selected

        if not self._selected:
            container.mount(Label("(없음)", classes="placeholder"))
            return
        rows = []
        for i, path in enumerate(self._selected):
            display = path.name
            parent = str(path.parent)
            if len(parent) > 25:
                parent = "…" + parent[-24:]
            rows.append(
                Horizontal(
                    Label(f"{parent}/{display}", classes="selected-path"),
                    Button("[x]", id=f"rm-{i}", classes="rm-btn", variant="error"),
                    classes="selected-row",
                )
            )
        container.mount(*rows)

    def _notify_pipeline(self) -> None:
        """reactive target_count를 갱신하여 부모가 watch로 감지한다."""
        self.target_count = len(self._selected)

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def get_selected_targets(self) -> list[Path]:
        """선택된 대상 경로 목록을 반환한다."""
        return list(self._selected)
