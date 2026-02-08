"""데이터베이스 테스트."""

import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from tubearchive.database.repository import (
    MergeJobRepository,
    SplitJobRepository,
    TranscodingJobRepository,
    VideoRepository,
)
from tubearchive.database.schema import get_connection, get_default_db_path, init_database
from tubearchive.models.job import JobStatus, SplitJob
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
        assert "split_jobs" in tables

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

    def test_delete_by_video_ids(self, job_repo: TranscodingJobRepository, video_id: int) -> None:
        """video_id 목록으로 트랜스코딩 작업 일괄 삭제."""
        job_repo.create(video_id)
        job_repo.create(video_id)

        deleted = job_repo.delete_by_video_ids([video_id])

        assert deleted == 2
        assert job_repo.get_by_video_id(video_id) == []

    def test_delete_by_video_ids_empty_list(self, job_repo: TranscodingJobRepository) -> None:
        """빈 목록 전달 시 0 반환."""
        assert job_repo.delete_by_video_ids([]) == 0

    def test_get_active_with_paths(self, job_repo: TranscodingJobRepository, video_id: int) -> None:
        """진행 중인 작업과 원본 경로 함께 조회."""
        job_id = job_repo.create(video_id)
        job_repo.update_status(job_id, JobStatus.PROCESSING)

        active = job_repo.get_active_with_paths(limit=10)

        assert len(active) == 1
        assert active[0]["status"] == "processing"
        assert active[0]["original_path"] is not None

    def test_get_active_with_paths_excludes_completed(
        self, job_repo: TranscodingJobRepository, video_id: int, tmp_path: Path
    ) -> None:
        """완료된 작업은 active에 포함되지 않음."""
        job_id = job_repo.create(video_id)
        job_repo.mark_completed(job_id, tmp_path / "output.mp4")

        active = job_repo.get_active_with_paths(limit=10)

        assert len(active) == 0


class TestVideoRepositoryExtended:
    """VideoRepository 추가 메서드 테스트."""

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

    def _insert_video(self, repo: VideoRepository, tmp_path: Path, name: str = "v.mp4") -> int:
        """헬퍼: 영상 삽입 후 ID 반환."""
        video_path = tmp_path / name
        video_path.write_text("")
        video_file = VideoFile(path=video_path, creation_time=datetime.now(), size_bytes=1024)
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
        return repo.insert(video_file, metadata)

    def test_count_all_empty(self, repo: VideoRepository) -> None:
        """빈 DB에서 count_all은 0."""
        assert repo.count_all() == 0

    def test_count_all_with_data(self, repo: VideoRepository, tmp_path: Path) -> None:
        """영상 삽입 후 count_all 정확성."""
        self._insert_video(repo, tmp_path, "a.mp4")
        self._insert_video(repo, tmp_path, "b.mp4")

        assert repo.count_all() == 2

    def test_delete_by_ids(self, repo: VideoRepository, tmp_path: Path) -> None:
        """ID 목록으로 일괄 삭제."""
        id1 = self._insert_video(repo, tmp_path, "a.mp4")
        id2 = self._insert_video(repo, tmp_path, "b.mp4")

        deleted = repo.delete_by_ids([id1, id2])

        assert deleted == 2
        assert repo.count_all() == 0

    def test_delete_by_ids_empty_list(self, repo: VideoRepository) -> None:
        """빈 목록 전달 시 0 반환."""
        assert repo.delete_by_ids([]) == 0


class TestMergeJobRepository:
    """MergeJobRepository 테스트."""

    @pytest.fixture
    def db_conn(self, tmp_path: Path) -> sqlite3.Connection:
        """테스트용 DB 연결."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        yield conn
        conn.close()

    @pytest.fixture
    def repo(self, db_conn: sqlite3.Connection) -> MergeJobRepository:
        """MergeJobRepository 인스턴스."""
        return MergeJobRepository(db_conn)

    def test_get_by_output_path(self, repo: MergeJobRepository, tmp_path: Path) -> None:
        """출력 경로로 MergeJob 조회."""
        output = tmp_path / "merged.mp4"
        repo.create(output_path=output, video_ids=[1, 2], title="Test")

        job = repo.get_by_output_path(output)

        assert job is not None
        assert job.title == "Test"
        assert job.video_ids == [1, 2]

    def test_get_by_output_path_not_found(self, repo: MergeJobRepository, tmp_path: Path) -> None:
        """존재하지 않는 경로 조회 시 None."""
        result = repo.get_by_output_path(tmp_path / "nonexistent.mp4")

        assert result is None

    def test_get_recent(self, repo: MergeJobRepository, tmp_path: Path) -> None:
        """최근 병합 작업 조회."""
        repo.create(output_path=tmp_path / "a.mp4", video_ids=[1])
        repo.create(output_path=tmp_path / "b.mp4", video_ids=[2])
        repo.create(output_path=tmp_path / "c.mp4", video_ids=[3])

        recent = repo.get_recent(limit=2)

        assert len(recent) == 2

    def test_count_all(self, repo: MergeJobRepository, tmp_path: Path) -> None:
        """전체 병합 작업 수."""
        assert repo.count_all() == 0

        repo.create(output_path=tmp_path / "a.mp4", video_ids=[1])
        repo.create(output_path=tmp_path / "b.mp4", video_ids=[2])

        assert repo.count_all() == 2

    def test_count_uploaded(self, repo: MergeJobRepository, tmp_path: Path) -> None:
        """업로드된 작업 수."""
        job_id = repo.create(output_path=tmp_path / "a.mp4", video_ids=[1])
        repo.create(output_path=tmp_path / "b.mp4", video_ids=[2])

        assert repo.count_uploaded() == 0

        repo.update_youtube_id(job_id, "yt_abc123")

        assert repo.count_uploaded() == 1


class TestSplitJobRepository:
    """SplitJobRepository 테스트."""

    @pytest.fixture
    def db_conn(self, tmp_path: Path) -> sqlite3.Connection:
        """테스트용 DB 연결."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)
        yield conn
        conn.close()

    @pytest.fixture
    def merge_repo(self, db_conn: sqlite3.Connection) -> MergeJobRepository:
        """MergeJobRepository 인스턴스."""
        return MergeJobRepository(db_conn)

    @pytest.fixture
    def repo(self, db_conn: sqlite3.Connection) -> SplitJobRepository:
        """SplitJobRepository 인스턴스."""
        return SplitJobRepository(db_conn)

    @pytest.fixture
    def merge_job_id(self, merge_repo: MergeJobRepository, tmp_path: Path) -> int:
        """테스트용 merge_job_id."""
        return merge_repo.create(
            output_path=tmp_path / "merged.mp4",
            video_ids=[1, 2],
            title="Test Merge",
        )

    def test_create_split_job(
        self, repo: SplitJobRepository, merge_job_id: int, tmp_path: Path
    ) -> None:
        """분할 작업 생성."""
        output_files = [tmp_path / "out_001.mp4", tmp_path / "out_002.mp4"]
        job_id = repo.create(
            merge_job_id=merge_job_id,
            split_criterion="duration",
            split_value="1h",
            output_files=output_files,
        )

        assert job_id > 0

    def test_get_by_id(self, repo: SplitJobRepository, merge_job_id: int, tmp_path: Path) -> None:
        """ID로 분할 작업 조회."""
        output_files = [tmp_path / "out_001.mp4", tmp_path / "out_002.mp4"]
        job_id = repo.create(
            merge_job_id=merge_job_id,
            split_criterion="duration",
            split_value="1h",
            output_files=output_files,
        )

        job = repo.get_by_id(job_id)

        assert job is not None
        assert isinstance(job, SplitJob)
        assert job.merge_job_id == merge_job_id
        assert job.split_criterion == "duration"
        assert job.split_value == "1h"
        assert job.output_files == output_files
        assert job.status == JobStatus.COMPLETED

    def test_get_by_id_not_found(self, repo: SplitJobRepository) -> None:
        """존재하지 않는 ID 조회 시 None."""
        assert repo.get_by_id(999) is None

    def test_get_by_merge_job_id(
        self, repo: SplitJobRepository, merge_job_id: int, tmp_path: Path
    ) -> None:
        """merge_job_id로 분할 작업 조회."""
        repo.create(
            merge_job_id=merge_job_id,
            split_criterion="duration",
            split_value="1h",
            output_files=[tmp_path / "a_001.mp4"],
        )
        repo.create(
            merge_job_id=merge_job_id,
            split_criterion="size",
            split_value="10G",
            output_files=[tmp_path / "b_001.mp4"],
        )

        jobs = repo.get_by_merge_job_id(merge_job_id)

        assert len(jobs) == 2
        assert jobs[0].split_criterion == "duration"
        assert jobs[1].split_criterion == "size"

    def test_get_by_merge_job_id_empty(self, repo: SplitJobRepository) -> None:
        """존재하지 않는 merge_job_id 조회 시 빈 리스트."""
        assert repo.get_by_merge_job_id(999) == []

    def test_update_status(
        self, repo: SplitJobRepository, merge_job_id: int, tmp_path: Path
    ) -> None:
        """상태 업데이트."""
        job_id = repo.create(
            merge_job_id=merge_job_id,
            split_criterion="duration",
            split_value="30m",
            output_files=[tmp_path / "out_001.mp4"],
        )

        repo.update_status(job_id, JobStatus.FAILED)

        job = repo.get_by_id(job_id)
        assert job is not None
        assert job.status == JobStatus.FAILED

    def test_output_files_serialization(
        self, repo: SplitJobRepository, merge_job_id: int, tmp_path: Path
    ) -> None:
        """output_files JSON 직렬화/역직렬화 검증."""
        paths = [
            tmp_path / "video_001.mp4",
            tmp_path / "video_002.mp4",
            tmp_path / "video_003.mp4",
        ]
        job_id = repo.create(
            merge_job_id=merge_job_id,
            split_criterion="size",
            split_value="256M",
            output_files=paths,
        )

        job = repo.get_by_id(job_id)
        assert job is not None
        assert len(job.output_files) == 3
        assert all(isinstance(p, Path) for p in job.output_files)
        assert job.output_files == paths

    def test_split_criterion_duration(
        self, repo: SplitJobRepository, merge_job_id: int, tmp_path: Path
    ) -> None:
        """duration 기준 분할 저장."""
        job_id = repo.create(
            merge_job_id=merge_job_id,
            split_criterion="duration",
            split_value="2h15m",
            output_files=[tmp_path / "out.mp4"],
        )

        job = repo.get_by_id(job_id)
        assert job is not None
        assert job.split_criterion == "duration"
        assert job.split_value == "2h15m"

    def test_split_criterion_size(
        self, repo: SplitJobRepository, merge_job_id: int, tmp_path: Path
    ) -> None:
        """size 기준 분할 저장."""
        job_id = repo.create(
            merge_job_id=merge_job_id,
            split_criterion="size",
            split_value="1.5G",
            output_files=[tmp_path / "out.mp4"],
        )

        job = repo.get_by_id(job_id)
        assert job is not None
        assert job.split_criterion == "size"
        assert job.split_value == "1.5G"

    def test_foreign_key_constraint(
        self, repo: SplitJobRepository, db_conn: sqlite3.Connection, tmp_path: Path
    ) -> None:
        """존재하지 않는 merge_job_id로 삽입 시 외래 키 제약."""
        with pytest.raises(sqlite3.IntegrityError):
            repo.create(
                merge_job_id=99999,
                split_criterion="duration",
                split_value="1h",
                output_files=[tmp_path / "out.mp4"],
            )

    def test_delete(self, repo: SplitJobRepository, merge_job_id: int, tmp_path: Path) -> None:
        """분할 작업 삭제."""
        job_id = repo.create(
            merge_job_id=merge_job_id,
            split_criterion="duration",
            split_value="1h",
            output_files=[tmp_path / "out.mp4"],
        )

        repo.delete(job_id)

        assert repo.get_by_id(job_id) is None


class TestGetDefaultDbPath:
    """get_default_db_path 함수 테스트."""

    def test_env_path_with_db_extension(self, tmp_path: Path) -> None:
        """확장자가 .db인 경로는 그대로 반환."""
        db_file = tmp_path / "custom.db"
        with patch.dict("os.environ", {"TUBEARCHIVE_DB_PATH": str(db_file)}):
            result = get_default_db_path()
            assert result == db_file

    def test_env_path_existing_dir(self, tmp_path: Path) -> None:
        """기존 디렉토리 경로이면 디렉토리/tubearchive.db 반환."""
        with patch.dict("os.environ", {"TUBEARCHIVE_DB_PATH": str(tmp_path)}):
            result = get_default_db_path()
            assert result == tmp_path / "tubearchive.db"

    def test_default_under_home(self, tmp_path: Path) -> None:
        """환경변수 없으면 ~/.tubearchive/tubearchive.db 반환."""
        with patch.dict("os.environ", {}, clear=False):
            # TUBEARCHIVE_DB_PATH 제거
            import os

            os.environ.pop("TUBEARCHIVE_DB_PATH", None)
            result = get_default_db_path()
            assert result.name == "tubearchive.db"
            assert ".tubearchive" in str(result)

    def test_env_empty_uses_default(self) -> None:
        """빈 환경변수는 falsy이므로 기본 경로 사용."""
        with patch.dict("os.environ", {"TUBEARCHIVE_DB_PATH": ""}):
            result = get_default_db_path()
            # 빈 문자열은 falsy → 기본 경로
            assert result.name == "tubearchive.db"
            assert ".tubearchive" in str(result)

    def test_env_path_no_extension_creates_dir(self, tmp_path: Path) -> None:
        """확장자 없는 미존재 경로 → 디렉토리 생성 + tubearchive.db 추가."""
        new_dir = tmp_path / "custom_db_dir"
        with patch.dict("os.environ", {"TUBEARCHIVE_DB_PATH": str(new_dir)}):
            result = get_default_db_path()
            assert result == new_dir / "tubearchive.db"
            assert new_dir.is_dir()


class TestGetConnection:
    """get_connection 함수 테스트."""

    def test_returns_valid_connection(self, tmp_path: Path) -> None:
        """유효한 SQLite 연결 반환."""
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        try:
            assert conn.row_factory == sqlite3.Row
        finally:
            conn.close()

    def test_foreign_keys_enabled(self, tmp_path: Path) -> None:
        """PRAGMA foreign_keys ON 설정 확인."""
        db_path = tmp_path / "test.db"
        conn = get_connection(db_path)
        try:
            cursor = conn.execute("PRAGMA foreign_keys")
            assert cursor.fetchone()[0] == 1
        finally:
            conn.close()


class TestMigrationIdempotent:
    """마이그레이션 멱등성 테스트."""

    def test_init_database_twice_is_safe(self, tmp_path: Path) -> None:
        """동일 DB에 init_database 두 번 호출해도 에러 없음."""
        db_path = tmp_path / "test.db"
        conn1 = init_database(db_path)
        conn1.close()

        # 두 번째 호출 → 에러 없이 완료
        conn2 = init_database(db_path)
        cursor = conn2.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        assert "videos" in tables
        assert "transcoding_jobs" in tables
        assert "merge_jobs" in tables
        assert "projects" in tables
        conn2.close()
