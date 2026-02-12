"""알림 오케스트레이터.

설정에 따라 활성화된 Provider를 구축하고, 이벤트를 분배한다.
모든 알림 전송 실패는 로그로 기록할 뿐 파이프라인을 중단하지 않는다.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tubearchive.notification.events import EventType, NotificationEvent
from tubearchive.notification.providers import (
    DiscordProvider,
    MacOSProvider,
    NotificationProvider,
    SlackProvider,
    TelegramProvider,
)

if TYPE_CHECKING:
    from tubearchive.config import NotificationConfig

logger = logging.getLogger(__name__)


class Notifier:
    """알림 오케스트레이터.

    설정에 따라 Provider 인스턴스를 생성하고,
    이벤트를 모든 활성 Provider에 전달한다.

    Args:
        config: 알림 설정 (NotificationConfig)
    """

    def __init__(self, config: NotificationConfig) -> None:
        self._config = config
        self._providers: list[NotificationProvider] = self._build_providers()

    @property
    def provider_count(self) -> int:
        """활성 Provider 수."""
        return len(self._providers)

    @property
    def has_providers(self) -> bool:
        """활성 Provider가 1개 이상인지 여부."""
        return len(self._providers) > 0

    def notify(self, event: NotificationEvent) -> None:
        """이벤트를 모든 활성 Provider에 전달한다.

        이벤트 타입이 설정에서 비활성화되어 있으면 전달하지 않는다.
        Provider 전송 실패는 로그로 기록하고 다음 Provider로 계속 진행한다.

        Args:
            event: 전송할 이벤트
        """
        if not self._providers:
            return

        if not self._is_event_enabled(event.event_type):
            logger.debug(
                "이벤트 %s가 비활성화되어 알림을 건너뜁니다",
                event.event_type.value,
            )
            return

        for provider in self._providers:
            try:
                success = provider.send(event)
                if success:
                    logger.debug("%s 알림 전송 성공", provider.name)
                else:
                    logger.warning("%s 알림 전송 실패", provider.name)
            except Exception:
                logger.exception(
                    "%s 알림 전송 중 예외 발생",
                    provider.name,
                )

    def test_notification(self) -> dict[str, bool]:
        """테스트 알림을 모든 활성 Provider에 전송한다.

        Returns:
            {provider_name: 성공여부} 딕셔너리
        """
        test_event = NotificationEvent(
            event_type=EventType.MERGE_COMPLETE,
            title="테스트 알림",
            message="TubeArchive 알림 시스템이 정상 동작합니다.",
        )
        results: dict[str, bool] = {}
        for provider in self._providers:
            try:
                results[provider.name] = provider.send(test_event)
            except Exception:
                logger.exception("%s 테스트 알림 실패", provider.name)
                results[provider.name] = False
        return results

    def _is_event_enabled(self, event_type: EventType) -> bool:
        """이벤트 타입이 설정에서 활성화되어 있는지 확인한다."""
        field_name = event_type.value  # e.g. "on_transcode_complete"
        value = getattr(self._config, field_name, None)
        # None이면 기본 True (활성화)
        return value is not False

    def _build_providers(self) -> list[NotificationProvider]:
        """설정에 따라 활성화된 Provider 인스턴스를 생성한다."""
        if self._config.enabled is False:
            return []

        providers: list[NotificationProvider] = []

        # macOS (기본 활성화)
        if self._config.macos.enabled is not False:
            sound = self._config.macos.sound is not False
            providers.append(MacOSProvider(sound=sound))

        # Telegram
        telegram_cfg = self._config.telegram
        if telegram_cfg.enabled and telegram_cfg.bot_token and telegram_cfg.chat_id:
            try:
                providers.append(
                    TelegramProvider(
                        bot_token=telegram_cfg.bot_token,
                        chat_id=telegram_cfg.chat_id,
                    )
                )
            except ValueError as e:
                logger.warning("Telegram Provider 초기화 실패: %s", e)

        # Discord
        discord_cfg = self._config.discord
        if discord_cfg.enabled and discord_cfg.webhook_url:
            try:
                providers.append(DiscordProvider(webhook_url=discord_cfg.webhook_url))
            except ValueError as e:
                logger.warning("Discord Provider 초기화 실패: %s", e)

        # Slack
        slack_cfg = self._config.slack
        if slack_cfg.enabled and slack_cfg.webhook_url:
            try:
                providers.append(SlackProvider(webhook_url=slack_cfg.webhook_url))
            except ValueError as e:
                logger.warning("Slack Provider 초기화 실패: %s", e)

        return providers
