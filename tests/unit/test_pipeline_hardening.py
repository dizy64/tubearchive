"""Final review regression tests for pipeline hardening fixes."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_get_temp_dir_uses_unique_atomic_directory() -> None:
    from tubearchive.app.cli.pipeline.io_utils import get_temp_dir

    first = get_temp_dir()
    second = get_temp_dir()
    try:
        assert first != second
        assert first.is_dir()
        assert second.is_dir()
        assert first.name.startswith("tubearchive-")
        assert second.name.startswith("tubearchive-")
    finally:
        shutil.rmtree(first, ignore_errors=True)
        shutil.rmtree(second, ignore_errors=True)


def test_has_audio_stream_only_reports_valid_probe_result() -> None:
    from tubearchive.app.cli.pipeline.io_utils import _has_audio_stream

    with patch(
        "tubearchive.app.cli.pipeline.io_utils.subprocess.run",
        return_value=SimpleNamespace(stdout=json.dumps({"streams": []})),
    ):
        assert _has_audio_stream(Path("silent.mp4")) is False

    with patch(
        "tubearchive.app.cli.pipeline.io_utils.subprocess.run",
        return_value=SimpleNamespace(stdout=json.dumps({"streams": [{"codec_type": "audio"}]})),
    ):
        assert _has_audio_stream(Path("audio.mp4")) is True


def test_has_audio_stream_propagates_probe_failure() -> None:
    import subprocess

    from tubearchive.app.cli.pipeline.io_utils import _has_audio_stream

    with (
        patch(
            "tubearchive.app.cli.pipeline.io_utils.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, ["ffprobe"]),
        ),
        pytest.raises(subprocess.CalledProcessError),
    ):
        _has_audio_stream(Path("broken.mp4"))


def test_cleanup_temp_preserves_directory_when_file_is_in_use(tmp_path: Path) -> None:
    from tubearchive.app.cli.pipeline import TranscodeResult, _cleanup_temp
    from tubearchive.domain.models.clip import ClipInfo

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    in_use = temp_dir / "chunk.mp4"
    in_use.write_bytes(b"keep")
    result = TranscodeResult(
        output_path=in_use,
        video_id=1,
        clip_info=ClipInfo(name="chunk.mp4", duration=1.0, device=None, shot_time=None),
        silence_segments=None,
    )

    with patch("tubearchive.app.cli.pipeline._is_file_in_use", return_value=True):
        _cleanup_temp(temp_dir, [result], tmp_path / "final.mp4")

    assert temp_dir.exists()
    assert in_use.exists()
    assert in_use.read_bytes() == b"keep"


def test_backup_history_failure_does_not_escape(tmp_path: Path) -> None:
    from tubearchive.app.cli.pipeline.archive import _run_backup
    from tubearchive.domain.media.backup import BackupResult

    final_path = tmp_path / "merged.mp4"
    final_path.write_bytes(b"merged")
    args = MagicMock(backup_remote="remote:path")
    executor = MagicMock()
    executor.copy.return_value = BackupResult(
        source=final_path,
        remote="remote:path",
        success=True,
    )

    with (
        patch("tubearchive.app.cli.pipeline.archive.BackupExecutor", return_value=executor),
        patch(
            "tubearchive.app.cli.main.database_session",
            side_effect=RuntimeError("database unavailable"),
        ),
    ):
        _run_backup(
            final_path=final_path,
            split_files=[],
            timelapse_path=None,
            original_paths_for_backup=[],
            validated_args=args,
            merge_job_id=42,
        )

    executor.copy.assert_called_once_with(final_path)


def test_subtitle_burn_falls_back_to_libx265(tmp_path: Path) -> None:
    from tubearchive.app.cli.pipeline.postprocess import _apply_subtitle_burn

    input_path = tmp_path / "input.mp4"
    subtitle_path = tmp_path / "captions.srt"
    input_path.write_bytes(b"input")
    subtitle_path.write_text("captions", encoding="utf-8")
    output_path = tmp_path / "input_subtitled.mp4"
    results = [
        SimpleNamespace(returncode=1, stderr="VideoToolbox unavailable"),
        SimpleNamespace(returncode=0, stderr=""),
    ]

    def run(cmd: list[str], **_: object) -> SimpleNamespace:
        result = results.pop(0)
        if result.returncode == 0:
            Path(cmd[-1]).write_bytes(b"burned")
        return result

    with (
        patch(
            "tubearchive.domain.media.subtitle.build_subtitle_filter",
            return_value="subtitles=captions.srt",
        ),
        patch(
            "tubearchive.app.cli.pipeline.postprocess.subprocess.run", side_effect=run
        ) as mock_run,
    ):
        result = _apply_subtitle_burn(input_path, subtitle_path)

    assert result == output_path
    assert [call.args[0][call.args[0].index("-c:v") + 1] for call in mock_run.call_args_list] == [
        "hevc_videotoolbox",
        "libx265",
    ]
    assert output_path.read_bytes() == b"burned"
