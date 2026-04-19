"""프리셋 저장/불러오기 모달 스크린.

``SavePresetScreen``: 프리셋 이름 입력 → 저장 경로 반환.
``LoadPresetScreen``: 저장된 프리셋 목록 → 선택된 TuiOptionState 반환.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView

from tubearchive.app.tui.models import TuiOptionState


class SavePresetScreen(ModalScreen[str | None]):
    """프리셋 이름 입력 모달."""

    DEFAULT_CSS = """
    SavePresetScreen {
        align: center middle;
    }
    #preset-dialog {
        width: 60;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #preset-dialog Label {
        margin-bottom: 1;
    }
    #preset-dialog Input {
        margin-bottom: 1;
    }
    #dialog-buttons {
        height: 3;
        align: right middle;
    }
    #dialog-buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="preset-dialog"):
            yield Label("프리셋 이름을 입력하세요:")
            yield Input(placeholder="예: 기본 설정", id="preset-name")
            with Horizontal(id="dialog-buttons"):
                yield Button("취소", id="cancel-btn", variant="default")
                yield Button("저장", id="save-btn", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#preset-name", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            name = self.query_one("#preset-name", Input).value.strip()
            if name:
                self.dismiss(name)
            else:
                self.query_one("#preset-name", Input).focus()
        else:
            self.dismiss(None)

    def on_key(self, event: object) -> None:
        from textual.events import Key

        if isinstance(event, Key) and event.key == "escape":
            self.dismiss(None)


class LoadPresetScreen(ModalScreen[TuiOptionState | None]):
    """저장된 프리셋 목록 모달."""

    DEFAULT_CSS = """
    LoadPresetScreen {
        align: center middle;
    }
    #preset-dialog {
        width: 60;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #preset-dialog Label {
        margin-bottom: 1;
    }
    #preset-list {
        height: 8;
        margin-bottom: 1;
        border: solid $surface-lighten-1;
    }
    #dialog-buttons {
        height: 3;
        align: right middle;
    }
    #dialog-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, presets: list[tuple[str, Path]]) -> None:
        super().__init__()
        self._presets = presets

    def compose(self) -> ComposeResult:
        with Vertical(id="preset-dialog"):
            yield Label("불러올 프리셋을 선택하세요:")
            yield ListView(
                *[ListItem(Label(name)) for name, _ in self._presets],
                id="preset-list",
            )
            with Horizontal(id="dialog-buttons"):
                yield Button("취소", id="cancel-btn", variant="default")
                yield Button("불러오기", id="load-btn", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#preset-list", ListView).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "load-btn":
            self._load_selected()
        else:
            self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        # 더블클릭/Enter 선택
        self._load_selected()

    def _load_selected(self) -> None:
        lv = self.query_one("#preset-list", ListView)
        idx = lv.index
        if idx is not None and 0 <= idx < len(self._presets):
            _, path = self._presets[idx]
            try:
                from tubearchive.app.tui.models import load_preset

                state = load_preset(path)
                self.dismiss(state)
            except Exception:
                self.dismiss(None)
        else:
            self.dismiss(None)

    def on_key(self, event: object) -> None:
        from textual.events import Key

        if isinstance(event, Key) and event.key == "escape":
            self.dismiss(None)
