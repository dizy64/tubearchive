"""Pipeline 탭 Phase 3 단위 테스트.

_TuiWriter, _TuiLogHandler 동작과 PipelinePane 실행 흐름을 검증한다.
실제 ffmpeg/run_pipeline 호출은 mock으로 대체한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tubearchive.app.tui.screens.pipeline import PipelinePane, _TuiLogHandler, _TuiWriter

# ---------------------------------------------------------------------------
# _TuiWriter 단위 테스트
# ---------------------------------------------------------------------------


def test_tui_writer_single_line() -> None:
    """줄바꿈이 있으면 콜백이 호출된다."""
    lines: list[str] = []
    writer = _TuiWriter(lines.append)
    writer.write("hello\n")
    assert lines == ["hello"]


def test_tui_writer_empty_line_skipped() -> None:
    """빈 줄은 콜백을 호출하지 않는다."""
    lines: list[str] = []
    writer = _TuiWriter(lines.append)
    writer.write("\n")
    assert lines == []


def test_tui_writer_multiple_lines() -> None:
    """한 번에 여러 줄 write 시 각 줄이 개별 콜백으로 호출된다."""
    lines: list[str] = []
    writer = _TuiWriter(lines.append)
    writer.write("a\nb\nc\n")
    assert lines == ["a", "b", "c"]


def test_tui_writer_partial_line_buffered() -> None:
    """줄바꿈 없이 write 한 내용은 버퍼에 보관된다."""
    lines: list[str] = []
    writer = _TuiWriter(lines.append)
    writer.write("partial")
    assert lines == []


def test_tui_writer_flush_drains_buffer() -> None:
    """flush() 시 버퍼에 남은 내용이 콜백으로 방출된다."""
    lines: list[str] = []
    writer = _TuiWriter(lines.append)
    writer.write("no newline")
    writer.flush()
    assert lines == ["no newline"]


def test_tui_writer_strips_trailing_whitespace() -> None:
    """줄 끝 공백은 제거된다."""
    lines: list[str] = []
    writer = _TuiWriter(lines.append)
    writer.write("hello   \n")
    assert lines == ["hello"]


# ---------------------------------------------------------------------------
# _TuiLogHandler 단위 테스트
# ---------------------------------------------------------------------------


def test_tui_log_handler_emits_formatted_message() -> None:
    """로그 레코드가 포맷돼 콜백으로 전달된다."""
    messages: list[str] = []
    handler = _TuiLogHandler(messages.append)
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="hello world",
        args=(),
        exc_info=None,
    )
    handler.emit(record)
    assert len(messages) == 1
    assert "hello world" in messages[0]


def test_tui_log_handler_callback_exception_does_not_propagate() -> None:
    """콜백에서 예외가 발생해도 emit()이 전파하지 않는다."""

    def bad_callback(msg: str) -> None:
        raise RuntimeError("callback error")

    handler = _TuiLogHandler(bad_callback)
    record = logging.LogRecord(
        name="test",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="test",
        args=(),
        exc_info=None,
    )
    # handleError를 호출하지만 예외가 전파되지 않아야 한다
    handler.emit(record)  # should not raise


# ---------------------------------------------------------------------------
# PipelinePane 통합 테스트 (실제 run_pipeline mock)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pipeline_run_button_disabled_initially() -> None:
    """파일 선택 전 실행 버튼이 비활성화 상태인지 확인한다."""
    from textual.widgets import Button

    from tubearchive.app.tui.app import TubeArchiveApp

    app = TubeArchiveApp()
    async with app.run_test(headless=True, size=(120, 40)):
        pane = app.query_one(PipelinePane)
        btn = pane.query_one("#run-button", Button)
        assert btn.disabled is True


@pytest.mark.asyncio
async def test_pipeline_on_done_updates_state() -> None:
    """_on_pipeline_done 호출 시 완료 상태로 전환된다."""
    from textual.widgets import Button

    from tubearchive.app.tui.app import TubeArchiveApp
    from tubearchive.app.tui.widgets.progress_panel import ProgressPanel

    output_path = Path("/tmp/merged.mp4")

    app = TubeArchiveApp()
    async with app.run_test(headless=True, size=(120, 40)):
        pane = app.query_one(PipelinePane)
        pane._pipeline_active = True
        pane._show_progress_view()

        pane._on_pipeline_done(output_path)

        panel = pane.query_one(ProgressPanel)
        assert panel._panel_active is False
        btn = pane.query_one("#run-button", Button)
        assert btn.disabled is False
        assert str(btn.label) == "다시 실행"


@pytest.mark.asyncio
async def test_pipeline_on_error_updates_state() -> None:
    """_on_pipeline_error 호출 시 오류 상태로 전환된다."""
    from textual.widgets import Button

    from tubearchive.app.tui.app import TubeArchiveApp
    from tubearchive.app.tui.widgets.progress_panel import ProgressPanel

    app = TubeArchiveApp()
    async with app.run_test(headless=True, size=(120, 40)):
        pane = app.query_one(PipelinePane)
        pane._pipeline_active = True
        pane._show_progress_view()

        pane._on_pipeline_error("파일을 찾을 수 없습니다")

        panel = pane.query_one(ProgressPanel)
        assert panel._panel_active is False
        btn = pane.query_one("#run-button", Button)
        assert btn.disabled is False
        assert str(btn.label) == "다시 실행"
