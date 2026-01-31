"""트랜스코딩 Resume 상태 관리.

중단된 트랜스코딩 작업을 이어서 처리할 수 있도록
진행률·임시 파일 경로를 DB에 저장하고, 재시작 시
이전 진행 위치를 계산하여 반환한다.
"""

import sqlite3
from pathlib import Path

from tubearchive.database.repository import TranscodingJobRepository
from tubearchive.models.job import JobStatus, TranscodingJob


class ResumeManager:
    """트랜스코딩 Resume 기능 관리자.

    ``Transcoder`` 내부에서 사용되며, DB의 ``transcoding_jobs`` 테이블을
    통해 작업 진행률을 추적한다. 중단 후 재실행 시 ``PROCESSING`` 상태의
    작업을 감지하여 이전 위치부터 이어서 처리한다.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """초기화."""
        self.conn = conn
        self.job_repo = TranscodingJobRepository(conn)

    def save_progress(self, job_id: int, progress: int) -> None:
        """
        진행률 저장.

        Args:
            job_id: 작업 ID
            progress: 진행률 (0-100)
        """
        self.job_repo.update_progress(job_id, progress)

    def set_temp_file(self, job_id: int, temp_path: Path) -> None:
        """
        임시 파일 경로 설정.

        Args:
            job_id: 작업 ID
            temp_path: 임시 파일 경로
        """
        self.conn.execute(
            "UPDATE transcoding_jobs SET temp_file_path = ? WHERE id = ?",
            (str(temp_path), job_id),
        )
        self.conn.commit()

    def get_resumable_jobs(self) -> list[TranscodingJob]:
        """
        Resume 가능한 작업 조회.

        Returns:
            PROCESSING 상태이고 temp_file_path가 있는 작업 목록
        """
        return self.job_repo.get_resumable()

    def calculate_resume_position(self, job: TranscodingJob, total_duration: float) -> float:
        """
        Resume 시작 위치 계산.

        Args:
            job: 작업 객체
            total_duration: 영상 전체 길이 (초)

        Returns:
            시작 위치 (초)
        """
        return (job.progress_percent / 100.0) * total_duration

    def reset_job_for_retry(self, job_id: int) -> None:
        """
        재시도를 위해 작업 초기화.

        Args:
            job_id: 작업 ID
        """
        self.conn.execute(
            """
            UPDATE transcoding_jobs
            SET status = ?, progress_percent = 0,
                error_message = NULL, started_at = NULL, completed_at = NULL
            WHERE id = ?
            """,
            (JobStatus.PENDING.value, job_id),
        )
        self.conn.commit()

    def is_video_processed(self, video_id: int) -> bool:
        """
        영상 처리 완료 여부 확인.

        Args:
            video_id: 영상 ID

        Returns:
            completed 상태 작업이 있으면 True (merged는 제외)
        """
        cursor = self.conn.execute(
            "SELECT COUNT(*) FROM transcoding_jobs WHERE video_id = ? AND status = ?",
            (video_id, JobStatus.COMPLETED.value),
        )
        count: int = cursor.fetchone()[0]
        return count > 0

    def get_or_create_job(self, video_id: int) -> int:
        """
        기존 작업 반환 또는 새 작업 생성.

        PENDING/PROCESSING 상태 작업이 있으면 반환,
        없으면 새로 생성.

        Args:
            video_id: 영상 ID

        Returns:
            작업 ID
        """
        cursor = self.conn.execute(
            """
            SELECT id FROM transcoding_jobs
            WHERE video_id = ? AND status IN (?, ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (video_id, JobStatus.PENDING.value, JobStatus.PROCESSING.value),
        )
        row = cursor.fetchone()

        if row is not None:
            job_id: int = row[0]
            return job_id

        return self.job_repo.create(video_id)
