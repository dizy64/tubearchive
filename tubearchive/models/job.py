"""트랜스코딩 및 병합 작업 모델."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path


class JobStatus(Enum):
    """작업 상태."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TranscodingJob:
    """트랜스코딩 작업."""

    id: int | None
    video_id: int
    temp_file_path: Path | None
    status: JobStatus
    progress_percent: int
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None

    def __post_init__(self) -> None:
        """검증."""
        if not 0 <= self.progress_percent <= 100:
            raise ValueError(f"Invalid progress: {self.progress_percent}")

    @property
    def is_resumable(self) -> bool:
        """Resume 가능 여부."""
        return self.status == JobStatus.PROCESSING and self.temp_file_path is not None


@dataclass
class MergeJob:
    """병합 작업."""

    id: int | None
    output_path: Path
    video_ids: list[int]
    status: JobStatus
    youtube_id: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        """검증."""
        if not self.video_ids:
            raise ValueError("video_ids cannot be empty")
