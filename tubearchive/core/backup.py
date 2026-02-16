"""클라우드 백업 실행 유틸리티.

`rclone` 명령을 이용해 파일을 로컬/원격 경로로 복사한다.
백업은 실패해도 파이프라인 전체를 중단하지 않는다. 결과는 호출자가
판단해 로그/DB 기록한다.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackupResult:
    """백업 실행 결과."""

    source: Path
    remote: str
    success: bool
    message: str | None = None


class BackupExecutor:
    """`rclone copy` 래퍼."""

    def __init__(self, remote: str) -> None:
        self.remote = remote.strip()

    def _build_command(self, source: Path) -> list[str]:
        """rclone copy 명령을 구성한다."""
        return ["rclone", "copy", str(source), self.remote]

    def copy(self, source: Path) -> BackupResult:
        """단일 파일을 백업 대상으로 전송한다.

        파일이 없으면 실패로 간주한다.
        """
        if not source.exists():
            return BackupResult(
                source=source,
                remote=self.remote,
                success=False,
                message="source file not found",
            )

        cmd = self._build_command(source)
        logger.info("Backing up with: %s", " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            return BackupResult(
                source=source,
                remote=self.remote,
                success=False,
                message=f"rclone not found: {exc}",
            )
        except OSError as exc:
            return BackupResult(
                source=source,
                remote=self.remote,
                success=False,
                message=f"backup failed: {exc}",
            )

        if result.returncode != 0:
            return BackupResult(
                source=source,
                remote=self.remote,
                success=False,
                message=result.stderr.strip() or result.stdout.strip(),
            )

        return BackupResult(source=source, remote=self.remote, success=True)
