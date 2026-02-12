"""알림 시스템 패키지.

파이프라인 이벤트(트랜스코딩/병합/업로드/에러)를
macOS 알림센터 및 외부 웹훅(Telegram, Discord, Slack)으로 전달한다.
"""

from tubearchive.notification.events import (
    EventType,
    NotificationEvent,
    error_event,
    merge_complete_event,
    transcode_complete_event,
    upload_complete_event,
)
from tubearchive.notification.notifier import Notifier

__all__ = [
    "EventType",
    "NotificationEvent",
    "Notifier",
    "error_event",
    "merge_complete_event",
    "transcode_complete_event",
    "upload_complete_event",
]
