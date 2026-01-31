"""데이터베이스 테스트."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from tubearchive.database.repository import TranscodingJobRepository, VideoRepository
from tubearchive.database.schema import init_database
from tubearchive.models.job import JobStatus
from tubearchive.models.video import VideoFile, VideoMetadata


class TestSchema:
    """스키마 테스트."""

    def test_init_database_creates_tables(self, tmp_path: Path) -> None:
        """데이터베이스 초기화 시 테이블 생성."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # 테이블 존재 확인
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        assert "videos" in tables
        assert "transcoding_jobs" in tables
        assert "merge_jobs" in tables

        conn.close()

    def test_schema_allows_merged_status(self, tmp_path: Path) -> None:
        """스키마가 merged 상태를 허용하는지 확인."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        conn.execute(
            "INSERT INTO videos (original_path, creation_time) VALUES (?, ?)",
            ("/test/video.mp4", "2024-01-01T00:00:00"),
        )
        video_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            "INSERT INTO transcoding_jobs (video_id, status) VALUES (?, 'merged')",
            (video_id,),
        )
        conn.commit()

        cursor = conn.execute("SELECT status FROM transcoding_jobs WHERE video_id = ?", (video_id,))
        assert cursor.fetchone()[0] == "merged"
        conn.close()

    def test_foreign_key_constraint(self, tmp_path: Path) -> None:
        """외래 키 제약 조건."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # 존재하지 않는 video_id로 transcoding_job 삽입 시도
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO transcoding_jobs (video_id) VALUES (999)")

        conn.close()


class TestVideoRepository:
    """VideoRepository 테스트."""

    @pytest.fixture
    def db_conn(self, tmp_path: Path) -> sqlite3.Connection:
        """테스트용 DB 연결."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        yield conn
        conn.close()

    @pytest.fixture
    def repo(self, db_conn: sqlite3.Connection) -> VideoRepository:
        """VideoRepository 인스턴스."""
        return VideoRepository(db_conn)

    @pytest.fixture
    def sample_video(self, tmp_path: Path) -> VideoFile:
        """샘플 VideoFile."""
        video_path = tmp_path / "sample.mp4"
        video_path.write_text("")
        return VideoFile(
            path=video_path,
            creation_time=datetime(2024, 1, 15, 10, 30, 0),
            size_bytes=1024,
        )

    @pytest.fixture
    def sample_metadata(self) -> VideoMetadata:
        """샘플 VideoMetadata."""
        return VideoMetadata(
            width=3840,
            height=2160,
            duration_seconds=120.5,
            fps=60.0,
            codec="hevc",
            pixel_format="yuv420p10le",
            is_portrait=False,
            is_vfr=False,
            device_model="NIKON Z 8",
            color_space="bt2020nc",
            color_transfer="smpte2084",
            color_primaries="bt2020",
        )

    def test_insert_video(
        self,
        repo: VideoRepository,
        sample_video: VideoFile,
        sample_metadata: VideoMetadata,
    ) -> None:
        """영상 삽입."""
        video_id = repo.insert(sample_video, sample_metadata)

        assert video_id > 0

    def test_get_video_by_id(
        self,
        repo: VideoRepository,
        sample_video: VideoFile,
        sample_metadata: VideoMetadata,
    ) -> None:
        """ID로 영상 조회."""
        video_id = repo.insert(sample_video, sample_metadata)
        row = repo.get_by_id(video_id)

        assert row is not None
        assert row["original_path"] == str(sample_video.path)
        assert row["device_model"] == "NIKON Z 8"

    def test_get_video_by_path(
        self,
        repo: VideoRepository,
        sample_video: VideoFile,
        sample_metadata: VideoMetadata,
    ) -> None:
        """경로로 영상 조회."""
        repo.insert(sample_video, sample_metadata)
        row = repo.get_by_path(sample_video.path)

        assert row is not None
        assert row["device_model"] == "NIKON Z 8"

    def test_duplicate_path_raises_error(
        self,
        repo: VideoRepository,
        sample_video: VideoFile,
        sample_metadata: VideoMetadata,
    ) -> None:
        """중복 경로 삽입 시 에러."""
        repo.insert(sample_video, sample_metadata)

        with pytest.raises(sqlite3.IntegrityError):
            repo.insert(sample_video, sample_metadata)


class TestTranscodingJobRepository:
    """TranscodingJobRepository 테스트."""

    @pytest.fixture
    def db_conn(self, tmp_path: Path) -> sqlite3.Connection:
        """테스트용 DB 연결."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        yield conn
        conn.close()

    @pytest.fixture
    def video_repo(self, db_conn: sqlite3.Connection) -> VideoRepository:
        """VideoRepository 인스턴스."""
        return VideoRepository(db_conn)

    @pytest.fixture
    def job_repo(self, db_conn: sqlite3.Connection) -> TranscodingJobRepository:
        """TranscodingJobRepository 인스턴스."""
        return TranscodingJobRepository(db_conn)

    @pytest.fixture
    def video_id(self, video_repo: VideoRepository, tmp_path: Path) -> int:
        """테스트용 video_id."""
        video_path = tmp_path / "sample.mp4"
        video_path.write_text("")
        video_file = VideoFile(
            path=video_path,
            creation_time=datetime.now(),
            size_bytes=1024,
        )
        metadata = VideoMetadata(
            width=1920,
            height=1080,
            duration_seconds=60.0,
            fps=30.0,
            codec="h264",
            pixel_format="yuv420p",
            is_portrait=False,
            is_vfr=False,
            device_model=None,
            color_space=None,
            color_transfer=None,
            color_primaries=None,
        )
        return video_repo.insert(video_file, metadata)

    def test_create_job(self, job_repo: TranscodingJobRepository, video_id: int) -> None:
        """작업 생성."""
        job_id = job_repo.create(video_id)

        assert job_id > 0

    def test_get_job_by_id(self, job_repo: TranscodingJobRepository, video_id: int) -> None:
        """ID로 작업 조회."""
        job_id = job_repo.create(video_id)
        job = job_repo.get_by_id(job_id)

        assert job is not None
        assert job.video_id == video_id
        assert job.status == JobStatus.PENDING
        assert job.progress_percent == 0

    def test_update_status(self, job_repo: TranscodingJobRepository, video_id: int) -> None:
        """상태 업데이트."""
        job_id = job_repo.create(video_id)
        job_repo.update_status(job_id, JobStatus.PROCESSING)

        job = job_repo.get_by_id(job_id)
        assert job is not None
        assert job.status == JobStatus.PROCESSING
        assert job.started_at is not None

    def test_update_progress(self, job_repo: TranscodingJobRepository, video_id: int) -> None:
        """진행률 업데이트."""
        job_id = job_repo.create(video_id)
        job_repo.update_progress(job_id, 50)

        job = job_repo.get_by_id(job_id)
        assert job is not None
        assert job.progress_percent == 50

    def test_mark_completed(
        self, job_repo: TranscodingJobRepository, video_id: int, tmp_path: Path
    ) -> None:
        """완료 처리."""
        job_id = job_repo.create(video_id)
        temp_file = tmp_path / "output.mp4"

        job_repo.mark_completed(job_id, temp_file)

        job = job_repo.get_by_id(job_id)
        assert job is not None
        assert job.status == JobStatus.COMPLETED
        assert job.progress_percent == 100
        assert job.completed_at is not None
        assert job.temp_file_path == temp_file

    def test_mark_failed(self, job_repo: TranscodingJobRepository, video_id: int) -> None:
        """실패 처리."""
        job_id = job_repo.create(video_id)
        error_msg = "FFmpeg failed"

        job_repo.mark_failed(job_id, error_msg)

        job = job_repo.get_by_id(job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED
        assert job.error_message == error_msg

    def test_get_incomplete_jobs(self, job_repo: TranscodingJobRepository, video_id: int) -> None:
        """미완료 작업 조회."""
        job_id = job_repo.create(video_id)
        job_repo.update_status(job_id, JobStatus.PROCESSING)

        incomplete = job_repo.get_incomplete_jobs()

        assert len(incomplete) == 1
        assert incomplete[0].id == job_id

    def test_get_jobs_by_video_id(self, job_repo: TranscodingJobRepository, video_id: int) -> None:
        """video_id로 작업 조회."""
        job_id = job_repo.create(video_id)

        jobs = job_repo.get_by_video_id(video_id)

        assert len(jobs) == 1
        assert jobs[0].id == job_id

    def test_mark_merged(
        self, job_repo: TranscodingJobRepository, video_id: int, tmp_path: Path
    ) -> None:
        """병합 후 상태 업데이트."""
        job_id = job_repo.create(video_id)
        job_repo.mark_completed(job_id, tmp_path / "output.mp4")

        job_repo.mark_merged(job_id)

        job = job_repo.get_by_id(job_id)
        assert job is not None
        assert job.status == JobStatus.MERGED

    def test_mark_merged_by_video_ids(
        self, job_repo: TranscodingJobRepository, video_id: int, tmp_path: Path
    ) -> None:
        """일괄 merged 상태 업데이트."""
        job_id = job_repo.create(video_id)
        job_repo.mark_completed(job_id, tmp_path / "output.mp4")

        count = job_repo.mark_merged_by_video_ids([video_id])

        assert count == 1
        job = job_repo.get_by_id(job_id)
        assert job is not None
        assert job.status == JobStatus.MERGED

    def test_mark_merged_skips_non_completed(
        self, job_repo: TranscodingJobRepository, video_id: int
    ) -> None:
        """pending/processing/failed 상태는 merged로 변경되지 않음."""
        job_repo.create(video_id)

        count = job_repo.mark_merged_by_video_ids([video_id])

        assert count == 0
