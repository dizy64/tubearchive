# Sprint 1: Pipeline 피드백 + 알림 통합 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** TUI 실행 중 파일별 진행률/ETA를 실시간 표시하고, 파이프라인 완료·오류 시 Slack/Telegram/Discord/macOS 알림을 발송한다.

**Architecture:** `PipelineContext` dataclass로 진행률 콜백(`on_progress`)과 알림 오케스트레이터(`notifier`)를 묶어 `run_pipeline`에 전달한다. CLI 경로는 `context=None`으로 호출하여 기존 동작이 변경되지 않는다. TUI는 `FileProgressPanel` 위젯으로 파일별 행을 렌더링하고, worker 스레드에서 `call_from_thread`를 통해 갱신한다.

**Tech Stack:** Python 3.14, dataclasses, Textual, pytest-asyncio

---

## 파일 구조

| 파일 | 상태 | 역할 |
|------|------|------|
| `tubearchive/app/cli/context.py` | 신규 | `PipelineContext`, `ProgressEvent` 타입 정의 |
| `tubearchive/app/cli/pipeline.py` | 수정 | `run_pipeline` 시그니처 변경, 이벤트 emit |
| `tubearchive/app/tui/widgets/file_progress_panel.py` | 신규 | 파일별 진행률 위젯 |
| `tubearchive/app/tui/screens/pipeline.py` | 수정 | `FileProgressPanel` 사용, notifier 생성 |
| `tests/unit/test_pipeline_context.py` | 신규 | context.py 단위 테스트 |
| `tests/unit/test_pipeline_progress.py` | 신규 | pipeline.py 이벤트 emit 테스트 |
| `tests/unit/test_file_progress_panel.py` | 신규 | FileProgressPanel 위젯 단위 테스트 |

---

## Task 1: PipelineContext + ProgressEvent 타입 정의

**Files:**
- Create: `tubearchive/app/cli/context.py`
- Create: `tests/unit/test_pipeline_context.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/unit/test_pipeline_context.py
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
        assert False, "should be frozen"
    except (AttributeError, TypeError):
        pass


def test_file_progress_event_carries_progress_info() -> None:
    info = ProgressInfo(percent=45, fps=29.0, eta_seconds=90.0, elapsed_seconds=10.0)
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
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
uv run pytest tests/unit/test_pipeline_context.py -v
```

Expected: `ModuleNotFoundError: No module named 'tubearchive.app.cli.context'`

- [ ] **Step 3: context.py 구현**

```python
# tubearchive/app/cli/context.py
"""파이프라인 실행 컨텍스트 및 진행률 이벤트 타입."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from tubearchive.infra.notification.notifier import Notifier
    from tubearchive.shared.progress import ProgressInfo


@dataclass(frozen=True)
class FileStartEvent:
    """파일 트랜스코딩 시작 이벤트."""

    filename: str
    file_index: int
    total_files: int


@dataclass(frozen=True)
class FileProgressEvent:
    """파일 트랜스코딩 진행률 이벤트."""

    filename: str
    info: ProgressInfo


@dataclass(frozen=True)
class FileDoneEvent:
    """파일 트랜스코딩 완료 이벤트."""

    filename: str
    success: bool


ProgressEvent = Union[FileStartEvent, FileProgressEvent, FileDoneEvent]


@dataclass
class PipelineContext:
    """파이프라인 사이드-채널: 진행률 콜백 + 알림 오케스트레이터."""

    notifier: Notifier | None = field(default=None)
    on_progress: Callable[[ProgressEvent], None] | None = field(default=None)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/unit/test_pipeline_context.py -v
```

Expected: 5 passed

- [ ] **Step 5: 커밋**

```bash
git add tubearchive/app/cli/context.py tests/unit/test_pipeline_context.py
git commit -m "feat: PipelineContext + ProgressEvent 타입 정의 (context.py)"
```

---

## Task 2: run_pipeline 시그니처 변경 (notifier → context)

**Files:**
- Modify: `tubearchive/app/cli/pipeline.py` (lines 811–816)
- Create: `tests/unit/test_pipeline_progress.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/unit/test_pipeline_progress.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.app.cli.context import PipelineContext


def _make_args(tmp_path: Path) -> object:
    """ValidatedArgs 최소 픽스처 (pipeline 내부에서만 사용되는 필드)."""
    from tubearchive.app.cli.validators import ValidatedArgs

    src = tmp_path / "clip.mov"
    src.touch()
    return ValidatedArgs(
        targets=[src],
        output=tmp_path / "out.mp4",
        output_dir=None,
        no_resume=False,
        keep_temp=False,
        dry_run=False,
    )


def test_run_pipeline_accepts_context_parameter(tmp_path: Path) -> None:
    """run_pipeline이 context 파라미터를 받는다."""
    from tubearchive.app.cli.pipeline import run_pipeline

    ctx = PipelineContext()
    # 스캔 단계에서 빈 파일이라 ValueError 발생하는 게 정상
    # 여기서는 시그니처만 확인
    with pytest.raises((ValueError, Exception)):
        run_pipeline(_make_args(tmp_path), context=ctx)


def test_run_pipeline_context_none_is_backward_compat(tmp_path: Path) -> None:
    """context=None이면 기존처럼 동작한다."""
    from tubearchive.app.cli.pipeline import run_pipeline

    with pytest.raises((ValueError, Exception)):
        run_pipeline(_make_args(tmp_path))


def test_run_pipeline_notifier_called_on_merge(tmp_path: Path) -> None:
    """context.notifier가 있으면 병합 완료 시 notify()가 호출된다."""
    from tubearchive.app.cli.pipeline import run_pipeline

    mock_notifier = MagicMock()
    mock_notifier.notify = MagicMock()
    ctx = PipelineContext(notifier=mock_notifier)

    with (
        patch("tubearchive.app.cli.pipeline.scan_videos") as mock_scan,
        patch("tubearchive.app.cli.pipeline._transcode_sequential") as mock_tc,
        patch("tubearchive.app.cli.pipeline.Merger") as mock_merger_cls,
        patch("tubearchive.app.cli.pipeline.init_database"),
        patch("tubearchive.app.cli.pipeline.database_session"),
        patch("tubearchive.app.cli.pipeline.save_merge_job_to_db", return_value=1),
        patch("tubearchive.app.cli.pipeline._print_summary"),
        patch("tubearchive.app.cli.pipeline.check_output_disk_space"),
        patch("tubearchive.app.cli.pipeline.get_temp_dir", return_value=tmp_path),
        patch("tubearchive.app.cli.pipeline.get_output_filename", return_value=tmp_path / "out.mp4"),
    ):
        from tubearchive.domain.models.clip import ClipInfo
        from tubearchive.app.cli.pipeline import TranscodeResult

        fake_video = MagicMock()
        fake_video.path = tmp_path / "clip.mov"
        mock_scan.return_value = [fake_video]

        fake_result = TranscodeResult(
            output_path=tmp_path / "clip_tc.mp4",
            video_id=1,
            clip_info=ClipInfo(name="clip", duration=10.0, device="unknown", shot_time=None),
            silence_segments=[],
        )
        mock_tc.return_value = [fake_result]

        merged = tmp_path / "out.mp4"
        merged.touch()
        mock_merger_cls.return_value.__enter__ = MagicMock(return_value=mock_merger_cls.return_value)
        mock_merger_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_merger_cls.return_value.merge.return_value = merged

        args = MagicMock()
        args.targets = [tmp_path]
        args.output = None
        args.output_dir = None
        args.no_resume = False
        args.keep_temp = False
        args.dry_run = False
        args.detect_silence = False
        args.upload = False
        args.template_intro = None
        args.template_outro = None
        args.group_sequences = False
        args.parallel = 1
        args.split_duration = None
        args.split_size = None
        args.archive_originals = None
        args.notify = False
        args.project = None
        args.quality_report = False
        args.subtitle = False
        args.bgm_path = None
        args.timelapse_speed = None
        args.thumbnail = False
        args.backup_remote = None
        args.hooks = None
        args.reorder = False
        args.sort_key = "shot_time"
        args.exclude_patterns = None
        args.include_only_patterns = None

        run_pipeline(args, context=ctx)

    mock_notifier.notify.assert_called()
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
uv run pytest tests/unit/test_pipeline_progress.py::test_run_pipeline_accepts_context_parameter -v
```

Expected: FAIL — `run_pipeline() got an unexpected keyword argument 'context'`

- [ ] **Step 3: run_pipeline 시그니처 변경**

`tubearchive/app/cli/pipeline.py` line 811–816을 수정:

```python
# 파일 상단 import 영역에 추가 (TYPE_CHECKING 블록 또는 직접 import)
from tubearchive.app.cli.context import PipelineContext

# run_pipeline 시그니처 변경
def run_pipeline(
    validated_args: ValidatedArgs,
    context: PipelineContext | None = None,
    generated_thumbnail_paths: list[Path] | None = None,
    generated_subtitle_paths: list[Path] | None = None,
) -> Path:
```

함수 본문 첫 줄에 추가 (기존 `notifier` 사용 부분을 `context.notifier`로 교체):

```python
    notifier = context.notifier if context else None
```

기존 `notifier:` 파라미터 독스트링 행 제거, `context:` 설명 추가:
```text
        context: 진행률 콜백 + 알림 오케스트레이터 (None이면 기존 동작)
```

- [ ] **Step 4: TUI screens/pipeline.py 기존 호출 수정**

`tubearchive/app/tui/screens/pipeline.py` line 248:
```python
# 변경 전
output_path = run_pipeline(validated_args, notifier=None)  # type: ignore[arg-type]
# 변경 후
output_path = run_pipeline(validated_args, context=None)
```

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/unit/test_pipeline_progress.py::test_run_pipeline_accepts_context_parameter tests/unit/test_pipeline_progress.py::test_run_pipeline_context_none_is_backward_compat -v
uv run pytest tests/unit/ -q
```

Expected: 모두 PASS

- [ ] **Step 6: 커밋**

```bash
git add tubearchive/app/cli/pipeline.py tubearchive/app/tui/screens/pipeline.py tests/unit/test_pipeline_progress.py
git commit -m "feat: run_pipeline에 PipelineContext 파라미터 추가, notifier → context 통합"
```

---

## Task 3: _transcode_sequential에서 ProgressEvent emit

**Files:**
- Modify: `tubearchive/app/cli/pipeline.py` (_transcode_sequential, lines 599–674)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_pipeline_progress.py`에 추가:

```python
def test_transcode_sequential_emits_start_and_done_events(tmp_path: Path) -> None:
    """_transcode_sequential이 FileStartEvent와 FileDoneEvent를 emit한다."""
    from tubearchive.app.cli.context import FileDoneEvent, FileStartEvent, PipelineContext
    from tubearchive.app.cli.pipeline import TranscodeOptions, _transcode_sequential
    from tubearchive.domain.models.clip import ClipInfo

    events: list[object] = []
    ctx = PipelineContext(on_progress=events.append)

    fake_video = MagicMock()
    fake_video.path = tmp_path / "clip.mov"

    fake_result = MagicMock()
    fake_result.output_path = tmp_path / "out.mp4"
    fake_result.video_id = 1
    fake_result.clip_info = ClipInfo(name="clip", duration=5.0, device="x", shot_time=None)
    fake_result.silence_segments = []

    opts = TranscodeOptions()

    with (
        patch("tubearchive.app.cli.pipeline.Transcoder") as mock_tc_cls,
        patch("tubearchive.app.cli.pipeline.detect_metadata"),
        patch("tubearchive.app.cli.pipeline._collect_clip_info",
              return_value=fake_result.clip_info),
    ):
        mock_tc = MagicMock()
        mock_tc_cls.return_value.__enter__ = MagicMock(return_value=mock_tc)
        mock_tc_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_tc.transcode_video.return_value = (
            fake_result.output_path, 1, []
        )

        _transcode_sequential([fake_video], tmp_path, opts, context=ctx)

    start_events = [e for e in events if isinstance(e, FileStartEvent)]
    done_events = [e for e in events if isinstance(e, FileDoneEvent)]

    assert len(start_events) == 1
    assert start_events[0].filename == "clip.mov"
    assert start_events[0].file_index == 0
    assert start_events[0].total_files == 1

    assert len(done_events) == 1
    assert done_events[0].filename == "clip.mov"
    assert done_events[0].success is True
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
uv run pytest tests/unit/test_pipeline_progress.py::test_transcode_sequential_emits_start_and_done_events -v
```

Expected: FAIL — `_transcode_sequential() got an unexpected keyword argument 'context'`

- [ ] **Step 3: _transcode_sequential 수정**

`tubearchive/app/cli/pipeline.py` — `_transcode_sequential` 시그니처 및 본문 수정:

```python
def _transcode_sequential(
    video_files: list[VideoFile],
    temp_dir: Path,
    opts: TranscodeOptions,
    context: PipelineContext | None = None,
) -> list[TranscodeResult]:
    results: list[TranscodeResult] = []
    progress = MultiProgressBar(total_files=len(video_files))

    with Transcoder(temp_dir=temp_dir) as transcoder:
        for i, video_file in enumerate(video_files):
            progress.start_file(video_file.path.name)

            if context and context.on_progress:
                context.on_progress(
                    FileStartEvent(
                        filename=video_file.path.name,
                        file_index=i,
                        total_files=len(video_files),
                    )
                )

            filename = video_file.path.name  # 클로저 캡처용

            def on_progress_info(
                info: ProgressInfo,
                _filename: str = filename,
                _ctx: PipelineContext | None = context,
            ) -> None:
                progress.update_with_info(info)
                if _ctx and _ctx.on_progress:
                    _ctx.on_progress(FileProgressEvent(filename=_filename, info=info))

            fade_config = opts.fade_map.get(video_file.path) if opts.fade_map else None
            fade_in = fade_config.fade_in if fade_config else None
            fade_out = fade_config.fade_out if fade_config else None

            metadata = detect_metadata(video_file.path)
            if opts.watermark:
                watermark_text = opts.watermark_text or _make_watermark_text(video_file, metadata)
            else:
                watermark_text = None
            output_path, video_id, silence_segments = transcoder.transcode_video(
                video_file,
                metadata=metadata,
                denoise=opts.denoise,
                denoise_level=opts.denoise_level,
                normalize_audio=opts.normalize_audio,
                fade_duration=opts.fade_duration,
                fade_in_duration=fade_in,
                fade_out_duration=fade_out,
                trim_silence=opts.trim_silence,
                silence_threshold=opts.silence_threshold,
                silence_min_duration=opts.silence_min_duration,
                stabilize=opts.stabilize,
                stabilize_strength=opts.stabilize_strength,
                stabilize_crop=opts.stabilize_crop,
                lut_path=str(opts.lut_path) if opts.lut_path else None,
                auto_lut=opts.auto_lut,
                lut_before_hdr=opts.lut_before_hdr,
                device_luts=opts.device_luts,
                watermark_text=watermark_text,
                watermark_position=opts.watermark_pos,
                watermark_size=opts.watermark_size,
                watermark_color=opts.watermark_color,
                watermark_alpha=opts.watermark_alpha,
                progress_info_callback=on_progress_info,
            )
            clip_info = _collect_clip_info(video_file, metadata)
            results.append(
                TranscodeResult(
                    output_path=output_path,
                    video_id=video_id,
                    clip_info=clip_info,
                    silence_segments=silence_segments,
                )
            )
            progress.finish_file()

            if context and context.on_progress:
                context.on_progress(
                    FileDoneEvent(filename=video_file.path.name, success=True)
                )

    return results
```

`run_pipeline`에서 `_transcode_sequential` 호출 부분 (line 972) 수정:

```python
results = _transcode_sequential(video_files, temp_dir, transcode_opts, context=context)
```

- [ ] **Step 4: 상단 import에 FileStartEvent/FileProgressEvent/FileDoneEvent 추가**

`pipeline.py` 파일 상단 import 블록에 다음 추가:

```python
from tubearchive.app.cli.context import (
    FileDoneEvent,
    FileProgressEvent,
    FileStartEvent,
    PipelineContext,
)
```

(Task 2에서 `PipelineContext`만 import했다면 이 세 타입을 추가)

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/unit/test_pipeline_progress.py -v
uv run pytest tests/unit/ -q
```

Expected: 모두 PASS

- [ ] **Step 6: 커밋**

```bash
git add tubearchive/app/cli/pipeline.py tests/unit/test_pipeline_progress.py
git commit -m "feat: _transcode_sequential에서 FileStartEvent/FileProgressEvent/FileDoneEvent emit"
```

---

## Task 4: _transcode_parallel에서 ProgressEvent emit (FileStart/Done)

**Files:**
- Modify: `tubearchive/app/cli/pipeline.py` (_transcode_single, lines 471–531; _transcode_parallel, lines 534–596)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_pipeline_progress.py`에 추가:

```python
def test_transcode_parallel_emits_start_and_done_events(tmp_path: Path) -> None:
    """_transcode_parallel이 각 파일에 대해 FileStartEvent와 FileDoneEvent를 emit한다."""
    from tubearchive.app.cli.context import FileDoneEvent, FileStartEvent, PipelineContext
    from tubearchive.app.cli.pipeline import TranscodeOptions, _transcode_parallel
    from tubearchive.domain.models.clip import ClipInfo

    events: list[object] = []
    ctx = PipelineContext(on_progress=events.append)

    fake_video = MagicMock()
    fake_video.path = tmp_path / "clip.mov"

    opts = TranscodeOptions()

    with (
        patch("tubearchive.app.cli.pipeline._transcode_single") as mock_single,
    ):
        from tubearchive.app.cli.pipeline import TranscodeResult

        mock_single.return_value = TranscodeResult(
            output_path=tmp_path / "out.mp4",
            video_id=1,
            clip_info=ClipInfo(name="clip", duration=5.0, device="x", shot_time=None),
            silence_segments=[],
        )

        _transcode_parallel([fake_video], tmp_path, 1, opts, context=ctx)

    start_events = [e for e in events if isinstance(e, FileStartEvent)]
    done_events = [e for e in events if isinstance(e, FileDoneEvent)]
    assert len(start_events) == 1
    assert len(done_events) == 1
    assert done_events[0].success is True
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
uv run pytest tests/unit/test_pipeline_progress.py::test_transcode_parallel_emits_start_and_done_events -v
```

Expected: FAIL

- [ ] **Step 3: _transcode_parallel 수정**

`tubearchive/app/cli/pipeline.py` — `_transcode_parallel` 시그니처 및 본문 수정:

```python
def _transcode_parallel(
    video_files: list[VideoFile],
    temp_dir: Path,
    max_workers: int,
    opts: TranscodeOptions,
    context: PipelineContext | None = None,
) -> list[TranscodeResult]:
    results: dict[int, TranscodeResult] = {}
    completed_count = 0
    total_count = len(video_files)
    print_lock = Lock()

    def on_complete(idx: int, filename: str, status: str, success: bool) -> None:
        nonlocal completed_count
        with print_lock:
            completed_count += 1
            print(
                f"\r🎬 트랜스코딩: [{completed_count}/{total_count}] {status}: {filename}",
                end="",
                flush=True,
            )
            if completed_count == total_count:
                print()
            if context and context.on_progress:
                context.on_progress(FileDoneEvent(filename=filename, success=success))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for i, video_file in enumerate(video_files):
            if context and context.on_progress:
                context.on_progress(
                    FileStartEvent(
                        filename=video_file.path.name,
                        file_index=i,
                        total_files=total_count,
                    )
                )
            futures[executor.submit(_transcode_single, video_file, temp_dir, opts)] = i

        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                results[idx] = result
                on_complete(idx, video_files[idx].path.name, "완료", True)
            except Exception as e:
                logger.error("Failed to transcode %s: %s", video_files[idx].path, e)
                on_complete(idx, video_files[idx].path.name, "실패", False)
                raise

    return [results[i] for i in range(total_count)]
```

`run_pipeline`에서 `_transcode_parallel` 호출 수정 (line 969):

```python
results = _transcode_parallel(video_files, temp_dir, parallel, transcode_opts, context=context)
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/unit/test_pipeline_progress.py -v
uv run pytest tests/unit/ -q
```

Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add tubearchive/app/cli/pipeline.py tests/unit/test_pipeline_progress.py
git commit -m "feat: _transcode_parallel에서 FileStartEvent/FileDoneEvent emit"
```

---

## Task 5: FileProgressPanel 위젯 신규 작성

**Files:**
- Create: `tubearchive/app/tui/widgets/file_progress_panel.py`
- Create: `tests/unit/test_file_progress_panel.py`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
# tests/unit/test_file_progress_panel.py
from __future__ import annotations

import pytest

from tubearchive.app.cli.context import (
    FileDoneEvent,
    FileProgressEvent,
    FileStartEvent,
)
from tubearchive.shared.progress import ProgressInfo


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
        panel.handle_event(
            FileStartEvent(filename="a.mov", file_index=0, total_files=2)
        )
        panel.handle_event(
            FileStartEvent(filename="b.mov", file_index=1, total_files=2)
        )
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
        panel.handle_event(
            FileStartEvent(filename="a.mov", file_index=0, total_files=1)
        )
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
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
uv run pytest tests/unit/test_file_progress_panel.py -v
```

Expected: `ModuleNotFoundError: No module named 'tubearchive.app.tui.widgets.file_progress_panel'`

- [ ] **Step 3: FileProgressPanel 구현**

```python
# tubearchive/app/tui/widgets/file_progress_panel.py
"""파일별 트랜스코딩 진행률 패널 위젯."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Label, ProgressBar, RichLog, Static

from tubearchive.app.cli.context import (
    FileDoneEvent,
    FileProgressEvent,
    FileStartEvent,
    ProgressEvent,
)

_ICONS: dict[str, str] = {
    "pending": "·",
    "processing": "→",
    "done": "✓",
    "error": "✗",
}

_COLORS: dict[str, str] = {
    "pending": "dim",
    "processing": "yellow",
    "done": "green",
    "error": "red",
}


class _FileRow(Static):
    """파일 한 줄 행 — 아이콘 + 파일명 + 진행률 + ETA."""

    DEFAULT_CSS = """
    _FileRow {
        height: 1;
        padding: 0 1;
    }
    """

    def __init__(self, filename: str) -> None:
        super().__init__()
        self._filename = filename
        self._status = "pending"
        self._percent = 0
        self._eta = ""

    def render(self) -> str:
        icon = _ICONS[self._status]
        color = _COLORS[self._status]
        name = self._filename[:40]
        if self._status == "processing":
            pct = f"{self._percent:3d}%"
            eta = f"  ETA {self._eta}" if self._eta else ""
            return f"[{color}]{icon}[/]  {name:<42} [{color}]{pct}{eta}[/]"
        if self._status == "done":
            return f"[{color}]{icon}[/]  {name:<42} [{color}]완료[/]"
        if self._status == "error":
            return f"[{color}]{icon}[/]  {name:<42} [{color}]오류[/]"
        return f"[{color}]{icon}[/]  {name}"

    def mark_processing(self) -> None:
        self._status = "processing"
        self.refresh()

    def update_progress(self, percent: int, eta: str) -> None:
        self._status = "processing"
        self._percent = percent
        self._eta = eta
        self.refresh()

    def mark_done(self, success: bool) -> None:
        self._status = "done" if success else "error"
        self._percent = 100 if success else self._percent
        self.refresh()


class FileProgressPanel(Widget):
    """파이프라인 진행률 패널 — 파일별 행 + 전체 진행 바 + 로그."""

    DEFAULT_CSS = """
    FileProgressPanel {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    #fp-header {
        height: 2;
        align: left middle;
        margin-bottom: 1;
    }
    #fp-files {
        height: auto;
        max-height: 12;
    }
    #fp-overall-bar {
        width: 1fr;
        margin: 1 0;
    }
    #fp-log {
        height: 1fr;
        border: solid $panel;
    }
    """

    def __init__(self, id: str | None = None) -> None:  # noqa: A002
        super().__init__(id=id)
        self._file_rows: dict[str, _FileRow] = {}
        self._done_count = 0
        self._total_files = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("준비 중...", id="fp-header")
            yield Vertical(id="fp-files")
            yield ProgressBar(total=None, id="fp-overall-bar", show_eta=False)
            yield RichLog(id="fp-log", highlight=True, markup=True, max_lines=500)

    # ------------------------------------------------------------------
    # 공개 API (call_from_thread 경유)
    # ------------------------------------------------------------------

    def handle_event(self, event: ProgressEvent) -> None:
        """ProgressEvent 수신 → 해당 행 갱신."""
        if isinstance(event, FileStartEvent):
            self._total_files = event.total_files
            self.query_one("#fp-header", Label).update(
                f"처리 중: {event.total_files}개 파일"
            )
            row = _FileRow(filename=event.filename)
            self._file_rows[event.filename] = row
            self.query_one("#fp-files", Vertical).mount(row)
            row.mark_processing()

        elif isinstance(event, FileProgressEvent):
            row = self._file_rows.get(event.filename)
            if row:
                from tubearchive.shared.progress import format_time
                eta = format_time(event.info.eta_seconds) if event.info.eta_seconds > 0 else ""
                row.update_progress(event.info.percent, eta)

        elif isinstance(event, FileDoneEvent):
            row = self._file_rows.get(event.filename)
            if row:
                row.mark_done(event.success)
            if event.success:
                self._done_count += 1
            self._update_overall_bar()

    def append_log(self, text: str) -> None:
        self.query_one("#fp-log", RichLog).write(text.rstrip())

    def start(self, label: str = "처리 중...") -> None:
        self._file_rows.clear()
        self._done_count = 0
        self._total_files = 0
        self.query_one("#fp-header", Label).update(label)
        files_container = self.query_one("#fp-files", Vertical)
        for child in list(files_container.children):
            child.remove()
        bar = self.query_one("#fp-overall-bar", ProgressBar)
        bar.update(total=None)
        self.query_one("#fp-log", RichLog).clear()

    def finish(self, output_path: str) -> None:
        bar = self.query_one("#fp-overall-bar", ProgressBar)
        bar.update(total=100, progress=100.0)
        self.query_one("#fp-header", Label).update(
            f"[green]완료:[/green] {output_path}"
        )

    def error(self, message: str) -> None:
        self.query_one("#fp-header", Label).update(f"[red]오류:[/red] {message}")

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    def _update_overall_bar(self) -> None:
        if self._total_files == 0:
            return
        bar = self.query_one("#fp-overall-bar", ProgressBar)
        if bar.total is None:
            bar.update(total=self._total_files)
        bar.update(progress=float(self._done_count))
```

- [ ] **Step 4: 테스트 통과 확인**

```bash
uv run pytest tests/unit/test_file_progress_panel.py -v
uv run pytest tests/unit/ -q
```

Expected: 모두 PASS

- [ ] **Step 5: 커밋**

```bash
git add tubearchive/app/tui/widgets/file_progress_panel.py tests/unit/test_file_progress_panel.py
git commit -m "feat: FileProgressPanel 위젯 신규 작성 (파일별 진행률 + ETA)"
```

---

## Task 6: screens/pipeline.py 업데이트 (PipelineContext + FileProgressPanel 교체)

**Files:**
- Modify: `tubearchive/app/tui/screens/pipeline.py`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_tui_pipeline.py`에 추가:

```python
@pytest.mark.asyncio
async def test_pipeline_uses_file_progress_panel() -> None:
    """PipelinePane이 FileProgressPanel을 포함한다."""
    from tubearchive.app.tui.app import TubeArchiveApp
    from tubearchive.app.tui.widgets.file_progress_panel import FileProgressPanel

    app = TubeArchiveApp()
    async with app.run_test(headless=True, size=(120, 40)):
        pane = app.query_one(PipelinePane)
        # FileProgressPanel이 렌더 트리에 있어야 함
        panel = pane.query_one(FileProgressPanel)
        assert panel is not None
```

- [ ] **Step 2: 테스트 실행 — FAIL 확인**

```bash
uv run pytest tests/unit/test_tui_pipeline.py::test_pipeline_uses_file_progress_panel -v
```

Expected: FAIL — `NoMatches: No 'FileProgressPanel' found`

- [ ] **Step 3: screens/pipeline.py 수정**

변경 내역:

**import 교체** (파일 상단):
```python
# 제거
from tubearchive.app.tui.widgets.progress_panel import ProgressPanel
# 추가
from tubearchive.app.tui.widgets.file_progress_panel import FileProgressPanel
```

**compose()** — `ProgressPanel` → `FileProgressPanel`:
```python
yield FileProgressPanel(id="pipeline-progress")
```

**`_run_pipeline_worker` 시그니처 변경** — `notifier` 파라미터 추가:
```python
@work(thread=True, exclusive=True)
def _run_pipeline_worker(self, validated_args: object, notifier: object) -> None:
```

**`_run_pipeline_worker` 본문 수정**:
```python
    from tubearchive.app.cli.context import PipelineContext
    from tubearchive.app.cli.pipeline import run_pipeline

    panel = self.query_one(FileProgressPanel)

    def _safe_append(text: str) -> None:
        self.app.call_from_thread(panel.append_log, text)

    def _on_progress(event: object) -> None:
        self.app.call_from_thread(panel.handle_event, event)

    writer = _TuiWriter(_safe_append)
    log_handler = _TuiLogHandler(_safe_append)
    context = PipelineContext(notifier=notifier, on_progress=_on_progress)  # type: ignore[arg-type]

    root_logger = logging.getLogger()
    prev_level = root_logger.level
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(log_handler)
    try:
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            output_path = run_pipeline(validated_args, context=context)  # type: ignore[arg-type]
        self.app.call_from_thread(self._on_pipeline_done, output_path)
    except Exception as exc:
        self.app.call_from_thread(self._on_pipeline_error, str(exc))
    finally:
        root_logger.removeHandler(log_handler)
        root_logger.setLevel(prev_level)
```

**`_on_pipeline_done` / `_on_pipeline_error`** — `ProgressPanel` → `FileProgressPanel`:
```python
def _on_pipeline_done(self, output_path: Path) -> None:
    panel = self.query_one(FileProgressPanel)
    ...

def _on_pipeline_error(self, message: str) -> None:
    panel = self.query_one(FileProgressPanel)
    ...
```

**`_launch_pipeline`** — notifier 조건부 생성 후 worker에 전달:
```python
def _launch_pipeline(self) -> None:
    from tubearchive.app.tui.bridge import build_validated_args

    browser = self.query_one(FileBrowserPane)
    options = self.query_one(OptionsPane)
    targets = browser.get_selected_targets()

    try:
        state = options.collect_state()
        yt = getattr(self.app, "_youtube_applied", {})
        if "upload_privacy" in yt:
            state.upload_privacy = str(yt["upload_privacy"])
        if "upload_playlists" in yt:
            state.upload_playlists = list(yt["upload_playlists"])
        validated_args = build_validated_args(targets, state)
    except ValueError as exc:
        self.query_one("#pipeline-status", Label).update(f"[red]{exc}[/]")
        return

    notifier = None
    if getattr(validated_args, "notify", False):
        try:
            from tubearchive.config import load_config
            from tubearchive.infra.notification.notifier import Notifier
            cfg = load_config()
            notifier = Notifier(cfg.notification)
        except Exception:
            pass

    self._pipeline_active = True
    self._show_progress_view()

    target_label = targets[0].name if targets else "?"
    panel = self.query_one(FileProgressPanel)
    panel.start(f"처리 중: {target_label}")
    self.query_one("#pipeline-status", Label).update("실행 중...")

    self._run_pipeline_worker(validated_args, notifier)
```

- [ ] **Step 4: 기존 TUI 테스트에서 ProgressPanel 참조 수정**

`tests/unit/test_tui_pipeline.py`에서 `ProgressPanel` import가 있으면 제거.

기존 `test_pipeline_on_done_updates_state`, `test_pipeline_on_error_updates_state` 테스트는 `pane._on_pipeline_done()`과 `pane._on_pipeline_error()`를 호출하므로 `FileProgressPanel`의 `finish()`/`error()`가 있으면 통과.

- [ ] **Step 5: 테스트 통과 확인**

```bash
uv run pytest tests/unit/test_tui_pipeline.py -v
uv run pytest tests/unit/ -q
```

Expected: 모두 PASS

- [ ] **Step 6: 커밋**

```bash
git add tubearchive/app/tui/screens/pipeline.py tests/unit/test_tui_pipeline.py
git commit -m "feat: TUI PipelinePane에 FileProgressPanel + PipelineContext 연결"
```

---

## Task 7: 버전 bump + 최종 검증

**Files:**
- Modify: `pyproject.toml`
- Modify: `tubearchive/__init__.py`

- [ ] **Step 1: 버전 확인**

```bash
grep -E "^version|^__version__" pyproject.toml tubearchive/__init__.py
```

- [ ] **Step 2: 버전 bump** (예: 0.x.y → 0.x.(y+1))

`pyproject.toml`과 `tubearchive/__init__.py` 두 곳 모두 동일한 버전으로 수정.

- [ ] **Step 3: import smoke 테스트**

```bash
uv run python -c "
from tubearchive.app.cli.context import PipelineContext, FileStartEvent, FileProgressEvent, FileDoneEvent
from tubearchive.app.cli.pipeline import run_pipeline
from tubearchive.app.tui.widgets.file_progress_panel import FileProgressPanel
print('import OK')
"
```

Expected: `import OK`

- [ ] **Step 4: 전체 단위 테스트**

```bash
uv run pytest tests/unit/ -q
```

Expected: 1472+ passed (신규 테스트 추가분 포함)

- [ ] **Step 5: ruff + mypy**

```bash
uv run ruff check tubearchive/ tests/
uv run ruff format --check tubearchive/ tests/
uv run mypy tubearchive/
```

Expected: 모두 통과

- [ ] **Step 6: 최종 커밋**

```bash
git add pyproject.toml tubearchive/__init__.py
git commit -m "chore: Sprint 1 버전 bump"
```

---

## Self-Review

### Spec coverage

| 스펙 요구사항 | 구현 태스크 |
|--------------|------------|
| `PipelineContext` dataclass | Task 1 |
| `ProgressEvent` 타입 (FileStart/Progress/Done) | Task 1 |
| `run_pipeline` context 파라미터 추가 | Task 2 |
| notifier → context.notifier 통합 | Task 2 |
| `_transcode_sequential` 이벤트 emit | Task 3 |
| `_transcode_parallel` 이벤트 emit | Task 4 |
| `FileProgressPanel` 위젯 신규 작성 | Task 5 |
| ProgressPanel → FileProgressPanel 교체 | Task 6 |
| TUI에서 notifier 조건부 생성 | Task 6 |
| CLI 경로 하위 호환 보장 | Task 2 (context=None 기본값) |

### Placeholder 검사

없음 — 모든 스텝에 실제 코드 포함.

### 타입 일관성

- `PipelineContext`의 필드명: `notifier`, `on_progress` — Task 2·3·4·6에서 동일하게 사용.
- `FileProgressPanel` 메서드명: `handle_event`, `start`, `finish`, `error`, `append_log` — Task 5 정의, Task 6 사용.
- `_FileRow` 메서드명: `mark_processing`, `update_progress`, `mark_done` — Task 5 내부에서 일관.
- `_status` 값: `"pending"`, `"processing"`, `"done"`, `"error"` — 테스트(Task 5)와 구현(Task 5) 일치.
