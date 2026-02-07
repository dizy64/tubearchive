"""데이터베이스 CRUD 리포지토리.

SQLite DB 위의 영상·트랜스코딩·병합·분할 작업 정보를 조회·생성·수정·삭제한다.

리포지토리 클래스:
    - :class:`VideoRepository`: 원본 영상 메타데이터
    - :class:`TranscodingJobRepository`: 트랜스코딩 작업 상태·진행률
    - :class:`MergeJobRepository`: 병합 작업 이력·YouTube 업로드 정보
    - :class:`SplitJobRepository`: 영상 분할 작업 이력
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from tubearchive.models.job import JobStatus, MergeJob, SplitJob, TranscodingJob
from tubearchive.models.video import VideoFile, VideoMetadata

logger = logging.getLogger(__name__)


class VideoRepository:
    """``videos`` 테이블 CRUD 저장소.

    원본 영상 파일의 경로·생성 시간·메타데이터를 저장하고 조회한다.
    """

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

    def count_all(self) -> int:
        """등록된 전체 영상 수를 반환한다."""
        cursor = self.conn.execute("SELECT COUNT(*) as cnt FROM videos")
        return int(cursor.fetchone()["cnt"])

    def get_stats(self, period: str | None = None) -> dict[str, object]:
        """영상 통계를 집계한다.

        Args:
            period: 기간 필터 (LIKE 패턴, 예: ``'2026-01'``). None이면 전체.

        Returns:
            ``total``, ``total_duration``, ``devices`` 키를 가진 딕셔너리.
        """
        where = ""
        params: list[str] = []
        if period:
            where = "WHERE creation_time LIKE ?"
            params.append(f"{period}%")

        row = self.conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(duration_seconds), 0) as dur"
            f" FROM videos {where}",
            params,
        ).fetchone()

        device_rows = self.conn.execute(
            f"""SELECT COALESCE(device_model, '미상') as device, COUNT(*) as cnt
                FROM videos {where}
                GROUP BY COALESCE(device_model, '미상')
                ORDER BY cnt DESC""",
            params,
        ).fetchall()

        return {
            "total": int(row["cnt"]),
            "total_duration": float(row["dur"]),
            "devices": [(r["device"], int(r["cnt"])) for r in device_rows],
        }

    def delete_by_ids(self, video_ids: list[int]) -> int:
        """여러 영상을 ID 목록으로 일괄 삭제한다.

        Args:
            video_ids: 삭제할 영상 ID 목록

        Returns:
            삭제된 행 수
        """
        if not video_ids:
            return 0
        placeholders = ",".join("?" * len(video_ids))
        cursor = self.conn.execute(
            f"DELETE FROM videos WHERE id IN ({placeholders})",
            video_ids,
        )
        self.conn.commit()
        return cursor.rowcount


class TranscodingJobRepository:
    """``transcoding_jobs`` 테이블 CRUD 저장소.

    작업 생성·상태 변경·진행률 갱신·Resume 가능 작업 조회를 제공한다.
    """

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
        """트랜스코딩 작업 진행률을 업데이트한다.

        Args:
            job_id: 트랜스코딩 작업 ID
            progress: 진행률 퍼센트 (0-100, DB ``progress_percent`` 컬럼)
        """
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

    def delete_by_video_ids(self, video_ids: list[int]) -> int:
        """여러 영상의 트랜스코딩 작업을 video_id 목록으로 일괄 삭제한다.

        Args:
            video_ids: 삭제 대상 영상 ID 목록

        Returns:
            삭제된 행 수
        """
        if not video_ids:
            return 0
        placeholders = ",".join("?" * len(video_ids))
        cursor = self.conn.execute(
            f"DELETE FROM transcoding_jobs WHERE video_id IN ({placeholders})",
            video_ids,
        )
        self.conn.commit()
        return cursor.rowcount

    def get_active_with_paths(self, limit: int = 10) -> list[sqlite3.Row]:
        """진행 중(pending/processing) 트랜스코딩 작업과 원본 영상 경로를 함께 조회한다.

        ``cmd_status`` 등 현황 표시에 사용된다. ``videos`` 테이블과 JOIN하여
        원본 파일 경로(``original_path``)를 포함한다.

        Args:
            limit: 최대 조회 건수 (기본 10)

        Returns:
            ``(id, status, progress_percent, original_path)`` 컬럼을 가진 Row 목록
        """
        cursor = self.conn.execute(
            """
            SELECT tj.id, tj.status, tj.progress_percent, v.original_path
            FROM transcoding_jobs tj
            JOIN videos v ON tj.video_id = v.id
            WHERE tj.status IN ('pending', 'processing')
            ORDER BY tj.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cursor.fetchall()

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
            f"UPDATE transcoding_jobs SET status = ? "
            f"WHERE video_id IN ({placeholders}) AND status = ?",
            [JobStatus.MERGED.value, *video_ids, JobStatus.COMPLETED.value],
        )
        self.conn.commit()
        return cursor.rowcount

    def get_stats(self, period: str | None = None) -> dict[str, object]:
        """트랜스코딩 작업 통계를 집계한다.

        Args:
            period: 기간 필터 (LIKE 패턴). None이면 전체.

        Returns:
            ``status_counts``, ``avg_encoding_speed`` 키를 가진 딕셔너리.
        """
        where = ""
        params: list[str] = []
        if period:
            where = "WHERE tj.created_at LIKE ?"
            params.append(f"{period}%")

        status_rows = self.conn.execute(
            f"""SELECT tj.status, COUNT(*) as cnt
                FROM transcoding_jobs tj {where}
                GROUP BY tj.status""",
            params,
        ).fetchall()
        status_counts = {r["status"]: int(r["cnt"]) for r in status_rows}

        speed_where = (
            "WHERE tj.started_at IS NOT NULL"
            " AND tj.completed_at IS NOT NULL"
            " AND v.duration_seconds > 0"
        )
        speed_params: list[str] = []
        if period:
            speed_where += " AND tj.created_at LIKE ?"
            speed_params.append(f"{period}%")

        speed_row = self.conn.execute(
            f"""SELECT AVG(
                    v.duration_seconds /
                    MAX((julianday(tj.completed_at) - julianday(tj.started_at)) * 86400, 0.001)
                ) as avg_speed
                FROM transcoding_jobs tj
                JOIN videos v ON tj.video_id = v.id
                {speed_where}""",
            speed_params,
        ).fetchone()
        avg_speed = float(speed_row["avg_speed"]) if speed_row["avg_speed"] else None

        return {
            "status_counts": status_counts,
            "avg_encoding_speed": avg_speed,
        }

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
    """``merge_jobs`` 테이블 CRUD 저장소.

    병합 이력·YouTube 업로드 상태·요약 마크다운을 저장하고 조회한다.
    """

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

    def get_by_output_path(self, output_path: Path) -> MergeJob | None:
        """출력 파일 경로로 병합 작업을 조회한다.

        같은 경로에 여러 레코드가 있으면 가장 최근 것을 반환한다.

        Args:
            output_path: 출력 파일 경로

        Returns:
            ``MergeJob`` 또는 해당 경로의 레코드가 없으면 ``None``
        """
        cursor = self.conn.execute(
            "SELECT * FROM merge_jobs WHERE output_path = ? ORDER BY created_at DESC LIMIT 1",
            (str(output_path),),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def get_recent(self, limit: int = 10) -> list[MergeJob]:
        """최근 병합 작업을 지정 개수만큼 조회한다.

        Args:
            limit: 최대 조회 건수 (기본 10)

        Returns:
            최신순 ``MergeJob`` 목록
        """
        cursor = self.conn.execute(
            "SELECT * FROM merge_jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def count_all(self) -> int:
        """전체 병합 작업 수를 반환한다."""
        cursor = self.conn.execute("SELECT COUNT(*) as cnt FROM merge_jobs")
        return int(cursor.fetchone()["cnt"])

    def count_uploaded(self) -> int:
        """YouTube 업로드 완료된 병합 작업 수를 반환한다."""
        cursor = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM merge_jobs WHERE youtube_id IS NOT NULL"
        )
        return int(cursor.fetchone()["cnt"])

    def get_stats(self, period: str | None = None) -> dict[str, object]:
        """병합 작업 통계를 집계한다.

        Args:
            period: 기간 필터 (LIKE 패턴). None이면 전체.

        Returns:
            ``total``, ``completed``, ``failed``, ``uploaded``,
            ``total_size_bytes``, ``total_duration`` 키를 가진 딕셔너리.
        """
        where = ""
        params: list[str] = []
        if period:
            where = "WHERE created_at LIKE ?"
            params.append(f"{period}%")

        row = self.conn.execute(
            f"""SELECT
                    COUNT(*) as total,
                    COALESCE(SUM(
                        CASE WHEN status = 'completed' THEN 1 ELSE 0 END
                    ), 0) as completed,
                    COALESCE(SUM(
                        CASE WHEN status = 'failed' THEN 1 ELSE 0 END
                    ), 0) as failed,
                    COALESCE(SUM(
                        CASE WHEN youtube_id IS NOT NULL THEN 1 ELSE 0 END
                    ), 0) as uploaded,
                    COALESCE(SUM(total_size_bytes), 0) as total_size,
                    COALESCE(SUM(total_duration_seconds), 0) as total_dur
                FROM merge_jobs {where}""",
            params,
        ).fetchone()

        return {
            "total": int(row["total"]),
            "completed": int(row["completed"]),
            "failed": int(row["failed"]),
            "uploaded": int(row["uploaded"]),
            "total_size_bytes": int(row["total_size"]),
            "total_duration": float(row["total_dur"]),
        }

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


class SplitJobRepository:
    """영상 분할 작업 리포지토리.

    ``split_jobs`` 테이블을 관리한다.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def create(
        self,
        merge_job_id: int,
        split_criterion: str,
        split_value: str,
        output_files: list[Path],
        status: JobStatus = JobStatus.COMPLETED,
    ) -> int:
        """분할 작업 생성.

        Args:
            merge_job_id: 원본 merge_job의 ID
            split_criterion: 분할 기준 (``duration`` 또는 ``size``)
            split_value: 분할 값 문자열 (예: ``1h``, ``10G``)
            output_files: 분할된 출력 파일 경로 목록
            status: 초기 상태 (기본: COMPLETED)

        Returns:
            생성된 split_job ID
        """
        output_json = json.dumps([str(p) for p in output_files])
        cursor = self.conn.execute(
            """INSERT INTO split_jobs
               (merge_job_id, split_criterion, split_value, output_files, status)
               VALUES (?, ?, ?, ?, ?)""",
            (merge_job_id, split_criterion, split_value, output_json, status.value),
        )
        self.conn.commit()
        return cursor.lastrowid or 0

    def get_by_id(self, job_id: int) -> SplitJob | None:
        """ID로 분할 작업 조회."""
        cursor = self.conn.execute("SELECT * FROM split_jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def get_by_merge_job_id(self, merge_job_id: int) -> list[SplitJob]:
        """merge_job_id로 분할 작업 목록 조회."""
        cursor = self.conn.execute(
            "SELECT * FROM split_jobs WHERE merge_job_id = ? ORDER BY id",
            (merge_job_id,),
        )
        return [self._row_to_job(row) for row in cursor.fetchall()]

    def update_status(self, job_id: int, status: JobStatus) -> None:
        """분할 작업 상태 업데이트."""
        self.conn.execute(
            "UPDATE split_jobs SET status = ? WHERE id = ?",
            (status.value, job_id),
        )
        self.conn.commit()

    def append_youtube_id(self, job_id: int, youtube_id: str) -> None:
        """분할 작업에 YouTube 영상 ID를 추가한다.

        파트별 업로드 완료 시 호출하여 youtube_ids JSON 배열에 누적.

        Args:
            job_id: split_job ID
            youtube_id: 업로드된 YouTube 영상 ID
        """
        row = self.conn.execute(
            "SELECT youtube_ids FROM split_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return
        try:
            ids = json.loads(row["youtube_ids"]) if row["youtube_ids"] else []
        except (json.JSONDecodeError, TypeError):
            ids = []
        ids.append(youtube_id)
        self.conn.execute(
            "UPDATE split_jobs SET youtube_ids = ? WHERE id = ?",
            (json.dumps(ids), job_id),
        )
        self.conn.commit()

    def delete(self, job_id: int) -> None:
        """분할 작업 삭제."""
        self.conn.execute("DELETE FROM split_jobs WHERE id = ?", (job_id,))
        self.conn.commit()

    def _row_to_job(self, row: sqlite3.Row) -> SplitJob:
        """Row를 SplitJob으로 변환."""
        try:
            output_files = [Path(p) for p in json.loads(row["output_files"])]
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Failed to parse output_files for split_job {row['id']}")
            output_files = []
        try:
            youtube_ids = json.loads(row["youtube_ids"]) if row["youtube_ids"] else []
        except (json.JSONDecodeError, TypeError):
            youtube_ids = []
        return SplitJob(
            id=row["id"],
            merge_job_id=row["merge_job_id"],
            split_criterion=row["split_criterion"],
            split_value=row["split_value"],
            output_files=output_files,
            status=JobStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            youtube_ids=youtube_ids,
            error_message=row["error_message"],
        )


class ArchiveHistoryRepository:
    """``archive_history`` 테이블 CRUD 저장소.

    원본 파일의 이동/삭제 이력을 조회·기록한다.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """초기화."""
        self.conn = conn

    def insert_history(
        self,
        video_id: int,
        operation: str,
        original_path: Path,
        destination_path: Path | None = None,
    ) -> int:
        """아카이브 이력 삽입.

        Args:
            video_id: 영상 ID
            operation: 작업 타입 (move/delete)
            original_path: 원본 경로
            destination_path: 이동 경로 (delete 시 None)

        Returns:
            삽입된 archive_history ID
        """
        cursor = self.conn.execute(
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
        return cursor.lastrowid or 0

    def get_history_by_video(self, video_id: int) -> list[sqlite3.Row]:
        """특정 영상의 아카이브 이력 조회.

        Args:
            video_id: 영상 ID

        Returns:
            아카이브 이력 Row 목록
        """
        cursor = self.conn.execute(
            """
            SELECT * FROM archive_history
            WHERE video_id = ?
            ORDER BY archived_at DESC
            """,
            (video_id,),
        )
        return cursor.fetchall()

    def get_all_history(self, limit: int = 100) -> list[sqlite3.Row]:
        """전체 아카이브 이력 조회.

        Args:
            limit: 최대 조회 건수 (기본 100)

        Returns:
            아카이브 이력 Row 목록
        """
        cursor = self.conn.execute(
            """
            SELECT ah.*, v.original_path as video_original_path
            FROM archive_history ah
            LEFT JOIN videos v ON ah.video_id = v.id
            ORDER BY ah.archived_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cursor.fetchall()

    def count_by_operation(self, operation: str) -> int:
        """특정 작업 타입의 이력 개수 조회.

        Args:
            operation: 작업 타입 (move/delete)

        Returns:
            이력 개수
        """
        cursor = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM archive_history WHERE operation = ?",
            (operation,),
        )
        return int(cursor.fetchone()["cnt"])

    def get_stats(self, period: str | None = None) -> dict[str, int]:
        """아카이브 작업 통계를 집계한다.

        Args:
            period: 기간 필터 (LIKE 패턴). None이면 전체.

        Returns:
            ``moved``, ``deleted`` 키를 가진 딕셔너리.
        """
        where = ""
        params: list[str] = []
        if period:
            where = "WHERE archived_at LIKE ?"
            params.append(f"{period}%")

        rows = self.conn.execute(
            f"""SELECT operation, COUNT(*) as cnt
                FROM archive_history {where}
                GROUP BY operation""",
            params,
        ).fetchall()

        result = {"moved": 0, "deleted": 0}
        for r in rows:
            if r["operation"] == "move":
                result["moved"] = int(r["cnt"])
            elif r["operation"] == "delete":
                result["deleted"] = int(r["cnt"])
        return result
