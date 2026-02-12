"""알림 제공자(Provider) 인터페이스 및 구현체.

macOS 알림센터, Telegram, Discord, Slack 웹훅을 지원한다.
외부 의존성 없이 subprocess(osascript)와 urllib.request로 구현.
"""

from __future__ import annotations

import json
import logging
import subprocess
import urllib.error
import urllib.request
from typing import Protocol

from tubearchive.notification.events import NotificationEvent

logger = logging.getLogger(__name__)

# Telegram Bot API URL 템플릿
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# HTTP 요청 타임아웃 (초)
WEBHOOK_TIMEOUT_SECONDS = 10


class NotificationProvider(Protocol):
    """알림 제공자 프로토콜.

    모든 알림 제공자는 이 프로토콜을 따른다.
    """

    @property
    def name(self) -> str:
        """제공자 이름 (로그·테스트 식별용)."""
        ...

    def send(self, event: NotificationEvent) -> bool:
        """알림 전송.

        Args:
            event: 전송할 이벤트

        Returns:
            전송 성공 시 True, 실패 시 False.
            실패해도 예외를 발생시키지 않는다.
        """
        ...


class MacOSProvider:
    """macOS 알림센터 제공자.

    ``osascript -e 'display notification ...'`` 으로 알림을 전송한다.
    macOS가 아닌 환경에서는 send()가 항상 False를 반환한다.
    """

    def __init__(self, *, sound: bool = True) -> None:
        self._sound = sound

    @property
    def name(self) -> str:
        return "macos"

    def send(self, event: NotificationEvent) -> bool:
        """osascript로 macOS 알림 전송."""
        try:
            script = self._build_script(event)
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                logger.warning(
                    "macOS 알림 전송 실패: %s",
                    result.stderr.strip(),
                )
                return False
            return True
        except FileNotFoundError:
            logger.warning("osascript를 찾을 수 없습니다 (macOS 전용)")
            return False
        except subprocess.TimeoutExpired:
            logger.warning("macOS 알림 전송 타임아웃")
            return False
        except Exception:
            logger.exception("macOS 알림 전송 중 예외 발생")
            return False

    def _build_script(self, event: NotificationEvent) -> str:
        """AppleScript 명령 생성."""
        title = event.title.replace('"', '\\"')
        message = event.message.replace('"', '\\"')
        sound_clause = ' sound name "default"' if self._sound else ""
        return (
            f'display notification "{message}" '
            f'with title "TubeArchive" '
            f'subtitle "{title}"'
            f"{sound_clause}"
        )


class TelegramProvider:
    """Telegram Bot API 제공자.

    urllib.request로 sendMessage API를 호출한다.
    """

    def __init__(self, *, bot_token: str, chat_id: str) -> None:
        if not bot_token:
            raise ValueError("Telegram bot_token이 비어 있습니다")
        if not chat_id:
            raise ValueError("Telegram chat_id가 비어 있습니다")
        self._bot_token = bot_token
        self._chat_id = chat_id

    @property
    def name(self) -> str:
        return "telegram"

    def send(self, event: NotificationEvent) -> bool:
        """Telegram Bot API sendMessage 호출."""
        url = TELEGRAM_API_URL.format(token=self._bot_token)
        text = f"*{event.title}*\n{event.message}"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
        return _post_json(url, payload, provider_name=self.name)


class DiscordProvider:
    """Discord Webhook 제공자."""

    def __init__(self, *, webhook_url: str) -> None:
        if not webhook_url:
            raise ValueError("Discord webhook_url이 비어 있습니다")
        self._webhook_url = webhook_url

    @property
    def name(self) -> str:
        return "discord"

    def send(self, event: NotificationEvent) -> bool:
        """Discord Webhook POST."""
        payload = {
            "content": f"**{event.title}**\n{event.message}",
        }
        return _post_json(self._webhook_url, payload, provider_name=self.name)


class SlackProvider:
    """Slack Incoming Webhook 제공자."""

    def __init__(self, *, webhook_url: str) -> None:
        if not webhook_url:
            raise ValueError("Slack webhook_url이 비어 있습니다")
        self._webhook_url = webhook_url

    @property
    def name(self) -> str:
        return "slack"

    def send(self, event: NotificationEvent) -> bool:
        """Slack Webhook POST."""
        payload = {
            "text": f"*{event.title}*\n{event.message}",
        }
        return _post_json(self._webhook_url, payload, provider_name=self.name)


def _post_json(url: str, payload: dict[str, str], *, provider_name: str) -> bool:
    """JSON POST 요청 전송.

    Args:
        url: 요청 URL
        payload: JSON 직렬화할 페이로드
        provider_name: 로그용 제공자 이름

    Returns:
        성공(2xx) 시 True, 실패 시 False.
    """
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(  # noqa: S310
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_SECONDS) as resp:  # noqa: S310
            status = resp.status
            if 200 <= status < 300:
                return True
            logger.warning(
                "%s 알림 전송 실패 (HTTP %d)",
                provider_name,
                status,
            )
            return False
    except urllib.error.HTTPError as e:
        logger.warning(
            "%s 알림 HTTP 오류: %d %s",
            provider_name,
            e.code,
            e.reason,
        )
        return False
    except urllib.error.URLError as e:
        logger.warning("%s 알림 네트워크 오류: %s", provider_name, e.reason)
        return False
    except Exception:
        logger.exception("%s 알림 전송 중 예외 발생", provider_name)
        return False
