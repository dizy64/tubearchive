"""FFmpeg 실행기."""

import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from tubearchive.ffmpeg.profiles import EncodingProfile

logger = logging.getLogger(__name__)


class FFmpegError(Exception):
    """FFmpeg 실행 오류."""

    def __init__(self, message: str, stderr: str | None = None) -> None:
        """초기화."""
        super().__init__(message)
        self.stderr = stderr


def parse_progress_line(line: str) -> dict[str, float] | None:
    """
    FFmpeg 진행률 라인 파싱.

    Args:
        line: FFmpeg stderr 라인

    Returns:
        파싱된 진행률 정보 또는 None
    """
    # time= 파싱
    time_match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", line)
    if not time_match:
        return None

    hours = int(time_match.group(1))
    minutes = int(time_match.group(2))
    seconds = int(time_match.group(3))
    centiseconds = int(time_match.group(4))

    time_seconds = hours * 3600 + minutes * 60 + seconds + centiseconds / 100

    result: dict[str, float] = {"time_seconds": time_seconds}

    # frame= 파싱
    frame_match = re.search(r"frame=\s*(\d+)", line)
    if frame_match:
        result["frame"] = float(frame_match.group(1))

    # fps= 파싱
    fps_match = re.search(r"fps=\s*([\d.]+)", line)
    if fps_match:
        result["fps"] = float(fps_match.group(1))

    # bitrate= 파싱
    bitrate_match = re.search(r"bitrate=\s*([\d.]+)kbits/s", line)
    if bitrate_match:
        result["bitrate"] = float(bitrate_match.group(1))

    return result


class FFmpegExecutor:
    """FFmpeg 명령 실행기."""

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        """초기화."""
        self.ffmpeg_path = ffmpeg_path

    def build_transcode_command(
        self,
        input_path: Path,
        output_path: Path,
        profile: EncodingProfile,
        video_filter: str | None = None,
        audio_filter: str | None = None,
        filter_complex: str | None = None,
        overwrite: bool = True,
        seek_start: float | None = None,
    ) -> list[str]:
        """
        트랜스코딩 FFmpeg 명령어 빌드.

        Args:
            input_path: 입력 파일 경로
            output_path: 출력 파일 경로
            profile: 인코딩 프로파일
            video_filter: 비디오 필터 (-vf)
            audio_filter: 오디오 필터 (-af)
            filter_complex: 복합 필터 (-filter_complex)
            overwrite: 덮어쓰기 여부
            seek_start: 시작 위치 (초)

        Returns:
            FFmpeg 명령어 리스트
        """
        cmd = [self.ffmpeg_path]

        # 덮어쓰기
        if overwrite:
            cmd.append("-y")

        # 시작 위치 (Resume용)
        if seek_start is not None and seek_start > 0:
            cmd.extend(["-ss", str(seek_start)])

        # 입력 파일
        cmd.extend(["-i", str(input_path)])

        # 필터
        if filter_complex:
            cmd.extend(["-filter_complex", filter_complex])
            cmd.extend(["-map", "[v_out]", "-map", "0:a"])
        elif video_filter:
            cmd.extend(["-vf", video_filter])

        if audio_filter:
            cmd.extend(["-af", audio_filter])

        # 인코딩 프로파일 적용
        cmd.extend(profile.to_ffmpeg_args())

        # 출력 파일
        cmd.append(str(output_path))

        return cmd

    def build_concat_command(
        self,
        concat_file: Path,
        output_path: Path,
        overwrite: bool = True,
    ) -> list[str]:
        """
        concat 병합 FFmpeg 명령어 빌드.

        Args:
            concat_file: concat 텍스트 파일 경로
            output_path: 출력 파일 경로
            overwrite: 덮어쓰기 여부

        Returns:
            FFmpeg 명령어 리스트
        """
        cmd = [self.ffmpeg_path]

        if overwrite:
            cmd.append("-y")

        cmd.extend(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(output_path),
            ]
        )

        return cmd

    def run(
        self,
        cmd: list[str],
        total_duration: float,
        progress_callback: Callable[[int], None] | None = None,
    ) -> None:
        """
        FFmpeg 명령 실행.

        Args:
            cmd: FFmpeg 명령어 리스트
            total_duration: 영상 전체 길이 (초)
            progress_callback: 진행률 콜백 (0-100)

        Raises:
            FFmpegError: FFmpeg 실행 실패
        """
        logger.info(f"Running FFmpeg: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        stderr_lines: list[str] = []

        # stderr에서 진행률 읽기
        if process.stderr:
            for line in process.stderr:
                stderr_lines.append(line)
                progress = parse_progress_line(line)

                if progress and progress_callback and total_duration > 0:
                    percent = self.calculate_progress_percent(
                        progress["time_seconds"],
                        total_duration,
                    )
                    progress_callback(percent)

        # 프로세스 완료 대기
        return_code = process.wait()

        if return_code != 0:
            stderr_output = "".join(stderr_lines)
            logger.error(f"FFmpeg failed with code {return_code}: {stderr_output}")
            raise FFmpegError(
                f"FFmpeg failed with exit code {return_code}",
                stderr=stderr_output,
            )

        logger.info("FFmpeg completed successfully")

    def calculate_progress_percent(
        self,
        current_time: float,
        total_duration: float,
    ) -> int:
        """
        진행률 계산.

        Args:
            current_time: 현재 처리된 시간 (초)
            total_duration: 전체 길이 (초)

        Returns:
            진행률 (0-100)
        """
        if total_duration <= 0:
            return 0

        percent = int((current_time / total_duration) * 100)
        return min(percent, 100)
