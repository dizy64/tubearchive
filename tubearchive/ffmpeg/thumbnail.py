"""썸네일 추출 모듈.

영상에서 대표 프레임을 추출하여 JPEG 썸네일을 생성한다.
"""

import json
import logging
import subprocess
from pathlib import Path

from tubearchive.core.detector import detect_metadata

logger = logging.getLogger(__name__)

DEFAULT_PERCENTAGES: tuple[float, ...] = (0.10, 0.33, 0.50)
YOUTUBE_THUMBNAIL_MIN_WIDTH = 1280
YOUTUBE_THUMBNAIL_MIN_HEIGHT = 720
YOUTUBE_THUMBNAIL_MAX_SIZE_BYTES = 2 * 1024 * 1024


def _probe_image_size(image_path: Path, ffprobe_path: str = "ffprobe") -> tuple[int, int]:
    """썸네일 이미지의 가로/세로를 ffprobe로 조회한다.

    Args:
        image_path: 이미지 파일 경로
        ffprobe_path: ffprobe 실행 파일 경로

    Returns:
        (width, height)

    Raises:
        RuntimeError: ffprobe 실패 또는 메타데이터 누락
    """
    try:
        result = subprocess.run(
            [
                ffprobe_path,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                str(image_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        raise RuntimeError(f"Failed to run ffprobe: {e}") from e

    if result.returncode != 0:
        raise RuntimeError(f"Failed to probe image metadata: {result.stderr}")

    try:
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams", [])
        stream = next(
            (s for s in streams if s.get("codec_type") == "video"),
            None,
        )
        if stream is None:
            raise ValueError("No video stream found")

        width = int(stream["width"])
        height = int(stream["height"])
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Failed to parse image metadata: {e}") from e

    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid image size: {width}x{height}")

    return width, height


def _build_thumbnail_prepare_command(
    source: Path,
    output: Path,
    ffmpeg_path: str = "ffmpeg",
    quality: int = 2,
) -> list[str]:
    """YouTube 업로드용 썸네일 변환 ffmpeg 명령을 만든다."""
    return [
        ffmpeg_path,
        "-y",
        "-i",
        str(source),
        "-vf",
        f"scale={YOUTUBE_THUMBNAIL_MIN_WIDTH}:{YOUTUBE_THUMBNAIL_MIN_HEIGHT}",
        "-q:v",
        str(quality),
        str(output),
    ]


def prepare_thumbnail_for_youtube(
    thumbnail_path: Path,
    ffmpeg_path: str = "ffmpeg",
    ffprobe_path: str = "ffprobe",
) -> Path:
    """YouTube 썸네일 업로드 조건에 맞게 이미지를 정규화한다.

    - 확장자: JPG/JPEG/PNG 허용
    - 최소 크기: 1280x720 미만이면 리사이즈
    - 최대 크기: 2MB 초과하면 재인코딩

    조건을 만족하면 원본 경로를 그대로 반환하고,
    조건 미충족 시 `_youtube` 접미사가 붙은 JPEG 파일로 변환 후 반환한다.
    """
    source = thumbnail_path.expanduser().resolve()

    if not source.exists():
        raise FileNotFoundError(f"Thumbnail file not found: {thumbnail_path}")

    suffix = source.suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png"}:
        raise ValueError(f"Unsupported thumbnail format: {suffix}")

    width, height = _probe_image_size(source, ffprobe_path=ffprobe_path)
    source_is_too_large = source.stat().st_size > YOUTUBE_THUMBNAIL_MAX_SIZE_BYTES
    if (
        width >= YOUTUBE_THUMBNAIL_MIN_WIDTH
        and height >= YOUTUBE_THUMBNAIL_MIN_HEIGHT
        and not source_is_too_large
    ):
        return source

    output = source.with_name(f"{source.stem}_youtube.jpg")
    command = _build_thumbnail_prepare_command(source, output, ffmpeg_path=ffmpeg_path)
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except OSError as e:
        raise RuntimeError(f"Failed to run ffmpeg: {e}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Thumbnail conversion timed out: {e}") from e

    if result.returncode != 0:
        raise RuntimeError(f"Failed to prepare thumbnail: {result.stderr}")

    if not output.exists():
        raise RuntimeError(f"Failed to create thumbnail: {output}")

    return output


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
            hours, minutes, secs = int(parts[0]), int(parts[1]), float(parts[2])
            result = hours * 3600 + minutes * 60 + secs
        elif len(parts) == 2:
            minutes, secs = int(parts[0]), float(parts[1])
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
                text=True,
                check=False,
                timeout=30,
            )
            if proc.returncode == 0 and out_path.exists():
                results.append(out_path)
                logger.info("Thumbnail created: %s (at %.1fs)", out_path.name, ts)
            else:
                logger.warning(
                    "Failed to extract thumbnail at %.1fs: returncode=%d, stderr=%s",
                    ts,
                    proc.returncode,
                    proc.stderr,
                )
        except subprocess.TimeoutExpired:
            logger.warning("Thumbnail extraction timed out at %.1fs", ts)
        except OSError as e:
            logger.warning("Failed to run ffmpeg for thumbnail at %.1fs: %s", ts, e)

    return results
