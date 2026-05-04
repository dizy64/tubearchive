# tests/unit/test_file_progress_panel.py
from __future__ import annotations

import pytest

from tubearchive.app.cli.context import (
    FileDoneEvent,
    FileStartEvent,
)


@pytest.mark.asyncio
async def test_file_progress_panel_shows_file_rows_on_start() -> None:
    """FileStartEvent 수신 시 파일 행이 추가된다."""
    from textual.app import App, ComposeResult

    from tubearchive.app.tui.widgets.file_progress_panel import FileProgressPanel

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileProgressPanel(id="panel")

    async with TestApp().run_test(headless=True, size=(120, 40)) as pilot:
        panel = pilot.app.query_one(FileProgressPanel)
        panel.handle_event(FileStartEvent(filename="a.mov", file_index=0, total_files=2))
        panel.handle_event(FileStartEvent(filename="b.mov", file_index=1, total_files=2))
        await pilot.pause()

        from tubearchive.app.tui.widgets.file_progress_panel import _FileRow

        rows = panel.query(_FileRow)
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_file_progress_panel_marks_done() -> None:
    """FileDoneEvent 수신 시 해당 행이 완료 상태로 전환된다."""
    from textual.app import App, ComposeResult

    from tubearchive.app.tui.widgets.file_progress_panel import FileProgressPanel

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileProgressPanel(id="panel")

    async with TestApp().run_test(headless=True, size=(120, 40)) as pilot:
        panel = pilot.app.query_one(FileProgressPanel)
        panel.handle_event(FileStartEvent(filename="a.mov", file_index=0, total_files=1))
        panel.handle_event(FileDoneEvent(filename="a.mov", success=True))
        await pilot.pause()

        from tubearchive.app.tui.widgets.file_progress_panel import _FileRow

        row = panel.query(_FileRow).first()
        assert row._status == "done"


@pytest.mark.asyncio
async def test_file_progress_panel_finish_updates_status() -> None:
    """finish() 호출 시 상태 라벨이 완료로 갱신된다."""
    from textual.app import App, ComposeResult

    from tubearchive.app.tui.widgets.file_progress_panel import FileProgressPanel

    class TestApp(App):
        def compose(self) -> ComposeResult:
            yield FileProgressPanel(id="panel")

    async with TestApp().run_test(headless=True, size=(120, 40)) as pilot:
        panel = pilot.app.query_one(FileProgressPanel)
        panel.finish("/output/merged.mp4")
        await pilot.pause()
        # 예외 없이 완료되면 OK
