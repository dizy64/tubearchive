"""상태·리셋·픽스 CLI 커맨드."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from tubearchive.app.queries.catalog import STATUS_ICONS, format_duration
from tubearchive.infra.db.repository import (
    MergeJobRepository,
    TranscodingJobRepository,
    VideoRepository,
)
from tubearchive.shared import truncate_path
from tubearchive.shared.progress import format_size

logger = logging.getLogger(__name__)


def _delete_build_records(conn: sqlite3.Connection, video_ids: list[int]) -> None:
    """빌드 관련 레코드 삭제 (transcoding_jobs → videos 순서)."""
    if not video_ids:
        return
    TranscodingJobRepository(conn).delete_by_video_ids(video_ids)
    VideoRepository(conn).delete_by_ids(video_ids)


def cmd_reset_build(path_arg: str) -> None:
    """``--reset-build`` 옵션 처리."""
    from tubearchive.app.cli.main import _interactive_select, database_session

    with database_session() as conn:
        repo = MergeJobRepository(conn)

        if path_arg:
            target_path = Path(path_arg).resolve()

            merge_job = repo.get_by_output_path(target_path)
            if merge_job:
                _delete_build_records(conn, merge_job.video_ids)

            deleted = repo.delete_by_output_path(target_path)
            if deleted > 0:
                print(f"✅ 빌드 기록 삭제됨: {target_path}")
                print("   이제 다시 빌드할 수 있습니다.")
            else:
                print(f"⚠️ 해당 경로의 기록이 없습니다: {target_path}")
        else:
            jobs = repo.get_all()
            if not jobs:
                print("📋 빌드 기록이 없습니다.")
                return

            print("\n📋 빌드 기록 목록")
            print("=" * 80)
            print(f"{'번호':<4} {'제목':<30} {'날짜':<12} {'YouTube':<10} 경로")
            print("-" * 80)
            for i, job in enumerate(jobs, 1):
                title = (job.title or "-")[:28]
                job_date = job.date or "-"
                yt_status = "✅ 업로드됨" if job.youtube_id else "-"
                path = truncate_path(str(job.output_path), max_len=40)
                print(f"{i:<4} {title:<30} {job_date:<12} {yt_status:<10} {path}")
            print("=" * 80)

            idx = _interactive_select(jobs, "\n삭제할 번호 입력 (0: 취소): ")
            if idx is None:
                return

            job = jobs[idx]
            _delete_build_records(conn, job.video_ids)
            if job.id is not None:
                repo.delete(job.id)
            print(f"\n✅ 빌드 기록 삭제됨: {job.title or job.output_path}")
            print("   이제 다시 빌드할 수 있습니다.")


def cmd_reset_upload(path_arg: str) -> None:
    """``--reset-upload`` 옵션 처리."""
    from tubearchive.app.cli.main import _interactive_select, database_session

    with database_session() as conn:
        repo = MergeJobRepository(conn)

        if path_arg:
            target_path = Path(path_arg).resolve()
            merge_job = repo.get_by_output_path(target_path)
            if merge_job and merge_job.youtube_id:
                if merge_job.id is not None:
                    repo.clear_youtube_id(merge_job.id)
                print(f"✅ 업로드 기록 초기화됨: {target_path}")
                print(f"   이전 YouTube ID: {merge_job.youtube_id}")
                print("   이제 다시 업로드할 수 있습니다.")
            elif merge_job:
                print(f"⚠️ 이미 업로드 기록이 없습니다: {target_path}")
            else:
                print(f"⚠️ 해당 경로의 기록이 없습니다: {target_path}")
        else:
            jobs = repo.get_uploaded()
            if not jobs:
                print("📋 업로드된 영상이 없습니다.")
                return

            print("\n📋 업로드된 영상 목록")
            print("=" * 90)
            print(f"{'번호':<4} {'제목':<30} {'날짜':<12} {'YouTube ID':<15} 경로")
            print("-" * 90)
            for i, job in enumerate(jobs, 1):
                title = (job.title or "-")[:28]
                job_date = job.date or "-"
                yt_id = job.youtube_id or "-"
                path = truncate_path(str(job.output_path), max_len=30)
                print(f"{i:<4} {title:<30} {job_date:<12} {yt_id:<15} {path}")
            print("=" * 90)

            idx = _interactive_select(jobs, "\n초기화할 번호 입력 (0: 취소): ")
            if idx is None:
                return

            job = jobs[idx]
            if job.id is not None:
                repo.clear_youtube_id(job.id)
            print(f"\n✅ 업로드 기록 초기화됨: {job.title or job.output_path}")
            print(f"   이전 YouTube ID: {job.youtube_id}")
            print("   이제 다시 업로드할 수 있습니다.")


def cmd_status() -> None:
    """``--status`` 옵션 처리: 전체 작업 현황 출력."""
    from tubearchive.app.cli.main import database_session

    with database_session() as conn:
        video_repo = VideoRepository(conn)
        transcoding_repo = TranscodingJobRepository(conn)
        merge_repo = MergeJobRepository(conn)

        print("\n📊 TubeArchive 작업 현황\n")

        processing_jobs = transcoding_repo.get_active_with_paths(limit=10)

        if processing_jobs:
            print("🔄 진행 중인 트랜스코딩:")
            print("-" * 70)
            for tc_row in processing_jobs:
                path = Path(tc_row["original_path"]).name
                status = "⏳ 대기" if tc_row["status"] == "pending" else "🔄 진행"
                progress = tc_row["progress_percent"] or 0
                print(f"  {status} [{progress:3d}%] {path}")
            print()

        recent_merge_jobs = merge_repo.get_recent(limit=10)

        if recent_merge_jobs:
            print("📁 최근 병합 작업:")
            print("-" * 90)
            print(f"{'ID':<4} {'상태':<10} {'제목':<25} {'날짜':<12} {'길이':<10} {'YouTube':<12}")
            print("-" * 90)
            for job in recent_merge_jobs:
                title = (job.title or "-")[:23]
                job_date = job.date or "-"
                status_icon = STATUS_ICONS.get(job.status.value, job.status.value)
                duration_str = format_duration(job.total_duration_seconds or 0)
                yt_status = f"✅ {job.youtube_id[:8]}..." if job.youtube_id else "- 미업로드"
                row_str = (
                    f"{job.id:<4} {status_icon:<10} {title:<25} {job_date:<12} {duration_str:<10}"
                )
                print(f"{row_str} {yt_status}")

            print("-" * 90)
        else:
            print("📁 병합 작업 없음\n")

        video_count = video_repo.count_all()
        total_jobs = merge_repo.count_all()
        uploaded_count = merge_repo.count_uploaded()

        print(
            f"\n📈 통계: 영상 {video_count}개 등록"
            f" | 병합 {total_jobs}건 | 업로드 {uploaded_count}건"
        )


def cmd_status_detail(job_id: int) -> None:
    """``--status-detail`` 옵션 처리: 특정 작업의 상세 정보를 출력한다."""
    from tubearchive.app.cli.main import database_session

    with database_session() as conn:
        job = MergeJobRepository(conn).get_by_id(job_id)

        if not job:
            print(f"❌ 작업 ID {job_id}를 찾을 수 없습니다.")
            return

        print(f"\n📋 작업 상세 (ID: {job_id})\n")
        print("=" * 60)

        print(f"📌 제목: {job.title or '-'}")
        print(f"📅 날짜: {job.date or '-'}")
        print(f"📁 출력: {job.output_path}")
        print(f"📊 상태: {STATUS_ICONS.get(job.status.value, job.status.value)}")
        print(f"⏱️  길이: {format_duration(job.total_duration_seconds or 0)}")
        print(f"💾 크기: {format_size(job.total_size_bytes or 0)}")

        if job.youtube_id:
            print(f"🎬 YouTube: https://youtu.be/{job.youtube_id}")
        else:
            print("🎬 YouTube: 미업로드")

        if job.clips_info_json:
            try:
                clips = json.loads(job.clips_info_json)
                print(f"\n📹 클립 ({len(clips)}개):")
                print("-" * 60)
                for i, clip in enumerate(clips, 1):
                    name = clip.get("name", "-")
                    clip_duration = clip.get("duration", 0)
                    device = clip.get("device", "-")
                    shot_time = clip.get("shot_time", "-")
                    print(f"  {i}. {name}")
                    print(f"     기기: {device} | 촬영: {shot_time} | 길이: {clip_duration:.1f}s")
            except json.JSONDecodeError:
                pass

        print("=" * 60)


def cmd_fix_device_models() -> None:
    """``--fix-device-models`` 처리: device_model을 재스캔하여 채우거나 수정한다."""
    from tubearchive.app.cli.main import database_session
    from tubearchive.domain.media.detector import _extract_device_model, _run_ffprobe

    with database_session() as conn:
        repo = VideoRepository(conn)
        rows = repo.get_missing_device_model(include_heuristic=True)

        total = len(rows)
        if total == 0:
            print("재감지가 필요한 영상이 없습니다.")
            return

        print(f"device_model 재스캔 대상 {total}개 처리 시작...")

        updated = 0
        skipped_missing = 0
        skipped_no_model = 0

        for row in rows:
            video_id: int = row["id"]
            path = Path(row["original_path"])

            if not path.exists():
                skipped_missing += 1
                logger.debug("파일 없음, 건너뜀: %s", path)
                continue

            try:
                probe_data = _run_ffprobe(path)
                device_model = _extract_device_model(probe_data, path)
            except Exception as exc:
                logger.debug("ffprobe 실패 (%s): %s", path.name, exc)
                skipped_no_model += 1
                continue

            if not device_model:
                skipped_no_model += 1
                logger.debug("모델 감지 불가: %s", path.name)
                continue

            repo.update_device_model(video_id, device_model)
            updated += 1
            logger.info("갱신: %s → %s", path.name, device_model)

        print(
            f"완료: {updated}개 갱신"
            f", {skipped_missing}개 파일 없음"
            f", {skipped_no_model}개 모델 감지 불가"
        )


__all__ = [
    "_delete_build_records",
    "cmd_fix_device_models",
    "cmd_reset_build",
    "cmd_reset_upload",
    "cmd_status",
    "cmd_status_detail",
]
