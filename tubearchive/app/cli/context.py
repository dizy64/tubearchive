"""파이프라인 실행 컨텍스트 및 진행률 이벤트 타입."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tubearchive.infra.notification.notifier import Notifier
    from tubearchive.shared.progress import ProgressInfo


@dataclass(frozen=True)
class FileStartEvent:
    """파일 트랜스코딩 시작 이벤트."""

    filename: str
    file_index: int
    total_files: int


@dataclass(frozen=True)
class FileProgressEvent:
    """파일 트랜스코딩 진행률 이벤트."""

    filename: str
    info: ProgressInfo


@dataclass(frozen=True)
class FileDoneEvent:
    """파일 트랜스코딩 완료 이벤트."""

    filename: str
    success: bool


ProgressEvent = FileStartEvent | FileProgressEvent | FileDoneEvent


@dataclass
class PipelineContext:
    """파이프라인 사이드-채널: 진행률 콜백 + 알림 오케스트레이터."""

    notifier: Notifier | None = field(default=None)
    on_progress: Callable[[ProgressEvent], None] | None = field(default=None)
