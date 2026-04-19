"""옵션 패널 위젯.

카테고리별 Collapsible 섹션으로 구성된다.
General 섹션은 기본 펼침, 나머지는 기본 접힘이므로
미설정 옵션이 마치 적용되는 것처럼 보이는 혼란을 방지한다.
``collect_state()``로 현재 위젯 값을 수집하고,
``apply_state()``로 외부에서 상태를 주입할 수 있다.
"""

from __future__ import annotations

import contextlib

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.widgets import Button, Collapsible, Input, Label, Select, Static, Switch

from tubearchive.app.tui.models import (
    CATEGORY_DEFS,
    OptionDef,
    TuiOptionState,
    default_state,
)

_OPT_PREFIX = "opt-"


def _field_id(field: str) -> str:
    return f"{_OPT_PREFIX}{field}"


class _OptionRow(Horizontal):
    """라벨 + 위젯 한 행."""

    DEFAULT_CSS = """
    _OptionRow {
        height: auto;
        margin: 0;
        align: left middle;
    }
    _OptionRow Label {
        width: 26;
        color: $text-muted;
    }
    _OptionRow Switch {
        width: auto;
    }
    _OptionRow Input {
        width: 30;
    }
    _OptionRow Select {
        width: 30;
    }
    """

    def __init__(self, opt: OptionDef, initial: TuiOptionState) -> None:
        super().__init__()
        self._opt = opt
        self._initial = initial

    def compose(self) -> ComposeResult:
        opt = self._opt
        state = self._initial
        yield Label(opt.label)

        raw_val = getattr(state, opt.field)
        widget_id = _field_id(opt.field)

        if opt.widget == "switch":
            yield Switch(value=bool(raw_val), id=widget_id)
        elif opt.widget == "select":
            options: list[tuple[str, str]] = list(opt.choices)
            current = str(raw_val)
            yield Select(
                [(lbl, val) for lbl, val in options],
                value=current,
                id=widget_id,
                allow_blank=False,
            )
        elif opt.widget in ("input", "input_float", "input_int"):
            placeholder = opt.hint or ""
            yield Input(
                value=str(raw_val) if raw_val not in ("", 0, 0.0) else "",
                placeholder=placeholder,
                id=widget_id,
            )


class OptionsPane(Static):
    """전체 옵션 패널.

    카테고리별 Collapsible 섹션으로 구성된다.
    General은 기본 펼침, 나머지는 기본 접힘.

    ``collect_state()`` 로 현재 위젯 값을 수집하여 ``TuiOptionState`` 를 반환한다.
    ``apply_state()`` 로 외부에서 상태를 주입할 수 있다.
    """

    class PresetAction(Message):
        """프리셋 저장/불러오기 버튼 클릭 시 앱으로 전달하는 메시지."""

        def __init__(self, action: str) -> None:
            super().__init__()
            self.action = action

    DEFAULT_CSS = """
    OptionsPane {
        width: 1fr;
    }
    #options-scroll {
        height: 1fr;
    }
    #preset-bar {
        height: 3;
        padding: 0 1;
        align: left middle;
        border-bottom: solid $accent;
    }
    #preset-bar Button {
        margin-right: 1;
        min-width: 10;
    }
    Collapsible {
        margin-bottom: 0;
    }
    CollapsibleTitle {
        background: $surface;
        color: $accent;
        text-style: bold;
        padding: 0 1;
    }
    """

    def __init__(self, initial_state: TuiOptionState | None = None) -> None:
        super().__init__()
        self._initial = initial_state or default_state()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[bold]옵션[/]", classes="section-title")
            with Horizontal(id="preset-bar"):
                yield Button("저장", id="preset-save", variant="default")
                yield Button("불러오기", id="preset-load", variant="default")
            with ScrollableContainer(id="options-scroll"):
                for i, category in enumerate(CATEGORY_DEFS):
                    # General(첫 번째)만 기본 펼침, 나머지 접힘
                    collapsed = i != 0
                    with Collapsible(title=category.title, collapsed=collapsed):
                        for opt in category.options:
                            yield _OptionRow(opt, self._initial)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ("preset-save", "preset-load"):
            event.stop()
            self.post_message(self.PresetAction(event.button.id))

    def collect_state(self) -> TuiOptionState:
        """현재 위젯 값을 읽어 TuiOptionState를 반환한다."""
        state = default_state()
        for category in CATEGORY_DEFS:
            for opt in category.options:
                wid = _field_id(opt.field)
                if opt.widget == "switch":
                    with contextlib.suppress(Exception):
                        val: object = self.query_one(f"#{wid}", Switch).value
                        setattr(state, opt.field, bool(val))
                elif opt.widget == "select":
                    with contextlib.suppress(Exception):
                        sel = self.query_one(f"#{wid}", Select)
                        if sel.value is not Select.BLANK:
                            setattr(state, opt.field, str(sel.value))
                elif opt.widget == "input":
                    with contextlib.suppress(Exception):
                        setattr(state, opt.field, self.query_one(f"#{wid}", Input).value)
                elif opt.widget == "input_int":
                    with contextlib.suppress(Exception):
                        raw = self.query_one(f"#{wid}", Input).value.strip()
                        if raw:
                            setattr(state, opt.field, int(raw))
                elif opt.widget == "input_float":
                    with contextlib.suppress(Exception):
                        raw = self.query_one(f"#{wid}", Input).value.strip()
                        if raw:
                            setattr(state, opt.field, float(raw))
        return state

    def apply_state(self, state: TuiOptionState) -> None:
        """위젯 값을 state로 업데이트한다 (프리셋 불러오기 등에 사용)."""
        for category in CATEGORY_DEFS:
            for opt in category.options:
                wid = _field_id(opt.field)
                val = getattr(state, opt.field)
                if opt.widget == "switch":
                    with contextlib.suppress(Exception):
                        self.query_one(f"#{wid}", Switch).value = bool(val)
                elif opt.widget == "select":
                    with contextlib.suppress(Exception):
                        self.query_one(f"#{wid}", Select).value = str(val)
                elif opt.widget in ("input", "input_float", "input_int"):
                    with contextlib.suppress(Exception):
                        raw = "" if val in (0, 0.0, "") else str(val)
                        self.query_one(f"#{wid}", Input).value = raw
