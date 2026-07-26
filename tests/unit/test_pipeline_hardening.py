"""파이프라인 분리 후 안전성 회귀 테스트."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def test_get_temp_dir_creates_unique_atomic_run_directories() -> None:
    """동시 실행은 공통 base 아래 서로 다른 실행 디렉터리를 사용한다."""
    from tubearchive.app.cli.pipeline.io_utils import get_temp_dir

    first = get_temp_dir()
    second = get_temp_dir()
    try:
        assert first != second
        assert first.parent == Path("/tmp/tubearchive")
        assert second.parent == Path("/tmp/tubearchive")
        assert first.is_dir()
        assert second.is_dir()
    finally:
        shutil.rmtree(first, ignore_errors=True)
        shutil.rmtree(second, ignore_errors=True)


def test_has_audio_stream_returns_false_for_valid_empty_probe() -> None:
    """정상 ffprobe 결과에 오디오 스트림이 없을 때만 False를 반환한다."""
    from tubearchive.app.cli.pipeline.io_utils import _has_audio_stream

    with patch(
        "tubearchive.app.cli.pipeline.io_utils.subprocess.run",
        return_value=SimpleNamespace(stdout=json.dumps({"streams": []})),
    ):
        assert _has_audio_stream(Path("silent.mp4")) is False


@pytest.mark.parametrize(
    "side_effect",
    [
        subprocess.CalledProcessError(1, ["ffprobe"]),
        subprocess.TimeoutExpired(["ffprobe"], 30),
    ],
)
def test_has_audio_stream_propagates_probe_failures(side_effect: Exception) -> None:
    """ffprobe 운영 실패를 오디오 없음으로 오인하지 않는다."""
    from tubearchive.app.cli.pipeline.io_utils import _has_audio_stream

    with (
        patch(
            "tubearchive.app.cli.pipeline.io_utils.subprocess.run",
            side_effect=side_effect,
        ),
        pytest.raises(type(side_effect)),
    ):
        _has_audio_stream(Path("broken.mp4"))


def test_has_audio_stream_propagates_invalid_json() -> None:
    """손상된 ffprobe 응답은 JSON 오류로 구분한다."""
    from tubearchive.app.cli.pipeline.io_utils import _has_audio_stream

    with (
        patch(
            "tubearchive.app.cli.pipeline.io_utils.subprocess.run",
            return_value=SimpleNamespace(stdout="{"),
        ),
        pytest.raises(json.JSONDecodeError),
    ):
        _has_audio_stream(Path("broken.mp4"))


def test_cleanup_temp_preserves_directory_when_file_is_in_use(tmp_path: Path) -> None:
    """사용 중 파일이 있으면 상위 임시 디렉터리까지 보존한다."""
    from tubearchive.app.cli.pipeline import TranscodeResult, _cleanup_temp
    from tubearchive.domain.models.clip import ClipInfo

    temp_dir = tmp_path / "run"
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

    assert in_use.read_bytes() == b"keep"
    assert temp_dir.exists()


def test_backup_history_failure_is_non_fatal(tmp_path: Path) -> None:
    """백업 완료 후 이력 DB 장애가 파이프라인 성공을 뒤집지 않는다."""
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


def test_subtitle_burn_uses_videotoolbox_then_libx265_fallback(tmp_path: Path) -> None:
    """자막 burn도 하드웨어 가속을 우선하고 실패할 때만 libx265를 쓴다."""
    from tubearchive.app.cli.pipeline.postprocess import _apply_subtitle_burn

    input_path = tmp_path / "input.mp4"
    subtitle_path = tmp_path / "captions.srt"
    input_path.write_bytes(b"input")
    subtitle_path.write_text("captions", encoding="utf-8")
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
            "tubearchive.app.cli.pipeline.postprocess.subprocess.run",
            side_effect=run,
        ) as mock_run,
    ):
        output = _apply_subtitle_burn(input_path, subtitle_path)

    assert output.read_bytes() == b"burned"
    assert [call.args[0][call.args[0].index("-c:v") + 1] for call in mock_run.call_args_list] == [
        "hevc_videotoolbox",
        "libx265",
    ]


def test_subtitle_result_replaces_final_without_preemptive_unlink(tmp_path: Path) -> None:
    """교체 실패 전까지 기존 병합 결과를 먼저 삭제하지 않는다."""
    from tubearchive.app.cli.pipeline.postprocess import _apply_post_merge_processing

    final_path = tmp_path / "merged.mp4"
    final_path.write_bytes(b"original")
    subtitle_path = tmp_path / "merged.srt"
    subtitle_path.write_text("captions", encoding="utf-8")
    burned_path = tmp_path / "merged_subtitled.mp4"
    burned_path.write_bytes(b"burned")
    args = SimpleNamespace(
        normalize_audio=False,
        bgm_path=None,
        subtitle=True,
        subtitle_format="srt",
        subtitle_model="tiny",
        subtitle_lang="ko",
        subtitle_burn=True,
        quality_report=False,
    )

    with (
        patch(
            "tubearchive.domain.media.subtitle.generate_subtitles",
            return_value=SimpleNamespace(
                subtitle_path=subtitle_path,
                detected_language="ko",
            ),
        ),
        patch(
            "tubearchive.app.cli.pipeline.postprocess._apply_subtitle_burn",
            return_value=burned_path,
        ),
        patch.object(Path, "unlink", autospec=True) as mock_unlink,
    ):
        _apply_post_merge_processing(
            final_path,
            tmp_path,
            args,  # type: ignore[arg-type]
            [],
            [],
            [],
        )

    mock_unlink.assert_not_called()
    assert final_path.read_bytes() == b"burned"
