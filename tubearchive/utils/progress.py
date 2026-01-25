"""진행률 표시 유틸리티."""

import sys
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
        if self.total == 0:
            percent = 100
        else:
            percent = int((self.current / self.total) * 100)

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

    def start_file(self, filename: str) -> None:
        """
        새 파일 처리 시작.

        Args:
            filename: 파일명
        """
        self.current_file += 1
        self.current_file_name = filename
        self.current_file_progress = 0
        self._display()

    def update_file_progress(self, percent: int) -> None:
        """
        현재 파일 진행률 업데이트.

        Args:
            percent: 진행률 (0-100)
        """
        self.current_file_progress = percent
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
        file_bar_width = 30
        filled = int(file_bar_width * self.current_file_progress / 100)
        bar = "█" * filled + "░" * (file_bar_width - filled)

        name = self.current_file_name
        if len(name) > 20:
            name = name[:17] + "..."

        return f"{overall} {name}: [{bar}] {self.current_file_progress:3d}%"

    def _display(self) -> None:
        """화면에 출력."""
        self.file.write(f"\r{self.render()}")
        self.file.flush()
