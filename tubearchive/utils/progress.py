"""진행률 표시 유틸리티."""

import sys
import time
from dataclasses import dataclass
from typing import TextIO


def format_time(seconds: float) -> str:
    """
    초를 시:분:초 형식으로 변환.

    Args:
        seconds: 초

    Returns:
        포맷된 시간 문자열
    """
    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


@dataclass
class ProgressInfo:
    """FFmpeg 진행률 상세 정보."""

    percent: int
    current_time: float  # 현재 처리된 시간 (초)
    total_duration: float  # 전체 영상 길이 (초)
    fps: float  # 현재 처리 속도

    # 내부 추적용 (ETA 계산)
    _start_time: float | None = None

    def calculate_eta(self) -> float | None:
        """
        예상 남은 시간 계산.

        Returns:
            예상 남은 시간 (초) 또는 None
        """
        if self.percent <= 0:
            return None
        if self.percent >= 100:
            return 0

        # 처리된 시간 기준 ETA 계산
        remaining_duration = self.total_duration - self.current_time
        if self.current_time <= 0:
            return None

        # 현재까지 처리 비율로 남은 시간 추정
        # fps 기반으로 실제 처리 속도 반영
        if self.fps > 0:
            # fps는 초당 프레임 수, 29.97fps 기준 1초 영상 = 1초 처리
            # 실제로는 fps가 높을수록 빠름
            frames_remaining = remaining_duration * 29.97  # 추정 프레임
            eta = frames_remaining / self.fps
            return max(0, eta)

        # fps 정보 없으면 비율 기반 추정
        elapsed_ratio = self.current_time / self.total_duration
        if elapsed_ratio > 0:
            # 경과 시간 기준 추정 (실제 벽시계 시간과 다름)
            return remaining_duration * (1 / elapsed_ratio - 1)

        return None


def format_size(bytes_: int) -> str:
    """
    바이트를 읽기 쉬운 형식으로 변환.

    Args:
        bytes_: 바이트 수

    Returns:
        포맷된 크기 문자열
    """
    size = float(bytes_)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


class ProgressBar:
    """터미널 프로그레스 바."""

    def __init__(
        self,
        total: int,
        desc: str = "",
        width: int = 40,
        file: TextIO | None = None,
    ) -> None:
        """
        초기화.

        Args:
            total: 전체 작업량
            desc: 설명
            width: 프로그레스 바 너비
            file: 출력 파일 (기본: stderr)
        """
        self.total = total
        self.current = 0
        self.desc = desc
        self.width = width
        self.file = file or sys.stderr

    def update(self, amount: int = 1) -> None:
        """
        진행률 증가.

        Args:
            amount: 증가량
        """
        self.current = min(self.current + amount, self.total)
        self._display()

    def set(self, value: int) -> None:
        """
        절대 진행률 설정.

        Args:
            value: 현재 값
        """
        self.current = min(value, self.total)
        self._display()

    def finish(self) -> None:
        """완료 처리."""
        self.current = self.total
        self._display()
        self.file.write("\n")
        self.file.flush()

    def render(self) -> str:
        """
        프로그레스 바 문자열 생성.

        Returns:
            렌더링된 프로그레스 바
        """
        percent = 100 if self.total == 0 else int(self.current / self.total * 100)

        filled = int(self.width * self.current / max(self.total, 1))
        bar = "█" * filled + "░" * (self.width - filled)

        parts = []
        if self.desc:
            parts.append(self.desc)
        parts.append(f"[{bar}]")
        parts.append(f"{percent:3d}%")
        parts.append(f"({self.current}/{self.total})")

        return " ".join(parts)

    def _display(self) -> None:
        """화면에 출력."""
        self.file.write(f"\r{self.render()}")
        self.file.flush()


class MultiProgressBar:
    """여러 작업의 진행률 표시."""

    def __init__(self, total_files: int, file: TextIO | None = None) -> None:
        """
        초기화.

        Args:
            total_files: 전체 파일 수
            file: 출력 파일
        """
        self.total_files = total_files
        self.current_file = 0
        self.current_file_name = ""
        self.current_file_progress = 0
        self.file = file or sys.stderr
        # 상세 정보
        self._progress_info: ProgressInfo | None = None
        self._file_start_time: float | None = None

    def start_file(self, filename: str) -> None:
        """
        새 파일 처리 시작.

        Args:
            filename: 파일명
        """
        self.current_file += 1
        self.current_file_name = filename
        self.current_file_progress = 0
        self._progress_info = None
        self._file_start_time = time.time()
        self._display()

    def update_file_progress(self, percent: int) -> None:
        """
        현재 파일 진행률 업데이트 (하위 호환).

        Args:
            percent: 진행률 (0-100)
        """
        self.current_file_progress = percent
        self._progress_info = None  # 상세 정보 없음
        self._display()

    def update_with_info(self, info: ProgressInfo) -> None:
        """
        상세 정보로 진행률 업데이트.

        Args:
            info: FFmpeg 진행률 상세 정보
        """
        self.current_file_progress = info.percent
        self._progress_info = info
        self._display()

    def finish_file(self) -> None:
        """현재 파일 완료."""
        self.current_file_progress = 100
        self._display()
        self.file.write("\n")
        self.file.flush()

    def render(self) -> str:
        """
        상태 문자열 생성.

        Returns:
            렌더링된 상태
        """
        overall = f"[{self.current_file}/{self.total_files}]"
        file_bar_width = 20
        filled = int(file_bar_width * self.current_file_progress / 100)
        bar = "█" * filled + "░" * (file_bar_width - filled)

        name = self.current_file_name
        if len(name) > 15:
            name = name[:12] + "..."

        base = f"{overall} {name}: [{bar}] {self.current_file_progress:3d}%"

        # 상세 정보가 있으면 추가
        if self._progress_info:
            info = self._progress_info
            time_str = f"{format_time(info.current_time)}/{format_time(info.total_duration)}"

            parts = [base, time_str]

            if info.fps > 0:
                parts.append(f"{info.fps:.1f}fps")

            # ETA 계산
            eta = self._calculate_eta_from_wall_time()
            if eta is not None and eta > 0:
                parts.append(f"ETA {format_time(eta)}")

            return " | ".join(parts)

        return base

    def _calculate_eta_from_wall_time(self) -> float | None:
        """
        실제 경과 시간 기반 ETA 계산.

        Returns:
            예상 남은 시간 (초) 또는 None
        """
        if not self._file_start_time or self.current_file_progress <= 0:
            return None
        if self.current_file_progress >= 100:
            return 0

        elapsed = time.time() - self._file_start_time
        if elapsed <= 0:
            return None

        # 현재 진행률 기준 예상 전체 시간
        total_estimated = elapsed * 100 / self.current_file_progress
        remaining = total_estimated - elapsed

        return max(0, remaining)

    def _display(self) -> None:
        """화면에 출력."""
        self.file.write(f"\r{self.render()}")
        self.file.flush()
