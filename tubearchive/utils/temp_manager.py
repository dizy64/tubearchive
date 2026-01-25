"""임시 파일 관리."""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from types import TracebackType

logger = logging.getLogger(__name__)


class TempManager:
    """임시 파일 관리자."""

    def __init__(
        self,
        base_dir: Path | None = None,
        prefix: str = "tubearchive_",
        keep: bool = False,
    ) -> None:
        """
        초기화.

        Args:
            base_dir: 임시 디렉토리 기본 위치 (기본: 현재 디렉토리)
            prefix: 디렉토리 접두사
            keep: 종료 시 임시 파일 보존 여부
        """
        self.base_dir = base_dir or Path.cwd()
        self.prefix = prefix
        self.keep = keep
        self._temp_dir: Path | None = None
        self._cleanup_files: list[Path] = []

    @property
    def temp_dir(self) -> Path:
        """임시 디렉토리 경로."""
        if self._temp_dir is None:
            raise RuntimeError("TempManager not initialized. Use as context manager.")
        return self._temp_dir

    def __enter__(self) -> TempManager:
        """Context manager 진입."""
        self._temp_dir = self.base_dir / f"{self.prefix}{uuid.uuid4().hex[:8]}"
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Created temp directory: {self._temp_dir}")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Context manager 종료."""
        if not self.keep:
            self.cleanup()

    def create_temp_file(self, filename: str, prefix: str = "") -> Path:
        """
        임시 파일 경로 생성.

        Args:
            filename: 파일명
            prefix: 파일명 접두사

        Returns:
            임시 파일 경로
        """
        name = f"{prefix}{filename}" if prefix else filename
        return self.temp_dir / name

    def get_temp_path(self, *parts: str) -> Path:
        """
        임시 경로 생성.

        Args:
            *parts: 경로 구성 요소

        Returns:
            전체 경로
        """
        return self.temp_dir.joinpath(*parts)

    def ensure_subdir(self, name: str) -> Path:
        """
        서브디렉토리 생성.

        Args:
            name: 서브디렉토리 이름

        Returns:
            서브디렉토리 경로
        """
        subdir = self.temp_dir / name
        subdir.mkdir(parents=True, exist_ok=True)
        return subdir

    def register_for_cleanup(self, path: Path) -> None:
        """
        정리할 파일 등록.

        Args:
            path: 파일 경로
        """
        self._cleanup_files.append(path)

    def cleanup(self) -> None:
        """임시 파일 및 디렉토리 정리."""
        # 등록된 외부 파일 정리
        for path in self._cleanup_files:
            try:
                if path.exists():
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    logger.debug(f"Cleaned up: {path}")
            except OSError as e:
                logger.warning(f"Failed to clean up {path}: {e}")

        # 임시 디렉토리 정리
        if self._temp_dir and self._temp_dir.exists():
            try:
                shutil.rmtree(self._temp_dir)
                logger.debug(f"Removed temp directory: {self._temp_dir}")
            except OSError as e:
                logger.warning(f"Failed to remove temp directory: {e}")

        self._cleanup_files.clear()
