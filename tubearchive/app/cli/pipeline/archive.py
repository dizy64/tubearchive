"""원본 파일 백업/아카이브 처리."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from tubearchive.app.cli.validators import ValidatedArgs
from tubearchive.domain.media.backup import BackupExecutor, BackupResult

logger = logging.getLogger(__name__)


def _run_backup(
    *,
    final_path: Path,
    split_files: list[Path],
    timelapse_path: Path | None,
    original_paths_for_backup: list[Path],
    validated_args: ValidatedArgs,
    merge_job_id: int | None,
) -> None:
    """병합/분할/타임랩스/원본 영상을 백업한다.

    실패해도 파이프라인을 중단하지 않고 로그만 남긴다.
    """
    if not validated_args.backup_remote:
        return

    remote = validated_args.backup_remote.strip()
    if not remote:
        logger.warning("backup remote is empty. skip backup.")
        return

    backup_targets: list[tuple[Path, Literal["output", "split", "timelapse", "original"]]] = []
    if final_path.exists():
        backup_targets.append((final_path, "output"))
    else:
        logger.warning("Final output not found for backup: %s", final_path)

    for split_file in split_files:
        if split_file.exists():
            backup_targets.append((split_file, "split"))
        else:
            logger.warning("Split file not found for backup: %s", split_file)

    if timelapse_path is not None:
        if timelapse_path.exists():
            backup_targets.append((timelapse_path, "timelapse"))
        else:
            logger.warning("Timelapse file not found for backup: %s", timelapse_path)

    for original_path in original_paths_for_backup:
        if original_path.exists():
            backup_targets.append((original_path, "original"))
        else:
            logger.warning("Original file not found for backup: %s", original_path)

    if not backup_targets:
        logger.warning("No backup targets found.")
        return

    logger.info("Starting backup (%s) for %d target(s)", remote, len(backup_targets))
    executor = BackupExecutor(remote)
    results: list[
        tuple[Path, Literal["output", "split", "timelapse", "original"], BackupResult]
    ] = []

    for source_path, source_type in backup_targets:
        backup_result = executor.copy(source_path)
        results.append((source_path, source_type, backup_result))
        if backup_result.success:
            logger.info("Backup succeeded: %s -> %s (%s)", source_path, remote, source_type)
        else:
            logger.warning(
                "Backup failed: %s -> %s (%s): %s",
                source_path,
                remote,
                source_type,
                backup_result.message,
            )

    if merge_job_id is None:
        logger.debug(
            "merge_job_id is None; skip backup history insertion (target count=%d)",
            len(results),
        )
        return

    from tubearchive.app.cli.main import database_session  # lazy: avoids circular import

    try:
        with database_session() as conn:
            from tubearchive.infra.db.repository import BackupHistoryRepository

            backup_repo = BackupHistoryRepository(conn)
            for source_path, source_type, result in results:
                backup_repo.insert_history(
                    merge_job_id=merge_job_id,
                    source_path=source_path,
                    remote=remote,
                    source_type=source_type,
                    success=result.success,
                    error_message=result.message,
                )
    except Exception:
        # 백업 이력 저장은 이미 완료된 백업 결과를 되돌릴 수 없으므로
        # 파이프라인 전체를 실패시키지 않고 원인만 기록한다.
        logger.warning("Failed to save backup history", exc_info=True)


def _archive_originals(
    video_paths: list[tuple[int, Path]],
    validated_args: ValidatedArgs,
) -> None:
    """원본 파일들을 정책에 따라 아카이빙한다.

    CLI 옵션(``--archive-originals``) 또는 설정 파일(``[archive]``)의
    정책을 읽어 원본 파일을 이동/삭제/유지한다.

    우선순위: CLI ``--archive-originals`` > config ``[archive].policy``

    Args:
        video_paths: (video_id, original_path) 튜플 리스트
        validated_args: 검증된 CLI 인자
    """
    from tubearchive.config import get_default_archive_destination, get_default_archive_policy
    from tubearchive.domain.media.archiver import ArchivePolicy, Archiver

    if not video_paths:
        logger.warning("아카이빙할 원본 파일이 없습니다.")
        return

    # 정책 결정: CLI 옵션 > config > 기본값(KEEP)
    if validated_args.archive_originals:
        policy = ArchivePolicy.MOVE
        destination: Path | None = validated_args.archive_originals
    else:
        policy_str = get_default_archive_policy()
        policy = ArchivePolicy(policy_str)
        destination = get_default_archive_destination()

    # KEEP 정책이면 아무것도 하지 않음
    if policy == ArchivePolicy.KEEP:
        logger.debug("아카이브 정책이 KEEP입니다. 원본 파일 유지.")
        return

    # MOVE 정책인데 destination이 없으면 경고
    if policy == ArchivePolicy.MOVE and not destination:
        logger.warning("MOVE 정책이 설정되었으나 destination이 없습니다. 원본 파일 유지.")
        return

    # DELETE 정책 시 확인 프롬프트 (core 모듈이 아닌 CLI 계층에서 처리)
    if (
        policy == ArchivePolicy.DELETE
        and not validated_args.archive_force
        and not _prompt_archive_delete_confirmation(len(video_paths))
    ):
        logger.info("사용자가 삭제를 취소했습니다.")
        return

    logger.info("원본 파일 아카이빙 시작 (정책: %s)...", policy.value)

    from tubearchive.app.cli.main import database_session  # lazy: avoids circular import

    with database_session() as conn:
        from tubearchive.infra.db.repository import ArchiveHistoryRepository

        archive_repo = ArchiveHistoryRepository(conn)
        archiver = Archiver(
            repo=archive_repo,
            policy=policy,
            destination=destination,
        )
        stats = archiver.archive_files(video_paths)

    if policy == ArchivePolicy.MOVE:
        logger.info("아카이빙 완료: 이동 %d, 실패 %d", stats.moved, stats.failed)
    elif policy == ArchivePolicy.DELETE:
        logger.info("아카이빙 완료: 삭제 %d, 실패 %d", stats.deleted, stats.failed)


def _prompt_archive_delete_confirmation(file_count: int) -> bool:
    """원본 파일 삭제 확인 프롬프트를 표시한다.

    Args:
        file_count: 삭제 대상 파일 개수

    Returns:
        True: 삭제 승인, False: 취소
    """
    print(f"\n⚠️  {file_count}개의 원본 파일을 영구 삭제하려고 합니다.")
    print("이 작업은 되돌릴 수 없습니다.")
    response = input("계속하시겠습니까? (y/N): ").strip().lower()
    return response in {"y", "yes"}
