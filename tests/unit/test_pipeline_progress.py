# tests/unit/test_pipeline_progress.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.app.cli.context import PipelineContext


def test_run_pipeline_accepts_context_parameter(tmp_path: Path) -> None:
    """run_pipeline이 context 파라미터를 받는다."""
    from tubearchive.app.cli.pipeline import run_pipeline

    ctx = PipelineContext()
    # 스캔 단계에서 존재하지 않는 파일이므로 FileNotFoundError(OSError) 발생이 정상
    with pytest.raises(OSError):
        run_pipeline(
            MagicMock(
                targets=[tmp_path / "nonexistent.mov"],
                output=tmp_path / "out.mp4",
                output_dir=None,
                no_resume=False,
                keep_temp=False,
                dry_run=False,
                detect_silence=False,
                upload=False,
                template_intro=None,
                template_outro=None,
                group_sequences=False,
                parallel=1,
                split_duration=None,
                split_size=None,
                archive_originals=None,
                notify=False,
                project=None,
                quality_report=False,
                subtitle=False,
                bgm_path=None,
                timelapse_speed=None,
                thumbnail=False,
                backup_remote=None,
                hooks=None,
                reorder=False,
                sort_key="shot_time",
                exclude_patterns=None,
                include_only_patterns=None,
            ),
            context=ctx,
        )


def test_run_pipeline_context_none_is_backward_compat(tmp_path: Path) -> None:
    """context=None이면 기존처럼 동작한다 (기존 notifier 파라미터 없어도 OK)."""
    from tubearchive.app.cli.pipeline import run_pipeline

    with pytest.raises(OSError):
        run_pipeline(
            MagicMock(
                targets=[tmp_path / "nonexistent.mov"],
                output=None,
                output_dir=None,
                no_resume=False,
                keep_temp=False,
                dry_run=False,
                detect_silence=False,
                upload=False,
                template_intro=None,
                template_outro=None,
                group_sequences=False,
                parallel=1,
                split_duration=None,
                split_size=None,
                archive_originals=None,
                notify=False,
                project=None,
                quality_report=False,
                subtitle=False,
                bgm_path=None,
                timelapse_speed=None,
                thumbnail=False,
                backup_remote=None,
                hooks=None,
                reorder=False,
                sort_key="shot_time",
                exclude_patterns=None,
                include_only_patterns=None,
            )
        )


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
        patch("tubearchive.app.cli.pipeline.save_merge_job_to_db", return_value=(None, 1)),
        patch("tubearchive.app.cli.pipeline._print_summary"),
        patch("tubearchive.app.cli.pipeline.check_output_disk_space"),
        patch("tubearchive.app.cli.pipeline.get_temp_dir", return_value=tmp_path),
        patch(
            "tubearchive.app.cli.pipeline.get_output_filename",
            return_value=tmp_path / "out.mp4",
        ),
        patch("tubearchive.app.cli.pipeline.run_hooks"),
        patch("tubearchive.app.cli.pipeline._mark_transcoding_jobs_merged"),
        patch("tubearchive.app.cli.pipeline._archive_originals"),
    ):
        from tubearchive.app.cli.pipeline import TranscodeResult
        from tubearchive.domain.models.clip import ClipInfo

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
        args.sort_key = "time"
        args.exclude_patterns = None
        args.include_only_patterns = None
        args.stabilize = False
        args.fade_duration = 0.5
        args.watermark = False

        run_pipeline(args, context=ctx)

    mock_notifier.notify.assert_called()


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
        patch(
            "tubearchive.app.cli.pipeline._collect_clip_info", return_value=fake_result.clip_info
        ),
    ):
        mock_tc = MagicMock()
        mock_tc_cls.return_value.__enter__ = MagicMock(return_value=mock_tc)
        mock_tc_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_tc.transcode_video.return_value = (fake_result.output_path, 1, [])

        _transcode_sequential([fake_video], tmp_path, opts, context=ctx)

    start_events = [e for e in events if isinstance(e, FileStartEvent)]
    done_events = [e for e in events if isinstance(e, FileDoneEvent)]

    assert len(start_events) == 1
    assert start_events[0].filename == "clip.mov"
    assert start_events[0].file_index == 0
    assert start_events[0].total_files == 1

    assert len(done_events) == 1
    assert done_events[0].filename == "clip.mov"
    assert done_events[0].file_index == 0
    assert done_events[0].success is True


def test_transcode_parallel_emits_start_and_done_events(tmp_path: Path) -> None:
    """_transcode_parallel이 각 파일에 대해 FileStartEvent와 FileDoneEvent를 emit한다.

    FileStartEvent는 루프 선행이 아닌 워커 내부(_transcode_single)에서 emit되어야 한다.
    """
    from tubearchive.app.cli.context import FileDoneEvent, FileStartEvent, PipelineContext
    from tubearchive.app.cli.pipeline import TranscodeOptions, _transcode_parallel
    from tubearchive.domain.models.clip import ClipInfo

    events: list[object] = []
    ctx = PipelineContext(on_progress=events.append)

    fake_video = MagicMock()
    fake_video.path = tmp_path / "clip.mov"

    fake_result_clip = ClipInfo(name="clip", duration=5.0, device="x", shot_time=None)
    opts = TranscodeOptions()

    # _transcode_single이 실제로 실행되어야 FileStartEvent가 워커 내부에서 emit된다.
    with (
        patch("tubearchive.app.cli.pipeline.Transcoder") as mock_tc_cls,
        patch("tubearchive.app.cli.pipeline.detect_metadata"),
        patch("tubearchive.app.cli.pipeline._collect_clip_info", return_value=fake_result_clip),
    ):
        mock_tc = MagicMock()
        mock_tc_cls.return_value.__enter__ = MagicMock(return_value=mock_tc)
        mock_tc_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_tc.transcode_video.return_value = (tmp_path / "out.mp4", 1, [])

        _transcode_parallel([fake_video], tmp_path, 1, opts, context=ctx)

    start_events = [e for e in events if isinstance(e, FileStartEvent)]
    done_events = [e for e in events if isinstance(e, FileDoneEvent)]
    assert len(start_events) == 1
    assert start_events[0].filename == "clip.mov"
    assert start_events[0].file_index == 0
    assert start_events[0].total_files == 1
    assert len(done_events) == 1
    assert done_events[0].file_index == 0
    assert done_events[0].success is True


def test_transcode_parallel_emits_done_for_all_files_on_partial_failure(
    tmp_path: Path,
) -> None:
    """파일 중 하나가 실패해도 나머지 FileDoneEvent가 모두 emit된 뒤 예외가 전파된다."""
    from tubearchive.app.cli.context import FileDoneEvent, PipelineContext
    from tubearchive.app.cli.pipeline import TranscodeOptions, _transcode_parallel
    from tubearchive.domain.models.clip import ClipInfo

    events: list[object] = []
    ctx = PipelineContext(on_progress=events.append)

    fake_video_a = MagicMock()
    fake_video_a.path = tmp_path / "a.mov"
    fake_video_b = MagicMock()
    fake_video_b.path = tmp_path / "b.mov"

    clip_a = ClipInfo(name="a", duration=5.0, device="x", shot_time=None)
    clip_b = ClipInfo(name="b", duration=5.0, device="x", shot_time=None)
    opts = TranscodeOptions()

    call_count = 0

    def fake_collect_clip_info(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return clip_a if call_count == 1 else clip_b

    with (
        patch("tubearchive.app.cli.pipeline.Transcoder") as mock_tc_cls,
        patch("tubearchive.app.cli.pipeline.detect_metadata"),
        patch(
            "tubearchive.app.cli.pipeline._collect_clip_info",
            side_effect=fake_collect_clip_info,
        ),
    ):
        mock_tc = MagicMock()
        mock_tc_cls.return_value.__enter__ = MagicMock(return_value=mock_tc)
        mock_tc_cls.return_value.__exit__ = MagicMock(return_value=False)

        # 두 번째 파일 트랜스코딩은 실패
        mock_tc.transcode_video.side_effect = [
            (tmp_path / "a_tc.mp4", 1, []),
            RuntimeError("transcode failed"),
        ]

        with pytest.raises(RuntimeError, match="transcode failed"):
            _transcode_parallel([fake_video_a, fake_video_b], tmp_path, 2, opts, context=ctx)

    done_events = [e for e in events if isinstance(e, FileDoneEvent)]
    # 두 파일 모두 FileDoneEvent가 emit되어야 한다
    assert len(done_events) == 2
    success_flags = {e.file_index: e.success for e in done_events}
    assert False in success_flags.values(), "실패한 파일의 FileDoneEvent.success가 False여야 함"
