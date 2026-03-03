"""입력 검증 유틸리티.

비디오 파일 경로·확장자 검증, FFmpeg/ffprobe 설치 여부 확인,
디스크 용량 체크 등 파이프라인 실행 전 사전 조건을 검증한다.
"""

import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


# 지원되는 비디오 확장자
VIDEO_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".mov",
        ".mts",
        ".m4v",
        ".mkv",
        ".avi",
        ".webm",
        ".m2ts",
        ".mxf",
    }
)


class ValidationError(Exception):
    """입력 검증 실패 시 발생하는 예외."""

    pass


def validate_video_file(path: Path) -> Path:
    """
    비디오 파일 검증.

    Args:
        path: 검증할 파일 경로

    Returns:
        검증된 경로

    Raises:
        ValidationError: 검증 실패
    """
    if not path.exists():
        raise ValidationError(f"File does not exist: {path}")

    if not path.is_file():
        raise ValidationError(f"Path is not a file: {path}")

    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValidationError(
            f"File is not a valid video format: {path} "
            f"(supported: {', '.join(sorted(VIDEO_EXTENSIONS))})"
        )

    if path.stat().st_size == 0:
        raise ValidationError(f"File is empty: {path}")

    return path


def validate_output_path(
    path: Path,
    allow_overwrite: bool = True,
) -> Path:
    """
    출력 경로 검증.

    Args:
        path: 출력 파일 경로
        allow_overwrite: 기존 파일 덮어쓰기 허용 여부

    Returns:
        검증된 경로

    Raises:
        ValidationError: 검증 실패
    """
    if not path.parent.exists():
        raise ValidationError(f"Output directory does not exist: {path.parent}")

    if path.exists() and not allow_overwrite:
        raise ValidationError(f"Output file already exists: {path}")

    return path


def check_disk_space(path: Path, required_bytes: int) -> None:
    """
    디스크 공간 확인.

    Args:
        path: 확인할 경로 (디렉토리)
        required_bytes: 필요한 바이트 수

    Raises:
        ValidationError: 공간 부족
    """
    # 경로가 파일이면 부모 디렉토리 사용
    check_path = path.parent if path.is_file() else path
    if not check_path.exists():
        check_path = Path.cwd()

    usage = shutil.disk_usage(check_path)
    available = usage.free

    if available < required_bytes:
        available_mb = available / (1024 * 1024)
        required_mb = required_bytes / (1024 * 1024)
        raise ValidationError(
            f"Insufficient disk space: {available_mb:.1f}MB available, {required_mb:.1f}MB required"
        )

    logger.debug(
        f"Disk space check passed: {available / (1024 * 1024):.1f}MB available, "
        f"{required_bytes / (1024 * 1024):.1f}MB required"
    )


def estimate_required_space(
    video_paths: list[Path],
    multiplier: float = 1.5,
) -> int:
    """
    필요한 디스크 공간 추정.

    Args:
        video_paths: 비디오 파일 경로 목록
        multiplier: 원본 크기 대비 배수 (기본: 1.5)

    Returns:
        필요한 바이트 수
    """
    total_size = sum(p.stat().st_size for p in video_paths if p.exists())
    return int(total_size * multiplier)


def validate_ffmpeg_available(command: str = "ffmpeg") -> str:
    """
    FFmpeg 가용성 확인.

    Args:
        command: 확인할 명령어 (ffmpeg 또는 ffprobe)

    Returns:
        FFmpeg 실행 파일 경로

    Raises:
        ValidationError: FFmpeg을 찾을 수 없음
    """
    path = shutil.which(command)
    if path is None:
        raise ValidationError(
            f"{command.upper()} not found. Please install FFmpeg: https://ffmpeg.org/download.html"
        )

    logger.debug(f"Found {command}: {path}")
    return path
