"""파일 브라우저 위젯.

``DirectoryTree`` 로 탐색하고 경로를 선택 목록에 추가한다.
경로 직접 입력 필드도 제공하므로 외장 드라이브 등 깊은 경로에 바로 접근 가능하다.
선택 목록은 멀티 선택을 지원하며, [x] 버튼으로 개별 제거하거나
[전체 삭제]로 일괄 제거할 수 있다.
``get_selected_targets()`` 로 최종 경로 목록을 조회한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Button, DirectoryTree, Input, Label, Static

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


class FileBrowserPane(Static):
    """디렉토리 트리 + 선택 목록 + 경로 직접 입력 패널.

    - DirectoryTree: 파일/디렉토리 탐색 (클릭 시 경로 입력창에 반영)
    - 경로 입력창 + [추가] 버튼: 직접 입력 또는 트리에서 선택 후 추가
    - 선택 목록: 추가된 경로 표시, [x] 개별 제거, [전체 삭제] 일괄 제거
    """

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
    #selected-header {
        height: 1;
        margin-top: 1;
        align: left middle;
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
    }
    .selected-row {
        height: 1;
        align: left middle;
    }
    .selected-row Label {
        width: 1fr;
        color: $accent;
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
        self._selected: list[Path] = []
        if initial_path is not None:
            self._selected.append(initial_path)

    def compose(self) -> ComposeResult:
        # 트리 루트는 항상 / (상위 탐색 보장)
        # 마지막 사용 디렉토리는 입력 필드 기본값으로만 사용
        initial_input = ""
        if self.initial_path:
            initial_input = str(self.initial_path)
        else:
            last_dir = _load_last_dir()
            if last_dir:
                initial_input = str(last_dir)
        with Vertical():
            yield Label("[bold]대상 경로 선택[/]", classes="section-title")
            yield DirectoryTree(str(Path("/")), id="browser-tree")
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

    # ------------------------------------------------------------------
    # 트리 이벤트: 클릭 시 입력 필드에 경로 반영
    # ------------------------------------------------------------------

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.query_one("#path-input", Input).value = str(event.path)

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.query_one("#path-input", Input).value = str(event.path)

    # ------------------------------------------------------------------
    # 추가 / 제거
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "path-add-btn":
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
            # 다음 실행 시 이 부근에서 트리가 시작되도록 저장
            _save_last_dir(path.parent if path.is_file() else path)

    def _rebuild_list(self) -> None:
        """선택 목록 컨테이너를 현재 _selected로 다시 그린다."""
        container = self.query_one("#selected-list", ScrollableContainer)
        container.remove_children()

        # 전체 삭제 버튼 활성/비활성
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
                parent = "\u2026" + parent[-24:]
            rows.append(
                Horizontal(
                    Label(f"{parent}/{display}", classes="selected-path"),
                    Button("[x]", id=f"rm-{i}", classes="rm-btn", variant="error"),
                    classes="selected-row",
                )
            )
        container.mount(*rows)

    def _notify_pipeline(self) -> None:
        """부모 PipelinePane의 실행 버튼 상태를 직접 갱신한다."""
        for ancestor in self.ancestors_with_self:
            if hasattr(ancestor, "_refresh_run_button") and ancestor is not self:
                ancestor._refresh_run_button()
                break

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------

    def get_selected_targets(self) -> list[Path]:
        """선택된 대상 경로 목록을 반환한다."""
        return list(self._selected)
