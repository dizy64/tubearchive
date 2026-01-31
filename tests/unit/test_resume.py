"""Resume 기능 테스트."""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from tubearchive.database.repository import TranscodingJobRepository, VideoRepository
from tubearchive.database.resume import ResumeManager
from tubearchive.database.schema import init_database
from tubearchive.models.job import JobStatus
from tubearchive.models.video import VideoFile, VideoMetadata


class TestResumeManager:
    """ResumeManager 테스트."""

    @pytest.fixture
    def db_conn(self, tmp_path: Path) -> sqlite3.Connection:
        """테스트용 DB 연결."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        yield conn
        conn.close()

    @pytest.fixture
    def resume_mgr(self, db_conn: sqlite3.Connection) -> ResumeManager:
        """ResumeManager 인스턴스."""
        return ResumeManager(db_conn)

    @pytest.fixture
    def video_repo(self, db_conn: sqlite3.Connection) -> VideoRepository:
        """VideoRepository 인스턴스."""
        return VideoRepository(db_conn)

    @pytest.fixture
    def job_repo(self, db_conn: sqlite3.Connection) -> TranscodingJobRepository:
        """TranscodingJobRepository 인스턴스."""
        return TranscodingJobRepository(db_conn)

    @pytest.fixture
    def sample_video_id(self, video_repo: VideoRepository, tmp_path: Path) -> int:
        """샘플 video_id."""
        video_path = tmp_path / "sample.mp4"
        video_path.write_text("")
        video_file = VideoFile(
            path=video_path,
            creation_time=datetime.now(),
            size_bytes=1024,
        )
        metadata = VideoMetadata(
            width=3840,
            height=2160,
            duration_seconds=120.0,
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
        return video_repo.insert(video_file, metadata)

    def test_save_progress(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
    ) -> None:
        """진행률 저장."""
        job_id = job_repo.create(sample_video_id)
        job_repo.update_status(job_id, JobStatus.PROCESSING)

        resume_mgr.save_progress(job_id, 50)

        job = job_repo.get_by_id(job_id)
        assert job is not None
        assert job.progress_percent == 50

    def test_get_resumable_jobs(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
        tmp_path: Path,
    ) -> None:
        """Resume 가능한 작업 조회."""
        # PROCESSING 상태 작업 생성
        job_id = job_repo.create(sample_video_id)
        job_repo.update_status(job_id, JobStatus.PROCESSING)
        job_repo.update_progress(job_id, 30)

        # temp_file_path 설정
        temp_file = tmp_path / "temp.mp4"
        temp_file.write_text("")
        resume_mgr.set_temp_file(job_id, temp_file)

        # Resume 가능한 작업 조회
        resumable = resume_mgr.get_resumable_jobs()

        assert len(resumable) == 1
        assert resumable[0].id == job_id
        assert resumable[0].progress_percent == 30

    def test_get_resumable_jobs_excludes_completed(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
        tmp_path: Path,
    ) -> None:
        """완료된 작업은 Resume 대상에서 제외."""
        job_id = job_repo.create(sample_video_id)
        job_repo.mark_completed(job_id, tmp_path / "output.mp4")

        resumable = resume_mgr.get_resumable_jobs()

        assert len(resumable) == 0

    def test_get_resumable_jobs_excludes_failed(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
    ) -> None:
        """실패한 작업은 Resume 대상에서 제외."""
        job_id = job_repo.create(sample_video_id)
        job_repo.mark_failed(job_id, "FFmpeg error")

        resumable = resume_mgr.get_resumable_jobs()

        assert len(resumable) == 0

    def test_calculate_resume_position(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
    ) -> None:
        """Resume 위치 계산 (progress → 시간)."""
        job_id = job_repo.create(sample_video_id)
        job_repo.update_status(job_id, JobStatus.PROCESSING)
        job_repo.update_progress(job_id, 50)

        job = job_repo.get_by_id(job_id)
        assert job is not None

        # 120초 영상의 50% = 60초
        resume_pos = resume_mgr.calculate_resume_position(job, 120.0)
        assert resume_pos == 60.0

    def test_calculate_resume_position_zero_progress(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
    ) -> None:
        """0% 진행률이면 0초부터."""
        job_id = job_repo.create(sample_video_id)

        job = job_repo.get_by_id(job_id)
        assert job is not None

        resume_pos = resume_mgr.calculate_resume_position(job, 120.0)
        assert resume_pos == 0.0

    def test_reset_job_for_retry(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
    ) -> None:
        """재시도를 위한 작업 초기화."""
        job_id = job_repo.create(sample_video_id)
        job_repo.update_status(job_id, JobStatus.PROCESSING)
        job_repo.update_progress(job_id, 50)
        job_repo.mark_failed(job_id, "Error")

        resume_mgr.reset_job_for_retry(job_id)

        job = job_repo.get_by_id(job_id)
        assert job is not None
        assert job.status == JobStatus.PENDING
        assert job.progress_percent == 0
        assert job.error_message is None

    def test_is_video_processed(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
        tmp_path: Path,
    ) -> None:
        """영상 처리 완료 여부 확인."""
        # 처리 전
        assert resume_mgr.is_video_processed(sample_video_id) is False

        # 처리 완료
        job_id = job_repo.create(sample_video_id)
        job_repo.mark_completed(job_id, tmp_path / "output.mp4")

        assert resume_mgr.is_video_processed(sample_video_id) is True

    def test_get_or_create_job_returns_existing(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
    ) -> None:
        """기존 PENDING/PROCESSING 작업이 있으면 반환."""
        existing_job_id = job_repo.create(sample_video_id)

        job_id = resume_mgr.get_or_create_job(sample_video_id)

        assert job_id == existing_job_id

    def test_get_or_create_job_creates_new(
        self,
        resume_mgr: ResumeManager,
        sample_video_id: int,
    ) -> None:
        """작업이 없으면 새로 생성."""
        job_id = resume_mgr.get_or_create_job(sample_video_id)

        assert job_id > 0

    def test_is_video_processed_false_for_merged(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
        tmp_path: Path,
    ) -> None:
        """merged 상태는 처리 완료로 간주하지 않음."""
        job_id = job_repo.create(sample_video_id)
        job_repo.mark_completed(job_id, tmp_path / "output.mp4")
        assert resume_mgr.is_video_processed(sample_video_id) is True

        job_repo.mark_merged(job_id)
        assert resume_mgr.is_video_processed(sample_video_id) is False

    def test_get_or_create_job_creates_new_when_merged(
        self,
        resume_mgr: ResumeManager,
        job_repo: TranscodingJobRepository,
        sample_video_id: int,
        tmp_path: Path,
    ) -> None:
        """merged 작업만 있으면 새 작업 생성."""
        job_id = job_repo.create(sample_video_id)
        job_repo.mark_completed(job_id, tmp_path / "output.mp4")
        job_repo.mark_merged(job_id)

        new_job_id = resume_mgr.get_or_create_job(sample_video_id)

        assert new_job_id != job_id
        assert new_job_id > 0
