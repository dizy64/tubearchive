"""DB 영속화 (merge job 저장, 상태 갱신, 프로젝트 연결)."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from tubearchive.domain.media.grouper import (
    FileSequenceGroup,
)
from tubearchive.domain.models.clip import ClipInfo
from tubearchive.infra.db.repository import (
    TranscodingJobRepository,
)

logger = logging.getLogger(__name__)


def _mark_transcoding_jobs_merged(video_ids: list[int]) -> None:
    """트랜스코딩 작업 상태를 merged로 업데이트 (임시 파일 정리 후)."""
    if not video_ids:
        return
    try:
        from tubearchive.app.cli.main import database_session  # lazy: avoids circular import

        with database_session() as conn:
            job_repo = TranscodingJobRepository(conn)
            count = job_repo.mark_merged_by_video_ids(video_ids)
        logger.debug(f"Marked {count} transcoding jobs as merged")
    except Exception:
        logger.warning("Failed to mark transcoding jobs as merged", exc_info=True)


def save_merge_job_to_db(
    output_path: Path,
    video_clips: list[ClipInfo],
    targets: list[Path],
    video_ids: list[int],
    groups: list[FileSequenceGroup] | None = None,
) -> tuple[str | None, int | None]:
    """병합 작업 정보를 DB에 저장 (타임라인 및 Summary 포함).

    Args:
        output_path: 출력 파일 경로
        video_clips: 클립 메타데이터 리스트
        targets: 입력 타겟 목록 (제목 추출용)
        video_ids: 병합된 영상들의 DB ID 목록
        groups: 시퀀스 그룹 목록 (Summary 생성용)

    Returns:
        (콘솔 출력용 Summary 마크다운, merge_job_id) 튜플. 실패 시 (None, None).
    """
    from tubearchive.shared.summary_generator import (
        generate_clip_summary,
        generate_youtube_description,
    )

    try:
        from tubearchive.app.cli.main import (  # type: ignore[attr-defined]  # lazy: callers patch main.*
            MergeJobRepository as _MergeJobRepository,
        )
        from tubearchive.app.cli.main import (
            database_session,
        )

        with database_session() as conn:
            repo = _MergeJobRepository(conn)

            # 타임라인 정보 생성 (각 클립의 메타데이터 포함)
            timeline: list[dict[str, str | float | None]] = []
            current_time = 0.0
            for clip in video_clips:
                timeline.append(
                    {
                        "name": clip.name,
                        "duration": clip.duration,
                        "start": current_time,
                        "end": current_time + clip.duration,
                        "device": clip.device,
                        "shot_time": clip.shot_time,
                    }
                )
                current_time += clip.duration

            clips_json = json.dumps(timeline, ensure_ascii=False)

            # 제목: 디렉토리명
            title = None
            if targets:
                first_target = targets[0]
                title = first_target.name if first_target.is_dir() else first_target.parent.name
                if not title or title == ".":
                    title = output_path.stem

            today = date.today().isoformat()

            total_duration = sum(c.duration for c in video_clips)
            total_size = output_path.stat().st_size if output_path.exists() else 0

            # 콘솔 출력용 요약 (마크다운 형식)
            console_summary = generate_clip_summary(video_clips, groups=groups)
            # YouTube 설명용 (타임스탬프 + 촬영기기)
            youtube_description = generate_youtube_description(video_clips, groups=groups)

            merge_job_id = repo.create(
                output_path=output_path,
                video_ids=video_ids,
                title=title,
                date=today,
                total_duration_seconds=total_duration,
                total_size_bytes=total_size,
                clips_info_json=clips_json,
                summary_markdown=youtube_description,
            )

        logger.debug("Merge job saved to database with summary")
        return console_summary, merge_job_id

    except Exception as e:
        logger.warning(f"Failed to save merge job to DB: {e}")
        return None, None


def _link_merge_job_to_project(project_name: str, merge_job_id: int) -> None:
    """병합 결과를 프로젝트에 연결한다.

    프로젝트가 없으면 자동 생성하고, merge_job을 연결한다.
    날짜 범위도 자동으로 갱신된다.

    Args:
        project_name: 프로젝트 이름
        merge_job_id: merge_job ID
    """
    from tubearchive.infra.db.repository import ProjectRepository

    try:
        from tubearchive.app.cli.main import database_session  # lazy: avoids circular import

        with database_session() as conn:
            repo = ProjectRepository(conn)
            project = repo.get_or_create(project_name)
            if project.id is None:
                logger.warning("Project created but has no ID")
                return
            repo.add_merge_job(project.id, merge_job_id)
            logger.info(f"Merge job {merge_job_id} linked to project '{project_name}'")
            print(f"\n📁 프로젝트 '{project_name}'에 병합 결과 연결됨")
    except Exception as e:
        logger.warning(f"Failed to link merge job to project: {e}")
