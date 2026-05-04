# tests/unit/test_pipeline_progress.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

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
