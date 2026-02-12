"""알림 이벤트 타입 및 데이터 모델.

파이프라인 이벤트(트랜스코딩/병합/업로드/에러)를 나타내는
불변 데이터 모델과 편의 팩토리 함수를 제공한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class EventType(Enum):
    """알림 이벤트 타입."""

    TRANSCODE_COMPLETE = "on_transcode_complete"
    MERGE_COMPLETE = "on_merge_complete"
    UPLOAD_COMPLETE = "on_upload_complete"
    ERROR = "on_error"


@dataclass(frozen=True)
class NotificationEvent:
    """알림 이벤트 데이터.

    Attributes:
        event_type: 이벤트 종류
        title: 알림 제목 (짧은 한 줄)
        message: 알림 본문 (상세 정보)
        timestamp: 이벤트 발생 시각
        metadata: 추가 메타데이터 (파일 경로, 크기, 에러 메시지 등)
    """

    event_type: EventType
    title: str
    message: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, str] = field(default_factory=dict)


def transcode_complete_event(
    *,
    file_count: int,
    total_duration: float,
    output_dir: str = "",
) -> NotificationEvent:
    """트랜스코딩 완료 이벤트 생성."""
    return NotificationEvent(
        event_type=EventType.TRANSCODE_COMPLETE,
        title="트랜스코딩 완료",
        message=f"{file_count}개 파일 트랜스코딩 완료 ({total_duration:.1f}초)",
        metadata={
            "file_count": str(file_count),
            "total_duration": f"{total_duration:.1f}",
            "output_dir": output_dir,
        },
    )


def merge_complete_event(
    *,
    output_path: str,
    file_count: int,
    total_size_bytes: int = 0,
) -> NotificationEvent:
    """병합 완료 이벤트 생성."""
    return NotificationEvent(
        event_type=EventType.MERGE_COMPLETE,
        title="병합 완료",
        message=f"{file_count}개 클립 병합 완료: {output_path}",
        metadata={
            "output_path": output_path,
            "file_count": str(file_count),
            "total_size_bytes": str(total_size_bytes),
        },
    )


def upload_complete_event(
    *,
    video_title: str,
    youtube_id: str = "",
) -> NotificationEvent:
    """업로드 완료 이벤트 생성."""
    url = f"https://youtu.be/{youtube_id}" if youtube_id else ""
    return NotificationEvent(
        event_type=EventType.UPLOAD_COMPLETE,
        title="YouTube 업로드 완료",
        message=f"'{video_title}' 업로드 완료" + (f"\n{url}" if url else ""),
        metadata={
            "video_title": video_title,
            "youtube_id": youtube_id,
            "youtube_url": url,
        },
    )


def error_event(
    *,
    error_message: str,
    stage: str = "",
) -> NotificationEvent:
    """에러 이벤트 생성."""
    return NotificationEvent(
        event_type=EventType.ERROR,
        title=f"오류 발생{f' ({stage})' if stage else ''}",
        message=error_message,
        metadata={
            "stage": stage,
            "error": error_message,
        },
    )
