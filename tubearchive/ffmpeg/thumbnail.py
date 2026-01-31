"""썸네일 추출 모듈.

영상에서 대표 프레임을 추출하여 JPEG 썸네일을 생성한다.
"""

import logging
import subprocess
from pathlib import Path

from tubearchive.core.detector import detect_metadata

logger = logging.getLogger(__name__)

DEFAULT_PERCENTAGES: tuple[float, ...] = (0.10, 0.33, 0.50)


def calculate_thumbnail_timestamps(
    duration_seconds: float,
    percentages: tuple[float, ...] = DEFAULT_PERCENTAGES,
) -> list[float]:
    """영상 길이 기반 썸네일 추출 타임스탬프 계산.

    Args:
        duration_seconds: 영상 전체 길이 (초)
        percentages: 추출 지점 비율 (기본: 10%, 33%, 50%)

    Returns:
        타임스탬프 리스트. duration <= 0이면 빈 리스트.
    """
    if duration_seconds <= 0:
        return []
    return [duration_seconds * p for p in percentages]


def parse_timestamp(timestamp_str: str) -> float:
    """타임스탬프 문자열을 초 단위로 변환.

    지원 형식: HH:MM:SS(.ms), MM:SS(.ms), SS(.ms)

    Args:
        timestamp_str: 타임스탬프 문자열

    Returns:
        초 단위 float

    Raises:
        ValueError: 잘못된 형식이거나 음수인 경우
    """
    s = timestamp_str.strip()
    if not s:
        raise ValueError(f"Invalid timestamp format: '{timestamp_str}'")

    parts = s.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, secs = float(parts[0]), float(parts[1]), float(parts[2])
            result = hours * 3600 + minutes * 60 + secs
        elif len(parts) == 2:
            minutes, secs = float(parts[0]), float(parts[1])
            result = minutes * 60 + secs
        elif len(parts) == 1:
            result = float(parts[0])
        else:
            raise ValueError(f"Invalid timestamp format: '{timestamp_str}'")
    except ValueError as e:
        if "Invalid timestamp" in str(e) or "negative" in str(e):
            raise
        raise ValueError(f"Invalid timestamp format: '{timestamp_str}'") from e

    if result < 0:
        raise ValueError(f"Timestamp must not be negative: {result}")

    return result


def generate_thumbnail_paths(
    video_path: Path,
    count: int,
    output_dir: Path | None = None,
) -> list[Path]:
    """썸네일 출력 경로 생성.

    {stem}_thumb_01.jpg, {stem}_thumb_02.jpg, ... 형식.

    Args:
        video_path: 원본 영상 경로
        count: 생성할 썸네일 수
        output_dir: 출력 디렉토리 (None이면 영상 디렉토리)

    Returns:
        경로 리스트
    """
    if count <= 0:
        return []

    stem = video_path.stem
    parent = output_dir if output_dir is not None else video_path.parent

    return [parent / f"{stem}_thumb_{i:02d}.jpg" for i in range(1, count + 1)]


def build_thumbnail_command(
    input_path: Path,
    output_path: Path,
    timestamp: float,
    quality: int = 2,
    ffmpeg_path: str = "ffmpeg",
) -> list[str]:
    """FFmpeg 썸네일 추출 명령 생성.

    -ss를 -i 앞에 배치하여 fast seeking 활용.

    Args:
        input_path: 입력 영상 경로
        output_path: 출력 이미지 경로
        timestamp: 추출 시점 (초)
        quality: JPEG 품질 (1-31, 낮을수록 고품질)
        ffmpeg_path: ffmpeg 실행 파일 경로

    Returns:
        명령 인자 리스트
    """
    return [
        ffmpeg_path,
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        str(input_path),
        "-vframes",
        "1",
        "-q:v",
        str(quality),
        str(output_path),
    ]


def extract_thumbnails(
    video_path: Path,
    timestamps: list[float] | None = None,
    output_dir: Path | None = None,
    quality: int = 2,
    ffmpeg_path: str = "ffmpeg",
) -> list[Path]:
    """영상에서 썸네일 추출.

    Args:
        video_path: 입력 영상 경로
        timestamps: 추출 시점 리스트 (None이면 자동 계산)
        output_dir: 출력 디렉토리 (None이면 영상 디렉토리)
        quality: JPEG 품질 (1-31)
        ffmpeg_path: ffmpeg 실행 파일 경로

    Returns:
        성공한 썸네일 경로 리스트
    """
    if not video_path.exists():
        logger.warning("Video file not found: %s", video_path)
        return []

    if timestamps is None:
        try:
            meta = detect_metadata(video_path)
            timestamps = calculate_thumbnail_timestamps(meta.duration_seconds)
        except (RuntimeError, OSError) as e:
            logger.warning("Failed to detect metadata for %s: %s", video_path, e)
            return []

    if not timestamps:
        return []

    paths = generate_thumbnail_paths(video_path, len(timestamps), output_dir)
    results: list[Path] = []

    for ts, out_path in zip(timestamps, paths, strict=True):
        cmd = build_thumbnail_command(video_path, out_path, ts, quality, ffmpeg_path)
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                check=False,
            )
            if proc.returncode == 0 and out_path.exists():
                results.append(out_path)
                logger.info("Thumbnail created: %s (at %.1fs)", out_path.name, ts)
            else:
                logger.warning(
                    "Failed to extract thumbnail at %.1fs: returncode=%d",
                    ts,
                    proc.returncode,
                )
        except OSError as e:
            logger.warning("Failed to run ffmpeg for thumbnail at %.1fs: %s", ts, e)

    return results
