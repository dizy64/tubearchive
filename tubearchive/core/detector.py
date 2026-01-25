"""영상 메타데이터 감지기."""

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from tubearchive.models.video import VideoMetadata


def detect_metadata(video_path: Path) -> VideoMetadata:
    """
    ffprobe로 영상 메타데이터 감지.

    Args:
        video_path: 영상 파일 경로

    Returns:
        VideoMetadata 객체

    Raises:
        RuntimeError: ffprobe 실행 실패
    """
    probe_data = _run_ffprobe(video_path)

    # 비디오 스트림 찾기
    video_stream = None
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    if not video_stream:
        raise RuntimeError(f"No video stream found in {video_path}")

    # 기본 정보 추출
    width = video_stream["width"]
    height = video_stream["height"]
    codec = video_stream["codec_name"]
    pixel_format = video_stream.get("pix_fmt", "unknown")

    # FPS 계산
    r_frame_rate = video_stream.get("r_frame_rate", "0/1")
    avg_frame_rate = video_stream.get("avg_frame_rate", "0/1")
    fps = _parse_frame_rate(r_frame_rate)

    # VFR 감지 (r_frame_rate != avg_frame_rate)
    is_vfr = _parse_frame_rate(r_frame_rate) != _parse_frame_rate(avg_frame_rate)

    # Duration
    duration_seconds = float(
        video_stream.get("duration") or probe_data.get("format", {}).get("duration", "0")
    )

    # 회전 메타데이터 확인
    rotation = int(video_stream.get("tags", {}).get("rotate", "0"))
    is_rotated_vertical = rotation in (90, 270)

    # 세로 영상 감지
    is_portrait = is_rotated_vertical or (width < height)

    # 기기 모델 감지
    device_model = probe_data.get("format", {}).get("tags", {}).get("com.apple.quicktime.model")

    # 컬러 정보
    color_space = video_stream.get("color_space")
    color_transfer = video_stream.get("color_transfer")
    color_primaries = video_stream.get("color_primaries")

    return VideoMetadata(
        width=width,
        height=height,
        duration_seconds=duration_seconds,
        fps=fps,
        codec=codec,
        pixel_format=pixel_format,
        is_portrait=is_portrait,
        is_vfr=is_vfr,
        device_model=device_model,
        color_space=color_space,
        color_transfer=color_transfer,
        color_primaries=color_primaries,
    )


def _run_ffprobe(video_path: Path) -> dict[str, Any]:
    """
    ffprobe 실행 및 JSON 파싱.

    Args:
        video_path: 영상 파일 경로

    Returns:
        ffprobe JSON 출력

    Raises:
        RuntimeError: ffprobe 실행 실패
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(video_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        data: dict[str, Any] = json.loads(result.stdout)
        return data
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffprobe failed: {e.stderr}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse ffprobe output: {e}") from e


def _parse_frame_rate(frame_rate_str: str) -> float:
    """
    프레임 레이트 문자열 파싱.

    Args:
        frame_rate_str: "60/1", "30000/1001" 등

    Returns:
        FPS (float)
    """
    try:
        fraction = Fraction(frame_rate_str)
        return float(fraction)
    except (ValueError, ZeroDivisionError):
        return 0.0
