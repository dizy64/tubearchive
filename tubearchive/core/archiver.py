"""원본 파일 아카이브 관리.

트랜스코딩 완료 후 원본 파일을 이동/삭제/유지하는 정책을 적용하고,
이력을 DB에 기록한다.

DB 접근은 :class:`~tubearchive.database.repository.ArchiveHistoryRepository`
를 통해서만 수행한다 (Repository 패턴 준수).
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from tubearchive.database.repository import ArchiveHistoryRepository

logger = logging.getLogger(__name__)


class ArchivePolicy(Enum):
    """아카이브 정책 열거형."""

    KEEP = "keep"  # 원본 파일 유지
    MOVE = "move"  # 지정 경로로 이동
    DELETE = "delete"  # 삭제


@dataclass
class ArchiveStats:
    """아카이브 결과 통계."""

    moved: int = 0
    deleted: int = 0
    kept: int = 0
    failed: int = 0


class Archiver:
    """원본 파일 아카이브 처리기.

    정책에 따라 원본 파일을 이동/삭제하고, Repository를 통해 이력을 DB에 기록한다.
    """

    def __init__(
        self,
        repo: ArchiveHistoryRepository,
        policy: ArchivePolicy,
        destination: Path | None = None,
    ) -> None:
        """초기화.

        Args:
            repo: 아카이브 이력 Repository
            policy: 아카이브 정책
            destination: MOVE 정책 시 이동할 경로

        Raises:
            ValueError: MOVE 정책인데 destination이 없을 때
        """
        self.repo = repo
        self.policy = policy
        self.destination = destination

        if self.policy == ArchivePolicy.MOVE and not self.destination:
            msg = "MOVE 정책을 사용하려면 destination 경로를 지정해야 합니다"
            raise ValueError(msg)

    def archive_files(self, video_paths: list[tuple[int, Path]]) -> ArchiveStats:
        """원본 파일들을 정책에 따라 아카이브한다.

        Args:
            video_paths: (video_id, original_path) 튜플 리스트

        Returns:
            아카이브 결과 통계
        """
        stats = ArchiveStats()

        if self.policy == ArchivePolicy.KEEP:
            logger.info("아카이브 정책이 KEEP입니다. 원본 파일 유지.")
            stats.kept = len(video_paths)
            return stats

        for video_id, original_path in video_paths:
            try:
                if self.policy == ArchivePolicy.MOVE:
                    destination_path = self._move_file(original_path)
                    self._record_history(video_id, "move", original_path, destination_path)
                    stats.moved += 1
                    logger.info("이동 완료: %s → %s", original_path, destination_path)
                elif self.policy == ArchivePolicy.DELETE:
                    if self._delete_file(original_path):
                        self._record_history(video_id, "delete", original_path, None)
                        stats.deleted += 1
                        logger.info("삭제 완료: %s", original_path)
            except Exception:
                logger.exception("아카이브 실패: %s", original_path)
                stats.failed += 1

        return stats

    def _move_file(self, original_path: Path) -> Path:
        """파일을 destination으로 이동한다.

        Args:
            original_path: 원본 파일 경로

        Returns:
            이동된 파일의 최종 경로

        Raises:
            ValueError: destination 미설정
            OSError: 파일 이동 실패
        """
        if not self.destination:
            msg = "destination이 설정되지 않았습니다"
            raise ValueError(msg)

        # destination 디렉토리 생성
        self.destination.mkdir(parents=True, exist_ok=True)

        # 목적지 경로 생성 (동일 파일명 충돌 시 번호 추가)
        destination_path = self.destination / original_path.name
        if destination_path.exists():
            stem = original_path.stem
            suffix = original_path.suffix
            counter = 1
            while destination_path.exists():
                destination_path = self.destination / f"{stem}_{counter}{suffix}"
                counter += 1

        shutil.move(str(original_path), str(destination_path))
        return destination_path

    def _delete_file(self, original_path: Path) -> bool:
        """파일을 삭제한다.

        Args:
            original_path: 원본 파일 경로

        Returns:
            파일이 실제로 삭제되었으면 True, 원래 없었으면 False.
        """
        if not original_path.exists():
            logger.warning("파일이 존재하지 않습니다: %s", original_path)
            return False

        original_path.unlink()
        return True

    def _record_history(
        self,
        video_id: int,
        operation: Literal["move", "delete"],
        original_path: Path,
        destination_path: Path | None,
    ) -> None:
        """아카이브 이력을 Repository를 통해 DB에 기록한다."""
        self.repo.insert_history(video_id, operation, original_path, destination_path)
