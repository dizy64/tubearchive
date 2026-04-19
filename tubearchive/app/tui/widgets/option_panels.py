"""옵션 패널 위젯.

카테고리별 ``Collapsible`` 섹션을 구성하고, 현재 위젯 값을 읽어
``TuiOptionState`` 를 반환하는 ``collect_state()`` 를 제공한다.
"""

from __future__ import annotations

import contextlib

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widgets import Collapsible, Input, Label, Select, Static, Switch

from tubearchive.app.tui.models import (
    CATEGORY_DEFS,
    CategoryDef,
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
        margin-bottom: 1;
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


class _CategorySection(Collapsible):
    """카테고리 하나를 감싸는 Collapsible."""

    def __init__(self, category: CategoryDef, initial: TuiOptionState) -> None:
        super().__init__(title=category.title, collapsed=category.collapsed)
        self._category = category
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical():
            for opt in self._category.options:
                yield _OptionRow(opt, self._initial)


class OptionsPane(Static):
    """전체 옵션 패널.

    ``collect_state()`` 로 현재 위젯 값을 수집하여 ``TuiOptionState`` 를 반환한다.
    """

    DEFAULT_CSS = """
    OptionsPane {
        width: 1fr;
    }
    #options-scroll {
        height: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._initial = default_state()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("[bold]옵션[/]", classes="section-title")
            with ScrollableContainer(id="options-scroll"):
                for category in CATEGORY_DEFS:
                    yield _CategorySection(category, self._initial)

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
