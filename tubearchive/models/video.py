"""영상 파일 및 메타데이터 모델."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class VideoFile:
    """원본 영상 파일 정보."""

    path: Path
    creation_time: datetime
    size_bytes: int

    def __post_init__(self) -> None:
        """검증."""
        if not self.path.exists():
            raise FileNotFoundError(f"Video file not found: {self.path}")
        if not self.path.is_file():
            raise ValueError(f"Path is not a file: {self.path}")


@dataclass(frozen=True)
class FadeConfig:
    """파일별 페이드 설정."""

    fade_in: float = 0.5
    fade_out: float = 0.5


@dataclass(frozen=True)
class VideoMetadata:
    """ffprobe로 감지한 영상 메타데이터."""

    width: int
    height: int
    duration_seconds: float
    fps: float
    codec: str
    pixel_format: str
    is_portrait: bool
    is_vfr: bool  # Variable Frame Rate
    device_model: str | None
    color_space: str | None
    color_transfer: str | None
    color_primaries: str | None

    def __post_init__(self) -> None:
        """검증."""
        if self.width <= 0 or self.height <= 0:
            raise ValueError(f"Invalid dimensions: {self.width}x{self.height}")
        if self.duration_seconds <= 0:
            raise ValueError(f"Invalid duration: {self.duration_seconds}")
        if self.fps <= 0:
            raise ValueError(f"Invalid FPS: {self.fps}")

    @property
    def aspect_ratio(self) -> float:
        """종횡비."""
        return self.width / self.height

    @property
    def resolution(self) -> tuple[int, int]:
        """해상도 (width, height)."""
        return (self.width, self.height)
