"""영상 파일 및 메타데이터 도메인 모델.

트랜스코딩 파이프라인에서 사용하는 불변(frozen) 데이터클래스 정의.

클래스:
    - :class:`VideoFile`: 원본 영상 파일 경로·크기·생성 시간
    - :class:`FadeConfig`: 파일별 페이드인/아웃 시간 (초)
    - :class:`VideoMetadata`: ffprobe로 추출한 코덱·해상도·색 공간 등 기술 정보
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class VideoFile:
    """원본 영상 파일 정보.

    Attributes:
        path: 영상 파일의 절대 경로 (존재 검증됨)
        creation_time: 파일 생성 시간 (macOS ``st_birthtime`` 기반)
        size_bytes: 파일 크기 (바이트)
    """

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
    """파일별 Dip-to-Black 페이드 설정.

    그룹 경계에서는 페이드를 적용하고, 그룹 내부 연결부에서는
    ``0.0`` 으로 설정하여 끊김 없이 이어붙인다.

    Attributes:
        fade_in: 페이드인 시간 (초). ``0.0`` 이면 적용하지 않음.
        fade_out: 페이드아웃 시간 (초). ``0.0`` 이면 적용하지 않음.
    """

    fade_in: float = 0.5
    fade_out: float = 0.5


@dataclass(frozen=True)
class VideoMetadata:
    """ffprobe로 감지한 영상 기술 메타데이터.

    인코딩 프로파일 선택, 필터 체인 구성, HDR 판별 등에 사용된다.

    Attributes:
        width: 가로 해상도 (px)
        height: 세로 해상도 (px)
        duration_seconds: 영상 길이 (초)
        fps: 프레임레이트 (예: 29.97)
        codec: 코덱명 (``hevc``, ``h264`` 등)
        pixel_format: 픽셀 포맷 (``yuv420p``, ``yuv420p10le`` 등)
        is_portrait: 세로 영상 여부 (height > width)
        is_vfr: VFR(가변 프레임레이트) 여부
        device_model: 촬영 기기명 (``com.apple.quicktime.model`` 등, 없으면 ``None``)
        color_space: 색 공간 (``bt709``, ``bt2020nc`` 등)
        color_transfer: 색 전달 함수 (``smpte2084``, ``arib-std-b67`` 등)
        color_primaries: 색 원색 (``bt709``, ``bt2020`` 등)
        has_audio: 오디오 스트림 존재 여부 (기본 ``True``)
    """

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
    location: str | None = None
    has_audio: bool = True

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
