"""영상 메타데이터 감지기.

ffprobe를 서브프로세스로 실행하여 영상 파일의 기술 메타데이터를 추출한다.

추출 항목:
    - **해상도**: width x height (예: 3840x2160)
    - **코덱**: codec_name (hevc, h264 등)
    - **프레임레이트**: r_frame_rate → float 변환 (분수 ``30000/1001`` 지원)
    - **픽셀 포맷**: pix_fmt (yuv420p, yuv420p10le 등)
    - **색 공간**: color_transfer, color_primaries, color_space
    - **길이**: duration (초)
    - **비트레이트**: bit_rate (bps)

반환:
    :class:`~tubearchive.models.video.VideoMetadata` 데이터클래스
"""

import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from tubearchive.models.video import VideoMetadata


def detect_metadata(video_path: Path) -> VideoMetadata:
    """ffprobe로 영상 메타데이터를 감지한다.

    비디오 스트림에서 해상도·코덱·픽셀 포맷·색 공간·프레임 레이트를,
    ``format`` 태그에서 기기명·재생 시간을 추출하여
    :class:`~tubearchive.models.video.VideoMetadata` 로 반환한다.

    Args:
        video_path: 분석할 영상 파일 경로.

    Returns:
        추출된 메타데이터 (해상도, 코덱, 색 공간, 기기명 등).

    Raises:
        RuntimeError: ffprobe 실행 실패 또는 비디오 스트림 없음.
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

    # 오디오 스트림 존재 여부
    has_audio = any(s.get("codec_type") == "audio" for s in probe_data.get("streams", []))

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
        has_audio=has_audio,
    )


def _run_ffprobe(video_path: Path) -> dict[str, Any]:
    """ffprobe를 실행하여 스트림·포맷 정보를 JSON으로 반환한다.

    ``-show_streams -show_format`` 옵션으로 모든 스트림과
    컨테이너 메타데이터를 한 번에 추출한다.

    Args:
        video_path: 분석할 영상 파일 경로.

    Returns:
        ffprobe JSON 출력 (``streams``, ``format`` 키 포함).

    Raises:
        RuntimeError: ffprobe 실행 실패 또는 JSON 파싱 오류.
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
    """FFmpeg ``r_frame_rate`` 분수 문자열을 float FPS로 변환한다.

    ``"30000/1001"`` → ``29.97``, ``"60/1"`` → ``60.0`` 등
    분수 형식을 :class:`fractions.Fraction` 으로 파싱한다.

    Args:
        frame_rate_str: 분수 또는 정수 형식 프레임 레이트 문자열.

    Returns:
        초당 프레임 수. 파싱 실패 시 ``0.0``.
    """
    try:
        fraction = Fraction(frame_rate_str)
        return float(fraction)
    except (ValueError, ZeroDivisionError):
        return 0.0
