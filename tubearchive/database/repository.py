"""데이터베이스 CRUD 작업."""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from tubearchive.models.job import JobStatus, MergeJob, TranscodingJob
from tubearchive.models.video import VideoFile, VideoMetadata


class VideoRepository:
    """영상 정보 저장소."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """초기화."""
        self.conn = conn

    def insert(self, video: VideoFile, metadata: VideoMetadata) -> int:
        """
        영상 정보 삽입.

        Args:
            video: VideoFile 객체
            metadata: VideoMetadata 객체

        Returns:
            삽입된 video_id
        """
        metadata_json = json.dumps(
            {
                "width": metadata.width,
                "height": metadata.height,
                "fps": metadata.fps,
                "codec": metadata.codec,
                "pixel_format": metadata.pixel_format,
                "is_vfr": metadata.is_vfr,
                "color_space": metadata.color_space,
                "color_transfer": metadata.color_transfer,
                "color_primaries": metadata.color_primaries,
            }
        )

        cursor = self.conn.execute(
            """
            INSERT INTO videos (
                original_path, creation_time, duration_seconds,
                device_model, is_portrait, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(video.path),
                video.creation_time.isoformat(),
                metadata.duration_seconds,
                metadata.device_model,
                1 if metadata.is_portrait else 0,
                metadata_json,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def get_by_id(self, video_id: int) -> sqlite3.Row | None:
        """ID로 영상 조회."""
        cursor = self.conn.execute(
            "SELECT * FROM videos WHERE id = ?",
            (video_id,),
        )
        result: sqlite3.Row | None = cursor.fetchone()
        return result

    def get_by_path(self, path: Path) -> sqlite3.Row | None:
        """경로로 영상 조회."""
        cursor = self.conn.execute(
            "SELECT * FROM videos WHERE original_path = ?",
            (str(path),),
        )
        result: sqlite3.Row | None = cursor.fetchone()
        return result

    def get_all(self) -> list[sqlite3.Row]:
        """모든 영상 조회."""
        cursor = self.conn.execute("SELECT * FROM videos ORDER BY creation_time")
        return cursor.fetchall()


class TranscodingJobRepository:
    """트랜스코딩 작업 저장소."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """초기화."""
        self.conn = conn

    def create(self, video_id: int) -> int:
        """
        작업 생성.

        Args:
            video_id: 영상 ID

        Returns:
            생성된 job_id
        """
        cursor = self.conn.execute(
            "INSERT INTO transcoding_jobs (video_id) VALUES (?)",
            (video_id,),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def get_by_id(self, job_id: int) -> TranscodingJob | None:
        """ID로 작업 조회."""
        cursor = self.conn.execute(
            "SELECT * FROM transcoding_jobs WHERE id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def get_by_video_id(self, video_id: int) -> list[TranscodingJob]:
        """video_id로 작업 조회."""
        cursor = self.conn.execute(
            "SELECT * FROM transcoding_jobs WHERE video_id = ? ORDER BY created_at DESC",
            (video_id,),
        )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def get_incomplete_jobs(self) -> list[TranscodingJob]:
        """미완료(processing) 작업 조회."""
        cursor = self.conn.execute(
            "SELECT * FROM transcoding_jobs WHERE status = ? ORDER BY started_at",
            (JobStatus.PROCESSING.value,),
        )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def get_resumable(self) -> list[TranscodingJob]:
        """Resume 가능한 작업 조회 (processing 상태이고 temp_file_path 존재)."""
        cursor = self.conn.execute(
            """
            SELECT * FROM transcoding_jobs
            WHERE status = ? AND temp_file_path IS NOT NULL
            ORDER BY started_at
            """,
            (JobStatus.PROCESSING.value,),
        )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def update_status(self, job_id: int, status: JobStatus) -> None:
        """상태 업데이트."""
        now = datetime.now().isoformat()

        if status == JobStatus.PROCESSING:
            self.conn.execute(
                "UPDATE transcoding_jobs SET status = ?, started_at = ? WHERE id = ?",
                (status.value, now, job_id),
            )
        elif status == JobStatus.COMPLETED:
            self.conn.execute(
                "UPDATE transcoding_jobs SET status = ?, completed_at = ? WHERE id = ?",
                (status.value, now, job_id),
            )
        else:
            self.conn.execute(
                "UPDATE transcoding_jobs SET status = ? WHERE id = ?",
                (status.value, job_id),
            )
        self.conn.commit()

    def update_progress(self, job_id: int, progress: int) -> None:
        """진행률 업데이트."""
        self.conn.execute(
            "UPDATE transcoding_jobs SET progress_percent = ? WHERE id = ?",
            (progress, job_id),
        )
        self.conn.commit()

    def mark_completed(self, job_id: int, output_path: Path) -> None:
        """완료 처리."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE transcoding_jobs
            SET status = ?, progress_percent = 100,
                temp_file_path = ?, completed_at = ?
            WHERE id = ?
            """,
            (JobStatus.COMPLETED.value, str(output_path), now, job_id),
        )
        self.conn.commit()

    def mark_failed(self, job_id: int, error_message: str) -> None:
        """실패 처리."""
        now = datetime.now().isoformat()
        self.conn.execute(
            """
            UPDATE transcoding_jobs
            SET status = ?, error_message = ?, completed_at = ?
            WHERE id = ?
            """,
            (JobStatus.FAILED.value, error_message, now, job_id),
        )
        self.conn.commit()

    def mark_merged(self, job_id: int) -> None:
        """병합 완료 후 상태 업데이트 (임시 파일 정리됨)."""
        self.conn.execute(
            "UPDATE transcoding_jobs SET status = ? WHERE id = ?",
            (JobStatus.MERGED.value, job_id),
        )
        self.conn.commit()

    def mark_merged_by_video_ids(self, video_ids: list[int]) -> int:
        """
        여러 영상의 completed 트랜스코딩 작업을 merged로 일괄 업데이트.

        Args:
            video_ids: 영상 ID 목록

        Returns:
            업데이트된 행 수
        """
        if not video_ids:
            return 0
        placeholders = ",".join("?" * len(video_ids))
        cursor = self.conn.execute(
            f"UPDATE transcoding_jobs SET status = ? "  # noqa: S608
            f"WHERE video_id IN ({placeholders}) AND status = ?",
            [JobStatus.MERGED.value, *video_ids, JobStatus.COMPLETED.value],
        )
        self.conn.commit()
        return cursor.rowcount

    def _row_to_job(self, row: sqlite3.Row) -> TranscodingJob:
        """Row를 TranscodingJob으로 변환."""
        return TranscodingJob(
            id=row["id"],
            video_id=row["video_id"],
            temp_file_path=Path(row["temp_file_path"]) if row["temp_file_path"] else None,
            status=JobStatus(row["status"]),
            progress_percent=row["progress_percent"],
            started_at=(datetime.fromisoformat(row["started_at"]) if row["started_at"] else None),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            error_message=row["error_message"],
        )


class MergeJobRepository:
    """병합 작업 저장소."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """초기화."""
        self.conn = conn

    def create(
        self,
        output_path: Path,
        video_ids: list[int],
        title: str | None = None,
        date: str | None = None,
        total_duration_seconds: float | None = None,
        total_size_bytes: int | None = None,
        clips_info_json: str | None = None,
        summary_markdown: str | None = None,
    ) -> int:
        """
        병합 작업 생성.

        Args:
            output_path: 출력 파일 경로
            video_ids: 포함된 영상 ID 목록
            title: 제목 (디렉토리명에서 추출)
            date: 날짜 (YYYY-MM-DD)
            total_duration_seconds: 총 재생 시간 (초)
            total_size_bytes: 총 파일 크기 (바이트)
            clips_info_json: 클립 정보 JSON
            summary_markdown: 마크다운 형식 요약 콘텐츠

        Returns:
            생성된 job_id
        """
        cursor = self.conn.execute(
            """
            INSERT INTO merge_jobs (
                output_path, video_ids, title, date,
                total_duration_seconds, total_size_bytes,
                clips_info_json, summary_markdown
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(output_path),
                json.dumps(video_ids),
                title,
                date,
                total_duration_seconds,
                total_size_bytes,
                clips_info_json,
                summary_markdown,
            ),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def get_by_id(self, job_id: int) -> MergeJob | None:
        """ID로 작업 조회."""
        cursor = self.conn.execute(
            "SELECT * FROM merge_jobs WHERE id = ?",
            (job_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def get_latest(self) -> MergeJob | None:
        """최신 작업 조회."""
        cursor = self.conn.execute("SELECT * FROM merge_jobs ORDER BY created_at DESC LIMIT 1")
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def update_status(self, job_id: int, status: JobStatus) -> None:
        """상태 업데이트."""
        self.conn.execute(
            "UPDATE merge_jobs SET status = ? WHERE id = ?",
            (status.value, job_id),
        )
        self.conn.commit()

    def update_youtube_id(self, job_id: int, youtube_id: str) -> None:
        """YouTube ID 업데이트 및 상태를 completed로 변경."""
        self.conn.execute(
            "UPDATE merge_jobs SET youtube_id = ?, status = ? WHERE id = ?",
            (youtube_id, JobStatus.COMPLETED.value, job_id),
        )
        self.conn.commit()

    def clear_youtube_id(self, job_id: int) -> None:
        """YouTube ID 초기화 (다시 업로드 가능하도록)."""
        self.conn.execute(
            "UPDATE merge_jobs SET youtube_id = NULL WHERE id = ?",
            (job_id,),
        )
        self.conn.commit()

    def get_all(self) -> list[MergeJob]:
        """모든 병합 작업 조회."""
        cursor = self.conn.execute("SELECT * FROM merge_jobs ORDER BY created_at DESC")
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def get_uploaded(self) -> list[MergeJob]:
        """업로드 완료된 작업만 조회."""
        cursor = self.conn.execute(
            "SELECT * FROM merge_jobs WHERE youtube_id IS NOT NULL ORDER BY created_at DESC"
        )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def delete(self, job_id: int) -> None:
        """병합 작업 삭제."""
        self.conn.execute("DELETE FROM merge_jobs WHERE id = ?", (job_id,))
        self.conn.commit()

    def delete_by_output_path(self, output_path: Path) -> int:
        """출력 경로로 병합 작업 삭제. 삭제된 행 수 반환."""
        cursor = self.conn.execute(
            "DELETE FROM merge_jobs WHERE output_path = ?",
            (str(output_path),),
        )
        self.conn.commit()
        return cursor.rowcount

    def _row_to_job(self, row: sqlite3.Row) -> MergeJob:
        """Row를 MergeJob으로 변환."""
        return MergeJob(
            id=row["id"],
            output_path=Path(row["output_path"]),
            video_ids=json.loads(row["video_ids"]),
            status=JobStatus(row["status"]),
            youtube_id=row["youtube_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            title=row["title"],
            date=row["date"],
            total_duration_seconds=row["total_duration_seconds"],
            total_size_bytes=row["total_size_bytes"],
            clips_info_json=row["clips_info_json"],
            summary_markdown=row["summary_markdown"],
        )
