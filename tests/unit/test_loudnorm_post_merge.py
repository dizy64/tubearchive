"""병합 후 라우드니스 정규화(post-merge loudnorm) 테스트.

``_apply_post_merge_loudnorm`` 은 병합된 영상 전체에 1회 EBU R128 loudnorm
2-pass를 적용한다. 비디오는 ``-c:v copy`` 로 복사하고 오디오만 재인코딩한다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

LOUDNORM_JSON_STDERR = """
[Parsed_loudnorm_0 @ 0x7f9b4] Summary:
{
\t"input_i" : "-24.50",
\t"input_tp" : "-3.20",
\t"input_lra" : "8.10",
\t"input_thresh" : "-35.00",
\t"output_i" : "-14.00",
\t"output_tp" : "-1.50",
\t"output_lra" : "7.00",
\t"output_thresh" : "-24.50",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.50"
}
"""


class _CompletedProc:
    """``subprocess.run`` 반환값을 흉내내는 미니 객체."""

    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


@pytest.fixture
def merged_video(tmp_path: Path) -> Path:
    """병합 결과 파일 경로 (실제 내용은 비어 있어도 무방, ffmpeg는 mock된다)."""
    src = tmp_path / "merged.mp4"
    src.write_bytes(b"")
    return src


def test_skips_when_no_audio_stream(merged_video: Path, tmp_path: Path) -> None:
    """오디오 스트림이 없으면 분석/2nd pass를 건너뛰고 원본 경로를 그대로 반환한다.

    수 GB 파일을 굳이 ``shutil.copy2``로 복제하지 않는다. 호출부는 반환 경로와
    입력 경로가 동일할 경우 ``shutil.move``를 건너뛰어야 한다.
    """
    from tubearchive.app.cli.pipeline import _apply_post_merge_loudnorm

    output = tmp_path / "normalized.mp4"

    with (
        patch("tubearchive.app.cli.pipeline._has_audio_stream", return_value=False),
        patch("tubearchive.app.cli.pipeline.subprocess.run") as mock_run,
    ):
        result = _apply_post_merge_loudnorm(merged_video, output)

    assert result == merged_video, "스킵 시 원본 경로를 그대로 반환해야 함"
    assert not output.exists(), "스킵 시 출력 파일을 생성하지 않아야 함 (디스크 I/O 절약)"
    mock_run.assert_not_called()


def test_analysis_failure_falls_back_to_original_path(merged_video: Path, tmp_path: Path) -> None:
    """1st pass 분석이 실패하면 정규화를 건너뛰고 원본 경로를 그대로 반환한다 (graceful)."""
    from tubearchive.app.cli.pipeline import _apply_post_merge_loudnorm
    from tubearchive.infra.ffmpeg.executor import FFmpegError

    output = tmp_path / "normalized.mp4"

    with (
        patch("tubearchive.app.cli.pipeline._has_audio_stream", return_value=True),
        patch("tubearchive.infra.ffmpeg.executor.FFmpegExecutor") as mock_executor_cls,
        patch("tubearchive.app.cli.pipeline.subprocess.run") as mock_run,
    ):
        mock_executor = mock_executor_cls.return_value
        mock_executor.build_loudness_analysis_command.return_value = ["ffmpeg"]
        mock_executor.run_analysis.side_effect = FFmpegError("analysis broke")

        result = _apply_post_merge_loudnorm(merged_video, output)

    assert result == merged_video
    assert not output.exists()
    # 2nd pass(ffmpeg run)는 호출되지 않아야 한다
    mock_run.assert_not_called()


def test_runs_analysis_then_applies_loudnorm(merged_video: Path, tmp_path: Path) -> None:
    """오디오가 있으면 1st pass 분석 후 2nd pass에서 loudnorm을 적용한다."""
    from tubearchive.app.cli.pipeline import _apply_post_merge_loudnorm

    output = tmp_path / "normalized.mp4"

    captured_cmds: list[list[str]] = []

    def _capture(cmd: list[str], *args: Any, **kwargs: Any) -> _CompletedProc:
        captured_cmds.append(cmd)
        # 출력 파일을 만들어둠 (호출자가 존재 여부에 의존하진 않지만 형식상)
        Path(cmd[-1]).write_bytes(b"")
        return _CompletedProc(returncode=0)

    with (
        patch("tubearchive.app.cli.pipeline._has_audio_stream", return_value=True),
        patch("tubearchive.infra.ffmpeg.executor.FFmpegExecutor") as mock_executor_cls,
        patch("tubearchive.app.cli.pipeline.subprocess.run", side_effect=_capture),
    ):
        mock_executor = mock_executor_cls.return_value
        mock_executor.build_loudness_analysis_command.return_value = [
            "ffmpeg",
            "-i",
            str(merged_video),
            "-af",
            "loudnorm=...:print_format=json",
            "-vn",
            "-f",
            "null",
            "-",
        ]
        mock_executor.run_analysis.return_value = LOUDNORM_JSON_STDERR

        result = _apply_post_merge_loudnorm(merged_video, output)

    assert result == output
    mock_executor.build_loudness_analysis_command.assert_called_once()
    mock_executor.run_analysis.assert_called_once()
    # 2nd pass ffmpeg 명령 검증
    assert len(captured_cmds) == 1
    cmd = captured_cmds[0]
    assert cmd[0] == "ffmpeg"
    # 비디오는 stream copy 여야 한다 — 재인코딩이 필요한 항목은 오디오뿐
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "copy"
    # 오디오는 AAC로 재인코딩
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "aac"
    # 샘플레이트는 48000 (loudnorm 96kHz 업샘플링 회귀 방지)
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "48000"
    # loudnorm 2nd pass 필터에 measured_I 측정값이 박혀 있어야 한다
    audio_filter_idx = cmd.index("-af") + 1
    audio_filter = cmd[audio_filter_idx]
    assert "loudnorm" in audio_filter
    assert "measured_I=-24.5" in audio_filter


def test_ffmpeg_2nd_pass_failure_raises(merged_video: Path, tmp_path: Path) -> None:
    """2nd pass ffmpeg 자체가 실패하면 RuntimeError를 올린다."""
    from tubearchive.app.cli.pipeline import _apply_post_merge_loudnorm

    output = tmp_path / "normalized.mp4"

    with (
        patch("tubearchive.app.cli.pipeline._has_audio_stream", return_value=True),
        patch("tubearchive.infra.ffmpeg.executor.FFmpegExecutor") as mock_executor_cls,
        patch(
            "tubearchive.app.cli.pipeline.subprocess.run",
            return_value=_CompletedProc(returncode=1, stderr="encode failed"),
        ),
    ):
        mock_executor = mock_executor_cls.return_value
        mock_executor.build_loudness_analysis_command.return_value = ["ffmpeg"]
        mock_executor.run_analysis.return_value = LOUDNORM_JSON_STDERR

        with pytest.raises(RuntimeError, match="Post-merge loudnorm failed"):
            _apply_post_merge_loudnorm(merged_video, output)


def test_transcode_options_no_longer_has_normalize_audio() -> None:
    """``TranscodeOptions``에서 ``normalize_audio`` 필드가 제거되었음을 회귀로 보장.

    라우드니스 정규화는 트랜스코딩이 아닌 병합 직후 단계에서 적용되므로,
    이 필드는 더 이상 존재해서는 안 된다.
    """
    from tubearchive.app.cli.pipeline import TranscodeOptions

    assert "normalize_audio" not in TranscodeOptions.__dataclass_fields__


def test_transcoder_does_not_accept_normalize_audio() -> None:
    """``Transcoder.transcode_video``에서 ``normalize_audio`` 매개변수가 제거됨을 보장."""
    import inspect

    from tubearchive.domain.media.transcoder import Transcoder

    sig = inspect.signature(Transcoder.transcode_video)
    assert "normalize_audio" not in sig.parameters
