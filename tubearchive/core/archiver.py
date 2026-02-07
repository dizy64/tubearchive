"""원본 파일 아카이브 관리.

트랜스코딩 완료 후 원본 파일을 이동/삭제/유지하는 정책을 적용하고,
이력을 DB에 기록한다.
"""

from __future__ import annotations

import logging
import shutil
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)


class ArchivePolicy(Enum):
    """아카이브 정책 열거형."""

    KEEP = "keep"  # 원본 파일 유지
    MOVE = "move"  # 지정 경로로 이동
    DELETE = "delete"  # 삭제


class Archiver:
    """원본 파일 아카이브 처리기.

    정책에 따라 원본 파일을 이동/삭제하고, 이력을 DB에 기록한다.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        policy: ArchivePolicy,
        destination: Path | None = None,
        force: bool = False,
    ) -> None:
        """초기화.

        Args:
            conn: DB 연결
            policy: 아카이브 정책
            destination: MOVE 정책 시 이동할 경로
            force: 삭제 확인 프롬프트 우회 여부
        """
        self.conn = conn
        self.policy = policy
        self.destination = destination
        self.force = force

        # MOVE 정책 시 destination 필수 검증
        if self.policy == ArchivePolicy.MOVE and not self.destination:
            msg = "MOVE 정책을 사용하려면 destination 경로를 지정해야 합니다"
            raise ValueError(msg)

    def archive_files(self, video_paths: list[tuple[int, Path]]) -> dict[str, int]:
        """원본 파일들을 정책에 따라 아카이브한다.

        Args:
            video_paths: (video_id, original_path) 튜플 리스트

        Returns:
            결과 통계: {"moved": 0, "deleted": 0, "kept": 0, "failed": 0}
        """
        stats = {"moved": 0, "deleted": 0, "kept": 0, "failed": 0}

        if self.policy == ArchivePolicy.KEEP:
            logger.info("아카이브 정책이 KEEP입니다. 원본 파일 유지.")
            stats["kept"] = len(video_paths)
            return stats

        # DELETE 정책 시 확인 프롬프트
        if (
            self.policy == ArchivePolicy.DELETE
            and not self.force
            and not self._prompt_confirmation(len(video_paths))
        ):
            logger.info("사용자가 삭제를 취소했습니다.")
            stats["kept"] = len(video_paths)
            return stats

        for video_id, original_path in video_paths:
            try:
                if self.policy == ArchivePolicy.MOVE:
                    destination_path = self._move_file(original_path)
                    self._record_history(video_id, "move", original_path, destination_path)
                    stats["moved"] += 1
                    logger.info("이동 완료: %s → %s", original_path, destination_path)
                elif self.policy == ArchivePolicy.DELETE:
                    self._delete_file(original_path)
                    self._record_history(video_id, "delete", original_path, None)
                    stats["deleted"] += 1
                    logger.info("삭제 완료: %s", original_path)
            except Exception:
                logger.exception("아카이브 실패: %s", original_path)
                stats["failed"] += 1

        return stats

    def _move_file(self, original_path: Path) -> Path:
        """파일을 destination으로 이동한다.

        Args:
            original_path: 원본 파일 경로

        Returns:
            이동된 파일의 최종 경로

        Raises:
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

        # 파일 이동
        shutil.move(str(original_path), str(destination_path))
        return destination_path

    def _delete_file(self, original_path: Path) -> None:
        """파일을 삭제한다.

        Args:
            original_path: 원본 파일 경로

        Raises:
            OSError: 파일 삭제 실패
        """
        if not original_path.exists():
            logger.warning("파일이 존재하지 않습니다: %s", original_path)
            return

        original_path.unlink()

    def _prompt_confirmation(self, file_count: int) -> bool:
        """삭제 확인 프롬프트를 표시한다.

        Args:
            file_count: 삭제할 파일 개수

        Returns:
            True: 삭제 승인, False: 취소
        """
        print(f"\n⚠️  {file_count}개의 원본 파일을 영구 삭제하려고 합니다.")
        print("이 작업은 되돌릴 수 없습니다.")
        response = input("계속하시겠습니까? (y/N): ").strip().lower()
        return response in {"y", "yes"}

    def _record_history(
        self,
        video_id: int,
        operation: str,
        original_path: Path,
        destination_path: Path | None,
    ) -> None:
        """아카이브 이력을 DB에 기록한다.

        Args:
            video_id: 영상 ID
            operation: 작업 타입 (move/delete)
            original_path: 원본 경로
            destination_path: 이동 경로 (delete 시 None)
        """
        self.conn.execute(
            """
            INSERT INTO archive_history (video_id, operation, original_path, destination_path)
            VALUES (?, ?, ?, ?)
            """,
            (
                video_id,
                operation,
                str(original_path),
                str(destination_path) if destination_path else None,
            ),
        )
        self.conn.commit()
