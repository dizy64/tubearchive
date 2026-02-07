"""타임랩스 생성 엔진.

원본 비디오에서 배속 조절하여 타임랩스 영상을 생성한다.

주요 기능:
    - setpts 기반 배속 조절 (2x ~ 60x)
    - 오디오 제거 또는 가속 (atempo 체인)
    - 해상도 변환 (선택적)
    - FFmpeg 진행률 추적
"""

import logging
from collections.abc import Callable
from pathlib import Path

from tubearchive.core.detector import detect_metadata
from tubearchive.ffmpeg.effects import (
    TIMELAPSE_MAX_SPEED,
    TIMELAPSE_MIN_SPEED,
    create_timelapse_audio_filter,
    create_timelapse_video_filter,
)
from tubearchive.ffmpeg.executor import FFmpegError, FFmpegExecutor
from tubearchive.utils.progress import ProgressInfo

logger = logging.getLogger(__name__)

# 해상도 프리셋 매핑
RESOLUTION_PRESETS = {
    "4k": (3840, 2160),
    "1080p": (1920, 1080),
    "720p": (1280, 720),
}


class TimelapseGenerator:
    """타임랩스 영상 생성기.

    입력 비디오에서 배속 조절하여 타임랩스 영상을 생성합니다.

    Usage::

        generator = TimelapseGenerator()
        output = generator.generate(
            input_path=Path("input.mp4"),
            output_path=Path("output_timelapse.mp4"),
            speed=10,
            keep_audio=False,
        )
    """

    def __init__(self) -> None:
        """초기화."""
        self.executor = FFmpegExecutor()

    def generate(
        self,
        input_path: Path,
        output_path: Path,
        speed: int,
        keep_audio: bool = False,
        resolution: str | None = None,
        progress_callback: Callable[[ProgressInfo], None] | None = None,
    ) -> Path:
        """
        타임랩스 영상 생성.

        Args:
            input_path: 입력 비디오 경로
            output_path: 출력 비디오 경로
            speed: 배속 (2-60 범위)
            keep_audio: 오디오 유지 여부 (True면 atempo로 가속, False면 제거)
            resolution: 출력 해상도 ("4k", "1080p", "720p" 또는 None)
            progress_callback: 진행률 콜백 함수

        Returns:
            생성된 타임랩스 파일 경로

        Raises:
            ValueError: 잘못된 입력 인자
            FFmpegError: FFmpeg 실행 실패
        """
        if not input_path.exists():
            raise ValueError(f"Input file not found: {input_path}")

        if speed < TIMELAPSE_MIN_SPEED or speed > TIMELAPSE_MAX_SPEED:
            raise ValueError(
                f"Speed must be between {TIMELAPSE_MIN_SPEED} and "
                f"{TIMELAPSE_MAX_SPEED}, got {speed}"
            )

        # 메타데이터 감지
        logger.info(f"Detecting metadata: {input_path.name}")
        metadata = detect_metadata(input_path)

        if metadata.duration_seconds < 5.0:
            logger.warning(
                f"Short video ({metadata.duration_seconds:.1f}s) may not produce "
                f"meaningful timelapse"
            )

        # 비디오 필터 구성
        video_filters = [create_timelapse_video_filter(speed)]

        # 해상도 변환 (지정된 경우)
        if resolution:
            target_width, target_height = self._parse_resolution(resolution)
            scale_filter = (
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2"
            )
            video_filters.append(scale_filter)

        video_filter_str = ",".join(video_filters)

        # FFmpeg 명령 구성
        cmd = [
            "ffmpeg",
            "-i",
            str(input_path),
            "-filter:v",
            video_filter_str,
        ]

        # 오디오 처리
        if keep_audio:
            audio_filter = create_timelapse_audio_filter(speed)
            cmd.extend(["-filter:a", audio_filter])
            cmd.extend(["-c:a", "aac", "-b:a", "128k"])
        else:
            cmd.append("-an")

        # 비디오 인코딩 설정
        cmd.extend(
            [
                "-c:v",
                "libx264",  # 타임랩스는 호환성 우선 (h264)
                "-preset",
                "medium",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-y",
                str(output_path),
            ]
        )

        # FFmpeg 실행
        logger.info(f"Generating {speed}x timelapse: {output_path.name}")
        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        try:
            self.executor.run(
                cmd=cmd,
                total_duration=metadata.duration_seconds / speed,  # 예상 출력 길이
            )
        except FFmpegError as e:
            logger.error(f"Failed to generate timelapse: {e}")
            raise

        if not output_path.exists():
            raise FFmpegError(f"Output file not created: {output_path}")

        logger.info(f"Timelapse generated: {output_path}")
        return output_path

    def _parse_resolution(self, resolution: str) -> tuple[int, int]:
        """
        해상도 문자열을 (width, height) 튜플로 변환.

        Args:
            resolution: "4k", "1080p", "720p" 또는 "1920x1080" 형식

        Returns:
            (width, height) 튜플

        Raises:
            ValueError: 잘못된 해상도 형식
        """
        resolution_lower = resolution.lower()

        # 프리셋 매핑 확인
        if resolution_lower in RESOLUTION_PRESETS:
            return RESOLUTION_PRESETS[resolution_lower]

        # "1920x1080" 형식 파싱
        if "x" in resolution_lower:
            try:
                width_str, height_str = resolution_lower.split("x")
                width = int(width_str)
                height = int(height_str)
                if width <= 0 or height <= 0:
                    raise ValueError("Width and height must be positive")
                return width, height
            except (ValueError, IndexError) as e:
                raise ValueError(f"Invalid resolution format: {resolution}") from e

        raise ValueError(
            f"Unsupported resolution: {resolution}. "
            f"Use presets (4k, 1080p, 720p) or WIDTHxHEIGHT format."
        )
