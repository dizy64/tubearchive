"""
알림 시스템 E2E 테스트.

실제 ffmpeg 파이프라인을 실행하면서 알림이 올바른 시점에 발송되는지 검증한다.
Provider의 실제 외부 호출은 mock으로 대체하고, 이벤트 dispatch 흐름을 확인한다.

실행:
    uv run pytest tests/e2e/test_notification.py -v
"""

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tubearchive.cli import ValidatedArgs, run_pipeline
from tubearchive.config import (
    DiscordConfig,
    MacOSNotifyConfig,
    NotificationConfig,
    SlackConfig,
    TelegramConfig,
)
from tubearchive.notification.events import EventType
from tubearchive.notification.notifier import Notifier

from .conftest import create_test_video

# ffmpeg 없으면 전체 모듈 스킵
pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard3,
]


def _make_notifier_config(*, macos_enabled: bool = True) -> NotificationConfig:
    """테스트용 NotificationConfig 생성."""
    return NotificationConfig(
        enabled=True,
        on_transcode_complete=True,
        on_merge_complete=True,
        on_upload_complete=True,
        on_error=True,
        macos=MacOSNotifyConfig(enabled=macos_enabled),
        telegram=TelegramConfig(),
        discord=DiscordConfig(),
        slack=SlackConfig(),
    )


class TestNotificationDuringPipeline:
    """파이프라인 실행 중 알림 dispatch E2E 테스트."""

    def test_single_video_sends_transcode_and_merge_events(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """단일 영상 파이프라인: transcode_complete + merge_complete 알림 발송."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)
        output_file = e2e_output_dir / "output.mp4"

        config = _make_notifier_config()
        notifier = Notifier(config)

        # Provider.send를 mock하여 실제 osascript 호출 방지
        mock_send = MagicMock(return_value=True)
        for provider in notifier._providers:
            provider.send = mock_send  # type: ignore[assignment]

        args = ValidatedArgs(
            targets=[e2e_video_dir / "clip.mov"],
            output=output_file,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )

        run_pipeline(args, notifier=notifier)

        # 알림이 2회 호출됨: transcode_complete + merge_complete
        assert mock_send.call_count == 2
        event_types = [call.args[0].event_type for call in mock_send.call_args_list]
        assert EventType.TRANSCODE_COMPLETE in event_types
        assert EventType.MERGE_COMPLETE in event_types

    def test_two_videos_merge_sends_events(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """2개 영상 병합 파이프라인: transcode_complete + merge_complete 알림."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        create_test_video(e2e_video_dir / "clip_001.mov", duration=2.0)
        create_test_video(e2e_video_dir / "clip_002.mov", duration=2.0)
        output_file = e2e_output_dir / "merged.mp4"

        config = _make_notifier_config()
        notifier = Notifier(config)

        mock_send = MagicMock(return_value=True)
        for provider in notifier._providers:
            provider.send = mock_send  # type: ignore[assignment]

        args = ValidatedArgs(
            targets=[e2e_video_dir],
            output=output_file,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )

        run_pipeline(args, notifier=notifier)

        assert mock_send.call_count == 2
        event_types = [call.args[0].event_type for call in mock_send.call_args_list]
        assert EventType.TRANSCODE_COMPLETE in event_types
        assert EventType.MERGE_COMPLETE in event_types

    def test_merge_event_contains_output_path(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """merge_complete 이벤트에 출력 파일 경로가 포함되어야 함."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)
        output_file = e2e_output_dir / "check_path.mp4"

        config = _make_notifier_config()
        notifier = Notifier(config)

        mock_send = MagicMock(return_value=True)
        for provider in notifier._providers:
            provider.send = mock_send  # type: ignore[assignment]

        args = ValidatedArgs(
            targets=[e2e_video_dir / "clip.mov"],
            output=output_file,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )

        run_pipeline(args, notifier=notifier)

        # merge_complete 이벤트 찾기
        merge_events = [
            call.args[0]
            for call in mock_send.call_args_list
            if call.args[0].event_type == EventType.MERGE_COMPLETE
        ]
        assert len(merge_events) == 1
        assert "check_path.mp4" in merge_events[0].message

    def test_no_notification_when_notifier_none(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """notifier=None이면 알림 없이 파이프라인이 정상 완료."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)
        output_file = e2e_output_dir / "no_notify.mp4"

        args = ValidatedArgs(
            targets=[e2e_video_dir / "clip.mov"],
            output=output_file,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )

        # notifier=None으로 호출 — 예외 없이 완료해야 함
        result = run_pipeline(args, notifier=None)
        assert result.exists()

    def test_disabled_event_not_sent(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """비활성화된 이벤트는 전송되지 않아야 함."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)
        output_file = e2e_output_dir / "disabled.mp4"

        # merge_complete만 비활성화
        config = NotificationConfig(
            enabled=True,
            on_transcode_complete=True,
            on_merge_complete=False,
            on_upload_complete=True,
            on_error=True,
            macos=MacOSNotifyConfig(enabled=True),
            telegram=TelegramConfig(),
            discord=DiscordConfig(),
            slack=SlackConfig(),
        )
        notifier = Notifier(config)

        mock_send = MagicMock(return_value=True)
        for provider in notifier._providers:
            provider.send = mock_send  # type: ignore[assignment]

        args = ValidatedArgs(
            targets=[e2e_video_dir / "clip.mov"],
            output=output_file,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )

        run_pipeline(args, notifier=notifier)

        # transcode_complete만 전송됨
        event_types = [call.args[0].event_type for call in mock_send.call_args_list]
        assert EventType.TRANSCODE_COMPLETE in event_types
        assert EventType.MERGE_COMPLETE not in event_types

    def test_provider_failure_does_not_break_pipeline(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Provider 전송 실패해도 파이프라인은 정상 완료해야 함."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)
        output_file = e2e_output_dir / "fail_notify.mp4"

        config = _make_notifier_config()
        notifier = Notifier(config)

        # Provider.send가 항상 예외를 발생
        mock_send = MagicMock(side_effect=RuntimeError("Provider 에러"))
        for provider in notifier._providers:
            provider.send = mock_send  # type: ignore[assignment]

        args = ValidatedArgs(
            targets=[e2e_video_dir / "clip.mov"],
            output=output_file,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )

        # 예외 없이 파이프라인 완료
        result = run_pipeline(args, notifier=notifier)
        assert result.exists()
