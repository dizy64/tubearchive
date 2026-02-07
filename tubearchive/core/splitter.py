"""영상 분할 모듈.

시간 또는 파일 크기 기준으로 영상을 분할하는 기능 제공.
ffmpeg segment muxer를 활용하여 재인코딩 없이 키프레임 기준 분할.
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def probe_duration(file_path: Path) -> float:
    """ffprobe로 영상 길이(초)를 조회한다.

    분할된 파일의 실제 길이를 확인하는 용도. segment muxer는 키프레임 기준으로
    분할하므로 요청 시간과 실제 길이가 다를 수 있다.

    Args:
        file_path: 영상 파일 경로

    Returns:
        초 단위 영상 길이. 실패 시 0.0.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError):
        return 0.0


@dataclass
class SplitOptions:
    """영상 분할 옵션.

    Attributes:
        duration: 분할 기준 시간(초). None이면 시간 기준 미사용.
        size: 분할 기준 크기(바이트). None이면 크기 기준 미사용.
    """

    duration: int | None = None
    size: int | None = None


class VideoSplitter:
    """영상 분할 클래스.

    ffmpeg의 segment muxer를 사용하여 재인코딩 없이
    키프레임 기준으로 영상을 분할.
    """

    def parse_duration(self, duration_str: str) -> int:
        """시간 문자열을 초 단위로 변환.

        Args:
            duration_str: 시간 문자열 (예: "1h", "30m", "1h30m", "90s", "3600")

        Returns:
            초 단위 시간

        Raises:
            ValueError: 잘못된 형식이거나 음수인 경우
        """
        if not duration_str:
            raise ValueError("Invalid duration format: empty string")

        # 단위 없는 숫자는 초로 해석
        if duration_str.isdigit():
            seconds = int(duration_str)
            if seconds <= 0:
                raise ValueError("Duration must be positive")
            return seconds

        # 시간 단위 파싱: 1h, 30m, 1h30m, 2h15m30s 등 (음수 포함)
        pattern = r"(?:(-?\d+)h)?(?:(-?\d+)m)?(?:(-?\d+)s)?"
        match = re.fullmatch(pattern, duration_str.lower())

        if not match:
            raise ValueError(f"Invalid duration format: {duration_str}")

        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)

        # 모든 값이 0이면 잘못된 형식
        if hours == 0 and minutes == 0 and seconds == 0:
            raise ValueError(f"Invalid duration format: {duration_str}")

        total_seconds = hours * 3600 + minutes * 60 + seconds

        if total_seconds <= 0:
            raise ValueError("Duration must be positive")

        return total_seconds

    def parse_size(self, size_str: str) -> int:
        """크기 문자열을 바이트로 변환.

        Args:
            size_str: 크기 문자열 (예: "10G", "256M", "1.5G", "1024K", "1024")

        Returns:
            바이트 단위 크기

        Raises:
            ValueError: 잘못된 형식이거나 음수/0인 경우
        """
        if not size_str:
            raise ValueError("Invalid size format: empty string")

        # 단위 없는 숫자는 바이트로 해석
        try:
            size_bytes = int(size_str)
            if size_bytes <= 0:
                raise ValueError("Size must be positive")
            return size_bytes
        except ValueError:
            pass  # 단위가 있는 경우로 계속 진행

        # 크기 단위 파싱: 10G, 256M, 1.5G 등 (음수 포함)
        pattern = r"^(-?[\d.]+)([KMGB]?)$"
        match = re.match(pattern, size_str.upper())

        if not match:
            raise ValueError(f"Invalid size format: {size_str}")

        value_str, unit = match.groups()

        try:
            value = float(value_str)
        except ValueError:
            raise ValueError(f"Invalid size format: {size_str}") from None

        if value <= 0:
            raise ValueError("Size must be positive")

        # 단위별 승수
        multipliers = {
            "K": 1024,
            "M": 1024**2,
            "G": 1024**3,
            "B": 1,  # 바이트
            "": 1,  # 단위 없음 = 바이트
        }

        size_bytes = int(value * multipliers[unit])

        if size_bytes <= 0:
            raise ValueError("Size must be positive")

        return size_bytes

    def generate_split_filenames(self, base_path: Path, count: int) -> list[Path]:
        """분할 파일명 목록 생성.

        Args:
            base_path: 원본 파일 경로 (예: /output/video.mp4)
            count: 분할 파일 개수

        Returns:
            분할 파일 경로 목록 (예: [/output/video_001.mp4, /output/video_002.mp4, ...])

        Raises:
            ValueError: count가 2 미만인 경우
        """
        if count < 2:
            raise ValueError("Count must be at least 2")

        # 경로 분리
        parent = base_path.parent
        stem = base_path.stem
        suffix = base_path.suffix

        # 3자리 숫자 패딩 (001, 002, ...)
        filenames = []
        for i in range(1, count + 1):
            filename = parent / f"{stem}_{i:03d}{suffix}"
            filenames.append(filename)

        return filenames

    def build_ffmpeg_command(
        self,
        input_path: Path,
        output_pattern: Path,
        options: SplitOptions,
    ) -> list[str]:
        """FFmpeg segment 명령어 생성.

        Args:
            input_path: 입력 영상 경로
            output_pattern: 출력 파일 패턴 (예: /output/video_%03d.mp4)
            options: 분할 옵션

        Returns:
            FFmpeg 명령어 리스트

        Raises:
            ValueError: 분할 기준이 하나도 설정되지 않은 경우
        """
        if options.duration is None and options.size is None:
            raise ValueError("At least one split criterion (duration or size) must be specified")

        cmd = [
            "ffmpeg",
            "-i",
            str(input_path),
            "-f",
            "segment",
        ]

        # 시간 기준 분할 (우선순위: duration > size)
        if options.duration is not None:
            cmd.extend(["-segment_time", str(options.duration)])
        elif options.size is not None:
            # 크기 기준 분할 (근사치, 정확하지 않음)
            # segment muxer는 정확한 크기 제어가 어려우므로
            # -fs (파일 크기 제한) 사용
            cmd.extend(["-fs", str(options.size)])

        # 키프레임 기준 분할 (정확한 시점이 아닐 수 있음)
        cmd.extend(
            [
                "-segment_list_type",
                "flat",
                "-reset_timestamps",
                "1",
                "-c",
                "copy",  # 재인코딩 없이 stream copy
                str(output_pattern),
            ]
        )

        return cmd

    def split_video(
        self,
        input_path: Path,
        output_dir: Path,
        options: SplitOptions,
    ) -> list[Path]:
        """영상을 분할하고 분할된 파일 목록 반환.

        Args:
            input_path: 입력 영상 경로
            output_dir: 출력 디렉토리
            options: 분할 옵션

        Returns:
            분할된 파일 경로 목록

        Raises:
            FileNotFoundError: 입력 파일이 존재하지 않는 경우
            NotADirectoryError: 출력 디렉토리가 존재하지 않는 경우
            RuntimeError: FFmpeg 실행 실패
        """
        # 입력 파일 검증
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {input_path}")

        # 출력 디렉토리 검증
        if not output_dir.exists() or not output_dir.is_dir():
            raise NotADirectoryError(f"Output directory not found: {output_dir}")

        # 출력 파일 패턴 생성
        output_pattern = output_dir / f"{input_path.stem}_%03d{input_path.suffix}"

        # FFmpeg 명령어 생성
        cmd = self.build_ffmpeg_command(input_path, output_pattern, options)

        # FFmpeg 실행
        logger.info(f"Splitting video: {input_path}")
        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg stderr: {result.stderr}")
            raise RuntimeError(f"FFmpeg failed with exit code {result.returncode}: {result.stderr}")

        # 생성된 분할 파일 목록 수집
        split_files = sorted(output_dir.glob(f"{input_path.stem}_[0-9][0-9][0-9]*"))

        logger.info(f"Split into {len(split_files)} files")
        return split_files
