"""알림 시스템 단위 테스트."""

from __future__ import annotations

import subprocess
import urllib.error
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.config import (
    DiscordConfig,
    MacOSNotifyConfig,
    NotificationConfig,
    SlackConfig,
    TelegramConfig,
    load_config,
)
from tubearchive.notification.events import (
    EventType,
    NotificationEvent,
    error_event,
    merge_complete_event,
    transcode_complete_event,
    upload_complete_event,
)
from tubearchive.notification.notifier import Notifier
from tubearchive.notification.providers import (
    DiscordProvider,
    MacOSProvider,
    SlackProvider,
    TelegramProvider,
    _post_json,
)

# =========================================================================
# EventType
# =========================================================================


class TestEventType:
    def test_enum_values(self) -> None:
        assert EventType.TRANSCODE_COMPLETE.value == "on_transcode_complete"
        assert EventType.MERGE_COMPLETE.value == "on_merge_complete"
        assert EventType.UPLOAD_COMPLETE.value == "on_upload_complete"
        assert EventType.ERROR.value == "on_error"

    def test_all_events_have_unique_values(self) -> None:
        values = [e.value for e in EventType]
        assert len(values) == len(set(values))


# =========================================================================
# NotificationEvent
# =========================================================================


class TestNotificationEvent:
    def test_creation_with_defaults(self) -> None:
        event = NotificationEvent(
            event_type=EventType.MERGE_COMPLETE,
            title="테스트",
            message="본문",
        )
        assert event.event_type == EventType.MERGE_COMPLETE
        assert event.title == "테스트"
        assert event.message == "본문"
        assert isinstance(event.timestamp, datetime)
        assert event.timestamp.tzinfo is not None  # UTC timezone-aware
        assert event.metadata == {}

    def test_frozen_enforcement(self) -> None:
        event = NotificationEvent(
            event_type=EventType.ERROR,
            title="t",
            message="m",
        )
        with pytest.raises(AttributeError):
            event.title = "변경"  # type: ignore[misc]

    def test_metadata_default_empty_dict(self) -> None:
        e1 = NotificationEvent(event_type=EventType.ERROR, title="a", message="b")
        e2 = NotificationEvent(event_type=EventType.ERROR, title="c", message="d")
        # 서로 다른 dict 인스턴스
        assert e1.metadata is not e2.metadata

    def test_explicit_metadata(self) -> None:
        event = NotificationEvent(
            event_type=EventType.MERGE_COMPLETE,
            title="t",
            message="m",
            metadata={"key": "val"},
        )
        assert event.metadata == {"key": "val"}


# =========================================================================
# Event Factory Functions
# =========================================================================


class TestEventFactories:
    def test_transcode_complete_event(self) -> None:
        event = transcode_complete_event(file_count=5, total_duration=120.5)
        assert event.event_type == EventType.TRANSCODE_COMPLETE
        assert "5개" in event.message
        assert "120.5" in event.message
        assert event.metadata["file_count"] == "5"

    def test_transcode_complete_event_with_output_dir(self) -> None:
        event = transcode_complete_event(file_count=1, total_duration=10.0, output_dir="/tmp/out")
        assert event.metadata["output_dir"] == "/tmp/out"

    def test_merge_complete_event(self) -> None:
        event = merge_complete_event(
            output_path="/tmp/merged.mp4",
            file_count=3,
            total_size_bytes=1024000,
        )
        assert event.event_type == EventType.MERGE_COMPLETE
        assert "3개" in event.message
        assert "/tmp/merged.mp4" in event.message
        assert event.metadata["total_size_bytes"] == "1024000"

    def test_upload_complete_event_with_youtube_id(self) -> None:
        event = upload_complete_event(video_title="테스트 영상", youtube_id="abc123")
        assert event.event_type == EventType.UPLOAD_COMPLETE
        assert "테스트 영상" in event.message
        assert "https://youtu.be/abc123" in event.message
        assert event.metadata["youtube_url"] == "https://youtu.be/abc123"

    def test_upload_complete_event_without_youtube_id(self) -> None:
        event = upload_complete_event(video_title="영상")
        assert "youtu.be" not in event.message
        assert event.metadata["youtube_url"] == ""

    def test_error_event_with_stage(self) -> None:
        event = error_event(error_message="FFmpeg 실패", stage="transcode")
        assert event.event_type == EventType.ERROR
        assert "transcode" in event.title
        assert "FFmpeg 실패" in event.message
        assert event.metadata["stage"] == "transcode"

    def test_error_event_without_stage(self) -> None:
        event = error_event(error_message="알 수 없는 에러")
        assert "오류 발생" in event.title
        assert event.metadata["stage"] == ""

    def test_error_event_truncates_long_message(self) -> None:
        """500자 초과 메시지는 잘라내야 함 (민감 정보 유출 방지)."""
        from tubearchive.notification.events import ERROR_MESSAGE_MAX_LENGTH

        long_msg = "x" * (ERROR_MESSAGE_MAX_LENGTH + 100)
        event = error_event(error_message=long_msg)
        assert len(event.message) == ERROR_MESSAGE_MAX_LENGTH + 3  # "..." 포함
        assert event.message.endswith("...")
        assert event.metadata["error"].endswith("...")

    def test_error_event_short_message_not_truncated(self) -> None:
        """짧은 메시지는 잘라내지 않아야 함."""
        event = error_event(error_message="짧은 에러")
        assert event.message == "짧은 에러"
        assert not event.message.endswith("...")

    def test_timestamp_is_utc(self) -> None:
        """타임스탬프가 UTC timezone-aware여야 함."""
        event = NotificationEvent(
            event_type=EventType.MERGE_COMPLETE,
            title="t",
            message="m",
        )
        assert event.timestamp.tzinfo == UTC


# =========================================================================
# MacOSProvider
# =========================================================================


class TestMacOSProvider:
    def _make_event(self) -> NotificationEvent:
        return NotificationEvent(
            event_type=EventType.MERGE_COMPLETE,
            title="테스트 제목",
            message="테스트 메시지",
        )

    @patch("tubearchive.notification.providers.subprocess.run")
    def test_send_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        provider = MacOSProvider(sound=True)
        assert provider.send(self._make_event()) is True
        mock_run.assert_called_once()

    @patch("tubearchive.notification.providers.subprocess.run")
    def test_send_failure_nonzero_exit(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stderr="error msg")
        provider = MacOSProvider()
        assert provider.send(self._make_event()) is False

    @patch("tubearchive.notification.providers.subprocess.run")
    def test_send_osascript_not_found(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = FileNotFoundError
        provider = MacOSProvider()
        assert provider.send(self._make_event()) is False

    @patch("tubearchive.notification.providers.subprocess.run")
    def test_send_timeout(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="osascript", timeout=5)
        provider = MacOSProvider()
        assert provider.send(self._make_event()) is False

    def test_build_script_with_sound(self) -> None:
        provider = MacOSProvider(sound=True)
        script = provider._build_script(self._make_event())
        assert 'sound name "default"' in script
        assert "TubeArchive" in script
        assert "테스트 제목" in script

    def test_build_script_without_sound(self) -> None:
        provider = MacOSProvider(sound=False)
        script = provider._build_script(self._make_event())
        assert "sound name" not in script

    def test_build_script_escapes_quotes(self) -> None:
        event = NotificationEvent(
            event_type=EventType.MERGE_COMPLETE,
            title='제목"특수',
            message='메시지"특수',
        )
        provider = MacOSProvider()
        script = provider._build_script(event)
        # 큰따옴표가 이스케이프되어야 함
        assert '\\"' in script

    def test_build_script_escapes_backslash(self) -> None:
        """백슬래시가 이중 이스케이프되어야 함."""
        event = NotificationEvent(
            event_type=EventType.MERGE_COMPLETE,
            title="C:\\Users\\test",
            message='path\\";do shell script "evil',
        )
        provider = MacOSProvider()
        script = provider._build_script(event)
        # 백슬래시가 이스케이프되어야 함
        assert "C:\\\\Users\\\\test" in script
        # 주입 시도가 무력화되어야 함: 큰따옴표가 이스케이프되어 문자열 밖으로 탈출 불가
        assert '\\\\"' in script

    def test_build_script_escapes_newline(self) -> None:
        """줄바꿈이 공백으로 치환되어야 함."""
        event = NotificationEvent(
            event_type=EventType.MERGE_COMPLETE,
            title="줄1\n줄2",
            message="메시지\r\n개행",
        )
        provider = MacOSProvider()
        script = provider._build_script(event)
        # 줄바꿈이 공백으로 치환됨
        assert "줄1 줄2" in script
        # \r 제거됨
        assert "\r" not in script

    def test_name_property(self) -> None:
        assert MacOSProvider().name == "macos"


# =========================================================================
# TelegramProvider
# =========================================================================


class TestTelegramProvider:
    def test_init_empty_token_raises(self) -> None:
        with pytest.raises(ValueError, match="bot_token"):
            TelegramProvider(bot_token="", chat_id="123")

    def test_init_empty_chat_id_raises(self) -> None:
        with pytest.raises(ValueError, match="chat_id"):
            TelegramProvider(bot_token="tok", chat_id="")

    @patch("tubearchive.notification.providers._post_json", return_value=True)
    def test_send_success(self, mock_post: MagicMock) -> None:
        provider = TelegramProvider(bot_token="tok123", chat_id="456")
        event = NotificationEvent(event_type=EventType.MERGE_COMPLETE, title="t", message="m")
        assert provider.send(event) is True
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "tok123" in call_args[0][0]
        assert call_args[0][1]["chat_id"] == "456"

    def test_name_property(self) -> None:
        p = TelegramProvider(bot_token="t", chat_id="c")
        assert p.name == "telegram"


# =========================================================================
# DiscordProvider
# =========================================================================


class TestDiscordProvider:
    def test_init_empty_url_raises(self) -> None:
        with pytest.raises(ValueError, match="webhook_url"):
            DiscordProvider(webhook_url="")

    def test_init_invalid_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="스킴"):
            DiscordProvider(webhook_url="ftp://evil.com/hook")

    def test_init_http_url_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """HTTP URL은 경고를 남기되 초기화는 성공해야 함."""
        import logging

        with caplog.at_level(logging.WARNING, logger="tubearchive.notification.providers"):
            provider = DiscordProvider(webhook_url="http://insecure.com/hook")
        assert provider.name == "discord"
        assert "HTTPS" in caplog.text

    @patch("tubearchive.notification.providers._post_json", return_value=True)
    def test_send_success(self, mock_post: MagicMock) -> None:
        provider = DiscordProvider(webhook_url="https://discord.com/hook")
        event = NotificationEvent(event_type=EventType.MERGE_COMPLETE, title="t", message="m")
        assert provider.send(event) is True

    @patch("tubearchive.notification.providers._post_json", return_value=True)
    def test_send_uses_embed_format(self, mock_post: MagicMock) -> None:
        """Discord는 Embed 포맷으로 전송해야 함."""
        provider = DiscordProvider(webhook_url="https://discord.com/hook")
        event = NotificationEvent(event_type=EventType.MERGE_COMPLETE, title="제목", message="본문")
        provider.send(event)
        payload = mock_post.call_args[0][1]
        assert "embeds" in payload
        embed = payload["embeds"][0]
        assert embed["title"] == "제목"
        assert embed["description"] == "본문"
        assert "color" in embed

    def test_name_property(self) -> None:
        assert DiscordProvider(webhook_url="https://x").name == "discord"


# =========================================================================
# SlackProvider
# =========================================================================


class TestSlackProvider:
    def test_init_empty_url_raises(self) -> None:
        with pytest.raises(ValueError, match="webhook_url"):
            SlackProvider(webhook_url="")

    def test_init_invalid_scheme_raises(self) -> None:
        with pytest.raises(ValueError, match="스킴"):
            SlackProvider(webhook_url="file:///etc/passwd")

    @patch("tubearchive.notification.providers._post_json", return_value=True)
    def test_send_success(self, mock_post: MagicMock) -> None:
        provider = SlackProvider(webhook_url="https://hooks.slack.com/x")
        event = NotificationEvent(event_type=EventType.MERGE_COMPLETE, title="t", message="m")
        assert provider.send(event) is True

    @patch("tubearchive.notification.providers._post_json", return_value=True)
    def test_send_uses_block_kit_format(self, mock_post: MagicMock) -> None:
        """Slack은 Block Kit 포맷으로 전송해야 함."""
        provider = SlackProvider(webhook_url="https://hooks.slack.com/x")
        event = NotificationEvent(event_type=EventType.MERGE_COMPLETE, title="제목", message="본문")
        provider.send(event)
        payload = mock_post.call_args[0][1]
        assert "blocks" in payload
        assert "text" in payload  # 폴백 텍스트
        header_block = payload["blocks"][0]
        assert header_block["type"] == "header"
        assert header_block["text"]["text"] == "제목"
        section_block = payload["blocks"][1]
        assert section_block["type"] == "section"
        assert section_block["text"]["text"] == "본문"

    def test_name_property(self) -> None:
        assert SlackProvider(webhook_url="https://x").name == "slack"


# =========================================================================
# _post_json
# =========================================================================


class TestPostJson:
    @patch("tubearchive.notification.providers.urllib.request.urlopen")
    def test_success_200(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        assert _post_json("https://api.example.com", {"k": "v"}, provider_name="test") is True

    @patch("tubearchive.notification.providers.urllib.request.urlopen")
    def test_success_204(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        assert _post_json("https://api.example.com", {"k": "v"}, provider_name="test") is True

    @patch("tubearchive.notification.providers.urllib.request.urlopen")
    def test_http_error_4xx(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.example.com",
            code=401,
            msg="Unauthorized",
            hdrs=MagicMock(),  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )
        assert _post_json("https://api.example.com", {}, provider_name="test") is False

    @patch("tubearchive.notification.providers.urllib.request.urlopen")
    def test_http_error_5xx(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="https://api.example.com",
            code=500,
            msg="Server Error",
            hdrs=MagicMock(),  # type: ignore[arg-type]
            fp=BytesIO(b""),
        )
        assert _post_json("https://api.example.com", {}, provider_name="test") is False

    @patch("tubearchive.notification.providers.urllib.request.urlopen")
    def test_url_error_network(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError("Network unreachable")
        assert _post_json("https://api.example.com", {}, provider_name="test") is False

    @patch("tubearchive.notification.providers.urllib.request.urlopen")
    def test_generic_exception(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = RuntimeError("unexpected")
        assert _post_json("https://api.example.com", {}, provider_name="test") is False


# =========================================================================
# Notifier
# =========================================================================


def _make_config(**kwargs: Any) -> NotificationConfig:
    """테스트용 NotificationConfig 생성 헬퍼."""
    defaults: dict[str, Any] = {
        "enabled": True,
        "on_transcode_complete": True,
        "on_merge_complete": True,
        "on_upload_complete": True,
        "on_error": True,
        "macos": MacOSNotifyConfig(enabled=False),
        "telegram": TelegramConfig(),
        "discord": DiscordConfig(),
        "slack": SlackConfig(),
    }
    defaults.update(kwargs)
    return NotificationConfig(**defaults)


class TestNotifier:
    def test_no_providers_noop(self) -> None:
        config = _make_config(macos=MacOSNotifyConfig(enabled=False))
        notifier = Notifier(config)
        assert notifier.has_providers is False
        # 예외 없이 무시됨
        event = NotificationEvent(event_type=EventType.ERROR, title="t", message="m")
        notifier.notify(event)

    def test_has_providers_with_macos(self) -> None:
        config = _make_config(macos=MacOSNotifyConfig(enabled=True))
        notifier = Notifier(config)
        assert notifier.has_providers is True
        assert notifier.provider_count == 1

    @patch("tubearchive.notification.providers.subprocess.run")
    def test_dispatch_to_provider(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        config = _make_config(macos=MacOSNotifyConfig(enabled=True))
        notifier = Notifier(config)
        event = merge_complete_event(output_path="/tmp/out.mp4", file_count=2)
        notifier.notify(event)
        mock_run.assert_called_once()

    def test_disabled_event_skipped(self) -> None:
        config = _make_config(
            on_merge_complete=False,
            macos=MacOSNotifyConfig(enabled=True),
        )
        notifier = Notifier(config)
        event = merge_complete_event(output_path="/tmp/out.mp4", file_count=2)
        # macOS provider가 호출되지 않음을 확인
        with patch.object(notifier._providers[0], "send") as mock_send:
            notifier.notify(event)
            mock_send.assert_not_called()

    @patch("tubearchive.notification.providers.subprocess.run")
    def test_provider_failure_continues(self, mock_run: MagicMock) -> None:
        """Provider가 False를 반환해도 예외 없이 계속 진행."""
        mock_run.return_value = MagicMock(returncode=1, stderr="fail")
        config = _make_config(macos=MacOSNotifyConfig(enabled=True))
        notifier = Notifier(config)
        event = merge_complete_event(output_path="/tmp/out.mp4", file_count=1)
        # 예외 없이 완료
        notifier.notify(event)

    @patch("tubearchive.notification.providers.subprocess.run")
    def test_test_notification(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        config = _make_config(macos=MacOSNotifyConfig(enabled=True))
        notifier = Notifier(config)
        results = notifier.test_notification()
        assert results == {"macos": True}

    def test_is_event_enabled_default_true(self) -> None:
        """이벤트 설정이 None이면 기본 활성화."""
        config = _make_config(
            on_transcode_complete=None,
            macos=MacOSNotifyConfig(enabled=True),
        )
        notifier = Notifier(config)
        assert notifier._is_event_enabled(EventType.TRANSCODE_COMPLETE) is True

    def test_is_event_enabled_false(self) -> None:
        config = _make_config(
            on_error=False,
            macos=MacOSNotifyConfig(enabled=True),
        )
        notifier = Notifier(config)
        assert notifier._is_event_enabled(EventType.ERROR) is False


# =========================================================================
# Build Providers
# =========================================================================


class TestBuildProviders:
    def test_macos_default_enabled(self) -> None:
        """macos.enabled가 None이면 기본 활성화."""
        config = _make_config(macos=MacOSNotifyConfig(enabled=None))
        notifier = Notifier(config)
        assert notifier.provider_count == 1

    def test_macos_explicitly_disabled(self) -> None:
        config = _make_config(macos=MacOSNotifyConfig(enabled=False))
        notifier = Notifier(config)
        assert notifier.provider_count == 0

    def test_telegram_needs_all_fields(self) -> None:
        config = _make_config(
            telegram=TelegramConfig(enabled=True, bot_token="tok", chat_id="123"),
        )
        notifier = Notifier(config)
        names = [p.name for p in notifier._providers]
        assert "telegram" in names

    def test_telegram_missing_token_skipped(self) -> None:
        config = _make_config(
            telegram=TelegramConfig(enabled=True, bot_token=None, chat_id="123"),
        )
        notifier = Notifier(config)
        names = [p.name for p in notifier._providers]
        assert "telegram" not in names

    def test_discord_enabled_with_url(self) -> None:
        config = _make_config(
            discord=DiscordConfig(enabled=True, webhook_url="https://hook"),
        )
        notifier = Notifier(config)
        names = [p.name for p in notifier._providers]
        assert "discord" in names

    def test_discord_enabled_without_url_skipped(self) -> None:
        config = _make_config(
            discord=DiscordConfig(enabled=True, webhook_url=None),
        )
        notifier = Notifier(config)
        names = [p.name for p in notifier._providers]
        assert "discord" not in names

    def test_slack_enabled_with_url(self) -> None:
        config = _make_config(
            slack=SlackConfig(enabled=True, webhook_url="https://hook"),
        )
        notifier = Notifier(config)
        names = [p.name for p in notifier._providers]
        assert "slack" in names

    def test_all_providers_enabled(self) -> None:
        config = _make_config(
            macos=MacOSNotifyConfig(enabled=True),
            telegram=TelegramConfig(enabled=True, bot_token="t", chat_id="c"),
            discord=DiscordConfig(enabled=True, webhook_url="https://d"),
            slack=SlackConfig(enabled=True, webhook_url="https://s"),
        )
        notifier = Notifier(config)
        assert notifier.provider_count == 4

    def test_no_providers_when_all_disabled(self) -> None:
        config = _make_config(
            macos=MacOSNotifyConfig(enabled=False),
            telegram=TelegramConfig(enabled=False),
            discord=DiscordConfig(enabled=False),
            slack=SlackConfig(enabled=False),
        )
        notifier = Notifier(config)
        assert notifier.provider_count == 0

    def test_global_enabled_false_disables_all(self) -> None:
        """전역 enabled=False이면 개별 provider 설정과 무관하게 모두 비활성."""
        config = _make_config(
            enabled=False,
            macos=MacOSNotifyConfig(enabled=True),
            telegram=TelegramConfig(enabled=True, bot_token="t", chat_id="c"),
            discord=DiscordConfig(enabled=True, webhook_url="https://hook"),
            slack=SlackConfig(enabled=True, webhook_url="https://hook"),
        )
        notifier = Notifier(config)
        assert notifier.provider_count == 0


# =========================================================================
# Config Parsing
# =========================================================================


class TestNotificationConfigParsing:
    def test_full_notification_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[notification]
enabled = true
on_transcode_complete = true
on_merge_complete = false
on_upload_complete = true
on_error = true

[notification.macos]
enabled = true
sound = false

[notification.telegram]
enabled = true
bot_token = "123:ABC"
chat_id = "456"

[notification.discord]
enabled = true
webhook_url = "https://discord.com/api/webhooks/123"

[notification.slack]
enabled = true
webhook_url = "https://hooks.slack.com/services/T/B/X"
""")
        config = load_config(config_file)
        n = config.notification
        assert n.enabled is True
        assert n.on_merge_complete is False
        assert n.macos.enabled is True
        assert n.macos.sound is False
        assert n.telegram.bot_token == "123:ABC"
        assert n.telegram.chat_id == "456"
        assert n.discord.webhook_url == "https://discord.com/api/webhooks/123"
        assert n.slack.webhook_url == "https://hooks.slack.com/services/T/B/X"

    def test_missing_notification_section(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text("[general]\n")
        config = load_config(config_file)
        n = config.notification
        assert n.enabled is None
        assert n.macos.enabled is None

    def test_partial_notification_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[notification]
enabled = true

[notification.macos]
sound = false
""")
        config = load_config(config_file)
        n = config.notification
        assert n.enabled is True
        assert n.macos.sound is False
        assert n.macos.enabled is None  # 미지정

    def test_type_errors_ignored(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[notification]
enabled = "not_a_bool"
""")
        config = load_config(config_file)
        # 타입 오류 시 None (무시)
        assert config.notification.enabled is None

    def test_malformed_sub_config_ignored(self, tmp_path: Path) -> None:
        """서브 설정에 잘못된 타입이 있어도 다른 설정은 정상 파싱."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""
[notification]
enabled = true

[notification.macos]
enabled = "yes_please"

[notification.telegram]
enabled = true
bot_token = "valid_token"
chat_id = "valid_id"
""")
        config = load_config(config_file)
        n = config.notification
        assert n.enabled is True
        # macos.enabled는 타입 오류로 None
        assert n.macos.enabled is None
        # telegram은 정상
        assert n.telegram.bot_token == "valid_token"


# =========================================================================
# CLI Options
# =========================================================================


class TestNotifyCLI:
    def test_notify_flag_default_false(self) -> None:
        from tubearchive.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["/tmp"])
        assert args.notify is False

    def test_notify_flag_set(self) -> None:
        from tubearchive.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["--notify", "/tmp"])
        assert args.notify is True

    def test_notify_test_flag(self) -> None:
        from tubearchive.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["--notify-test"])
        assert args.notify_test is True
