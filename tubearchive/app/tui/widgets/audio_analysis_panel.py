"""외부 오디오 사전 분석 결과 모달 패널."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from tubearchive.domain.media.audio_sync import ExternalAudioSegment
from tubearchive.shared.validators import parse_finite_float

_LOW_CONFIDENCE_THRESHOLD = 0.1
_NAME_WIDTH = 32


class _SegmentRow(Horizontal):
    """클립 1개의 분석 결과 + 수동 보정 입력 행."""

    DEFAULT_CSS = """
    _SegmentRow {
        height: 3;
        padding: 0 1;
        align: left middle;
    }
    _SegmentRow.low-confidence {
        background: $error 15%;
    }
    _SegmentRow .seg-name {
        width: 32;
        color: $text;
    }
    _SegmentRow .seg-offset {
        width: 12;
        color: $text-muted;
        text-align: right;
    }
    _SegmentRow .seg-conf {
        width: 10;
        text-align: right;
    }
    _SegmentRow .seg-conf.low {
        color: $error;
    }
    _SegmentRow .seg-conf.ok {
        color: $success;
    }
    _SegmentRow .seg-input {
        width: 16;
        margin-left: 2;
    }
    """

    def __init__(self, filename: str, seg: ExternalAudioSegment) -> None:
        super().__init__()
        self._filename = filename
        self._seg = seg
        self._low = seg.confidence < _LOW_CONFIDENCE_THRESHOLD
        if self._low:
            self.add_class("low-confidence")

    @property
    def filename(self) -> str:
        return self._filename

    def compose(self) -> ComposeResult:
        name = self._filename
        if len(name) > _NAME_WIDTH:
            name = "…" + name[-(_NAME_WIDTH - 1) :]
        conf_class = "low" if self._low else "ok"
        conf_icon = "⚠" if self._low else "✓"
        yield Label(name, classes="seg-name")
        yield Label(f"{self._seg.start_seconds:8.3f}s", classes="seg-offset")
        yield Label(
            f"{conf_icon} {self._seg.confidence:.3f}",
            classes=f"seg-conf {conf_class}",
        )
        placeholder = "예: 5.0 (느릴때) / -3.0 (빠를때)" if self._low else "예: 1.0 / -1.0"
        yield Input(
            placeholder=placeholder,
            classes="seg-input",
        )

    def get_adjustment(self) -> tuple[str, float] | None:
        """입력된 보정값을 반환한다. 비어있으면 None."""
        inp = self.query_one(Input)
        raw = inp.value.strip()
        if not raw:
            return None
        val = parse_finite_float(raw, "보정값")
        return self._filename, val


class AudioAnalysisPanel(ModalScreen[str | None]):
    """외부 오디오 사전 분석 결과를 표시하고 수동 보정값을 입력받는 모달 화면.

    dismiss(result)로 닫힐 때 result는 "패턴:초, ..." 형식의 문자열.
    취소 시 None, 빈 보정값 적용 시 빈 문자열을 반환한다.
    """

    DEFAULT_CSS = """
    AudioAnalysisPanel {
        align: center middle;
    }
    #panel-container {
        width: 90;
        height: auto;
        max-height: 80vh;
        background: $surface;
        border: heavy $accent;
        padding: 1 2;
    }
    #panel-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #panel-hint {
        color: $text-muted;
        margin-bottom: 1;
    }
    #panel-legend {
        color: $text-muted;
        margin-bottom: 1;
    }
    #col-header {
        height: 1;
        padding: 0 1;
        background: $primary 20%;
    }
    #col-header .h-name { width: 32; text-style: bold; }
    #col-header .h-offset { width: 12; text-align: right; text-style: bold; }
    #col-header .h-conf { width: 10; text-align: right; text-style: bold; }
    #col-header .h-adj { width: 18; margin-left: 2; text-style: bold; }
    #segment-scroll {
        max-height: 24;
        border: solid $surface-darken-1;
    }
    #panel-footer {
        margin-top: 1;
        align: right middle;
        height: 3;
    }
    #apply-btn { margin-right: 1; }
    """

    def __init__(
        self,
        segments: dict[Path, ExternalAudioSegment],
    ) -> None:
        super().__init__()
        self._segments = segments

    def compose(self) -> ComposeResult:
        low_count = sum(
            1 for seg in self._segments.values() if seg.confidence < _LOW_CONFIDENCE_THRESHOLD
        )
        with Vertical(id="panel-container"):
            yield Label("외부 오디오 사전 분석 결과", id="panel-title")
            if low_count:
                yield Static(
                    f"[yellow]⚠ 신뢰도 낮은 클립 {low_count}개 — 수동 보정 권장[/]",
                    id="panel-hint",
                )
            else:
                yield Static(
                    "[green]✓ 모든 클립 자동 동기화 완료[/]",
                    id="panel-hint",
                )
            yield Static(
                "[dim]보정값: 양수 → 소리 느릴 때 앞으로 당김 / 음수 → 소리 빠를 때 뒤로 밀기[/]",
                id="panel-legend",
            )
            with Horizontal(id="col-header"):
                yield Label("파일명", classes="h-name")
                yield Label("오프셋", classes="h-offset")
                yield Label("신뢰도", classes="h-conf")
                yield Label("수동 보정(초)", classes="h-adj")
            with ScrollableContainer(id="segment-scroll"):
                for path, seg in self._segments.items():
                    yield _SegmentRow(path.name, seg)
            with Horizontal(id="panel-footer"):
                yield Button("보정값 적용", id="apply-btn", variant="primary")
                yield Button("닫기", id="cancel-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-btn":
            parts: list[str] = []
            for row in self.query(_SegmentRow):
                try:
                    adj = row.get_adjustment()
                except ValueError as exc:
                    row.query_one(Input).focus()
                    self.notify(str(exc), severity="error")
                    return
                if adj is not None:
                    pattern, delta = adj
                    parts.append(f"{pattern}:{delta:g}")
            self.dismiss(", ".join(parts))
        elif event.button.id == "cancel-btn":
            self.dismiss(None)
