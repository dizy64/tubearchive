"""후처리 훅 실행 유틸리티 테스트."""

import subprocess
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from tubearchive.config import HooksConfig
from tubearchive.core import hooks
from tubearchive.core.hooks import HookContext, HookEvent, run_hooks


class TestHookContext:
    """HookContext 기본 동작 테스트."""

    def test_build_hook_env(self) -> None:
        """실행 컨텍스트가 환경변수에 반영된다."""
        context = HookContext(
            output_path=Path("/tmp/output.mp4"),
            youtube_id="yt123",
            input_paths=(Path("/a.mp4"), Path("/b.mov")),
            error_message="boom",
        )

        env = hooks._build_hook_env(context)

        assert env["TUBEARCHIVE_OUTPUT_PATH"] == "/tmp/output.mp4"
        assert env["TUBEARCHIVE_YOUTUBE_ID"] == "yt123"
        assert env["TUBEARCHIVE_INPUT_PATHS"] == "/a.mp4;/b.mov"
        assert env["TUBEARCHIVE_INPUT_COUNT"] == "2"
        assert env["TUBEARCHIVE_ERROR_MESSAGE"] == "boom"

    def test_build_hook_env_empty_context(self) -> None:
        """빈 컨텍스트는 기본 빈 문자열로 반영된다."""
        env = hooks._build_hook_env(HookContext())

        assert env["TUBEARCHIVE_OUTPUT_PATH"] == ""
        assert env["TUBEARCHIVE_YOUTUBE_ID"] == ""
        assert env["TUBEARCHIVE_INPUT_PATHS"] == ""
        assert env["TUBEARCHIVE_INPUT_COUNT"] == "0"


class TestRunHooks:
    """run_hooks 동작 테스트."""

    def test_executes_commands(self) -> None:
        """훅 이벤트에 등록된 명령을 모두 실행한다."""
        context = HookContext(input_paths=(Path("/a"), Path("/b")), output_path=Path("/out.mp4"))

        with patch("tubearchive.core.hooks.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            run_hooks(
                HooksConfig(on_transcode=("script-transcode", "script-merge")),
                "on_transcode",
                context=context,
                timeout_sec=7,
            )

        assert mock_run.call_count == 2

        first_call = mock_run.call_args_list[0]
        assert first_call.args[0] == ["script-transcode"]
        assert "shell" not in first_call.kwargs
        assert first_call.kwargs["timeout"] == 7
        assert first_call.kwargs["check"] is False

        env = first_call.kwargs["env"]
        assert env["TUBEARCHIVE_OUTPUT_PATH"] == "/out.mp4"
        assert env["TUBEARCHIVE_INPUT_PATHS"] == "/a;/b"
        assert env["TUBEARCHIVE_INPUT_COUNT"] == "2"

    def test_no_commands_no_execution(self) -> None:
        """훅 정의가 없으면 외부 실행을 시도하지 않는다."""
        with patch("tubearchive.core.hooks.subprocess.run") as mock_run:
            run_hooks(HooksConfig(), "on_transcode", context=HookContext())

        mock_run.assert_not_called()

    def test_unknown_event_is_ignored(self) -> None:
        """알 수 없는 이벤트는 안전하게 무시한다."""
        with patch("tubearchive.core.hooks.subprocess.run") as mock_run:
            run_hooks(
                HooksConfig(on_transcode=("x.sh",)),
                cast(HookEvent, "invalid"),
                context=HookContext(),
            )

        mock_run.assert_not_called()

    def test_timeout_is_handled(self) -> None:
        """타임아웃은 예외로 노출되지 않고 경고만 기록한다."""
        with patch("tubearchive.core.hooks.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired("cmd", 1)

            run_hooks(
                HooksConfig(on_merge=("sleep 5",)),
                "on_merge",
                context=HookContext(),
                timeout_sec=1,
            )

        mock_run.assert_called_once()

    def test_error_does_not_stop_following_hooks(self) -> None:
        """한 훅 실패가 나머지 훅 실행을 막지 않는다."""
        with patch("tubearchive.core.hooks.subprocess.run") as mock_run:
            mock_run.side_effect = [RuntimeError("boom"), MagicMock(returncode=0)]

            run_hooks(
                HooksConfig(on_error=("cmd1", "cmd2")),
                "on_error",
                context=HookContext(),
            )

        assert mock_run.call_count == 2
