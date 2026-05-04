from __future__ import annotations

from tubearchive.app.cli.context import (
    FileDoneEvent,
    FileProgressEvent,
    FileStartEvent,
    PipelineContext,
    ProgressEvent,
)
from tubearchive.shared.progress import ProgressInfo


def test_file_start_event_is_frozen() -> None:
    e = FileStartEvent(filename="a.mov", file_index=0, total_files=3)
    assert e.filename == "a.mov"
    assert e.file_index == 0
    assert e.total_files == 3
    try:
        e.filename = "b.mov"  # type: ignore[misc]
        raise AssertionError("should be frozen")
    except (AttributeError, TypeError):
        pass


def test_file_progress_event_carries_progress_info() -> None:
    info = ProgressInfo(percent=45, current_time=10.0, total_duration=100.0, fps=29.0)
    e = FileProgressEvent(filename="a.mov", info=info)
    assert e.info.percent == 45


def test_file_done_event_success_and_failure() -> None:
    ok = FileDoneEvent(filename="a.mov", success=True)
    fail = FileDoneEvent(filename="a.mov", success=False)
    assert ok.success is True
    assert fail.success is False


def test_pipeline_context_defaults_none() -> None:
    ctx = PipelineContext()
    assert ctx.notifier is None
    assert ctx.on_progress is None


def test_pipeline_context_accepts_callback() -> None:
    events: list[ProgressEvent] = []
    ctx = PipelineContext(on_progress=events.append)
    e = FileStartEvent(filename="a.mov", file_index=0, total_files=1)
    assert ctx.on_progress is not None
    ctx.on_progress(e)
    assert events == [e]
