"""프로젝트 모드 단위 테스트.

ProjectRepository CRUD, 다대다 관계, 날짜 범위 자동 갱신,
프로젝트 상세 조회, 엣지 케이스를 검증한다.
"""

import io
import json
import sqlite3
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from tubearchive.commands.project import (
    _format_date_range,
    _format_project_status,
    print_project_detail,
    print_project_list,
)
from tubearchive.database.repository import (
    MergeJobRepository,
    ProjectRepository,
)
from tubearchive.database.schema import init_database
from tubearchive.models.job import Project


class TestProjectSchema:
    """프로젝트 스키마 테스트."""

    def test_init_database_creates_project_tables(self, tmp_path: Path) -> None:
        """데이터베이스 초기화 시 프로젝트 관련 테이블 생성."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        assert "projects" in tables
        assert "project_merge_jobs" in tables
        conn.close()

    def test_project_name_unique_constraint(self, tmp_path: Path) -> None:
        """프로젝트 이름 UNIQUE 제약 조건."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        conn.execute("INSERT INTO projects (name) VALUES (?)", ("제주도 여행",))
        conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO projects (name) VALUES (?)", ("제주도 여행",))
        conn.close()

    def test_project_merge_jobs_composite_pk(self, tmp_path: Path) -> None:
        """project_merge_jobs 복합 PK 제약 조건."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        # 프로젝트 생성
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("테스트",))
        project_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # merge_job 생성
        conn.execute(
            "INSERT INTO merge_jobs (output_path, video_ids) VALUES (?, ?)",
            ("/out/merged.mp4", "[1]"),
        )
        merge_job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        # 첫 연결 성공
        conn.execute(
            "INSERT INTO project_merge_jobs (project_id, merge_job_id) VALUES (?, ?)",
            (project_id, merge_job_id),
        )
        conn.commit()

        # 중복 연결 시 IntegrityError
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO project_merge_jobs (project_id, merge_job_id) VALUES (?, ?)",
                (project_id, merge_job_id),
            )
        conn.close()

    def test_cascade_delete_project_removes_links(self, tmp_path: Path) -> None:
        """프로젝트 삭제 시 project_merge_jobs도 CASCADE 삭제."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        conn.execute("INSERT INTO projects (name) VALUES (?)", ("삭제 테스트",))
        project_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            "INSERT INTO merge_jobs (output_path, video_ids) VALUES (?, ?)",
            ("/out/merged.mp4", "[1]"),
        )
        merge_job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            "INSERT INTO project_merge_jobs (project_id, merge_job_id) VALUES (?, ?)",
            (project_id, merge_job_id),
        )
        conn.commit()

        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()

        cursor = conn.execute(
            "SELECT COUNT(*) FROM project_merge_jobs WHERE project_id = ?",
            (project_id,),
        )
        assert cursor.fetchone()[0] == 0
        conn.close()

    def test_cascade_delete_merge_job_removes_links(self, tmp_path: Path) -> None:
        """merge_job 삭제 시 project_merge_jobs도 CASCADE 삭제."""
        db_path = tmp_path / "test.db"
        conn = init_database(db_path)

        conn.execute("INSERT INTO projects (name) VALUES (?)", ("프로젝트A",))
        project_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            "INSERT INTO merge_jobs (output_path, video_ids) VALUES (?, ?)",
            ("/out/merged.mp4", "[1]"),
        )
        merge_job_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            "INSERT INTO project_merge_jobs (project_id, merge_job_id) VALUES (?, ?)",
            (project_id, merge_job_id),
        )
        conn.commit()

        conn.execute("DELETE FROM merge_jobs WHERE id = ?", (merge_job_id,))
        conn.commit()

        cursor = conn.execute(
            "SELECT COUNT(*) FROM project_merge_jobs WHERE merge_job_id = ?",
            (merge_job_id,),
        )
        assert cursor.fetchone()[0] == 0
        conn.close()

    def test_migration_adds_project_tables_to_existing_db(self, tmp_path: Path) -> None:
        """기존 DB에 마이그레이션으로 프로젝트 테이블 추가."""
        db_path = tmp_path / "test.db"

        # 1차: 프로젝트 테이블 없는 구 스키마 시뮬레이션
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_path TEXT NOT NULL UNIQUE,
                creation_time TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS merge_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                output_path TEXT NOT NULL,
                video_ids TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                youtube_id TEXT,
                title TEXT,
                date TEXT,
                total_duration_seconds REAL,
                total_size_bytes INTEGER,
                clips_info_json TEXT,
                summary_markdown TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS transcoding_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                temp_file_path TEXT,
                status TEXT DEFAULT 'pending'
                    CHECK(status IN ('pending','processing','completed','failed','merged')),
                progress_percent INTEGER DEFAULT 0,
                started_at TEXT,
                completed_at TEXT,
                error_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS split_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                merge_job_id INTEGER NOT NULL,
                split_criterion TEXT NOT NULL,
                split_value TEXT NOT NULL,
                output_files TEXT NOT NULL,
                youtube_ids TEXT,
                error_message TEXT,
                status TEXT DEFAULT 'completed',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (merge_job_id) REFERENCES merge_jobs(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS archive_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                operation TEXT NOT NULL,
                original_path TEXT NOT NULL,
                destination_path TEXT,
                archived_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
            );
        """)
        conn.commit()
        conn.close()

        # 2차: init_database 호출 → 마이그레이션 실행
        conn = init_database(db_path)

        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "projects" in tables
        assert "project_merge_jobs" in tables
        conn.close()


# ---------------------------------------------------------------------------
# Repository 테스트 fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def db_conn(tmp_path: Path) -> Generator[sqlite3.Connection]:
    """테스트용 DB 연결."""
    db_path = tmp_path / "test.db"
    conn = init_database(db_path)
    yield conn
    conn.close()


def _create_merge_job(
    conn: sqlite3.Connection,
    output_path: str = "/out/merged.mp4",
    title: str | None = "테스트 영상",
    date: str | None = "2025-01-15",
    duration: float | None = 120.0,
    size: int | None = 1024000,
) -> int:
    """테스트용 merge_job 생성 헬퍼."""
    repo = MergeJobRepository(conn)
    return repo.create(
        output_path=Path(output_path),
        video_ids=[1],
        title=title,
        date=date,
        total_duration_seconds=duration,
        total_size_bytes=size,
    )


class TestProjectRepository:
    """ProjectRepository CRUD 테스트."""

    def test_create_project(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트 생성."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("제주도 여행", description="2025년 봄 제주")

        assert project_id > 0

    def test_create_project_without_description(self, db_conn: sqlite3.Connection) -> None:
        """설명 없이 프로젝트 생성."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("간단 프로젝트")

        project = repo.get_by_id(project_id)
        assert project is not None
        assert project.name == "간단 프로젝트"
        assert project.description is None

    def test_create_duplicate_name_raises(self, db_conn: sqlite3.Connection) -> None:
        """중복 이름 프로젝트 생성 시 IntegrityError."""
        repo = ProjectRepository(db_conn)
        repo.create("중복 테스트")

        with pytest.raises(sqlite3.IntegrityError):
            repo.create("중복 테스트")

    def test_get_by_id(self, db_conn: sqlite3.Connection) -> None:
        """ID로 프로젝트 조회."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("조회 테스트", description="설명입니다")

        project = repo.get_by_id(project_id)
        assert project is not None
        assert project.id == project_id
        assert project.name == "조회 테스트"
        assert project.description == "설명입니다"
        assert project.date_range_start is None
        assert project.date_range_end is None
        assert project.playlist_id is None
        assert isinstance(project, Project)

    def test_get_by_id_not_found(self, db_conn: sqlite3.Connection) -> None:
        """존재하지 않는 ID 조회."""
        repo = ProjectRepository(db_conn)
        assert repo.get_by_id(9999) is None

    def test_get_by_name(self, db_conn: sqlite3.Connection) -> None:
        """이름으로 프로젝트 조회."""
        repo = ProjectRepository(db_conn)
        repo.create("이름 조회")

        project = repo.get_by_name("이름 조회")
        assert project is not None
        assert project.name == "이름 조회"

    def test_get_by_name_not_found(self, db_conn: sqlite3.Connection) -> None:
        """존재하지 않는 이름 조회."""
        repo = ProjectRepository(db_conn)
        assert repo.get_by_name("없는 프로젝트") is None

    def test_get_all(self, db_conn: sqlite3.Connection) -> None:
        """모든 프로젝트 조회."""
        repo = ProjectRepository(db_conn)
        repo.create("프로젝트A")
        repo.create("프로젝트B")
        repo.create("프로젝트C")

        projects = repo.get_all()
        assert len(projects) == 3

    def test_get_all_empty(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트 없을 때 빈 리스트."""
        repo = ProjectRepository(db_conn)
        assert repo.get_all() == []

    def test_update_description(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트 설명 업데이트."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("업데이트 테스트")

        repo.update_description(project_id, "새로운 설명")

        project = repo.get_by_id(project_id)
        assert project is not None
        assert project.description == "새로운 설명"

    def test_update_playlist_id(self, db_conn: sqlite3.Connection) -> None:
        """플레이리스트 ID 업데이트."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("플레이리스트 테스트")

        repo.update_playlist_id(project_id, "PLxxxxxxxx")

        project = repo.get_by_id(project_id)
        assert project is not None
        assert project.playlist_id == "PLxxxxxxxx"

    def test_delete_project(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트 삭제."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("삭제할 프로젝트")

        repo.delete(project_id)
        assert repo.get_by_id(project_id) is None

    def test_count_all(self, db_conn: sqlite3.Connection) -> None:
        """전체 프로젝트 수 조회."""
        repo = ProjectRepository(db_conn)
        assert repo.count_all() == 0

        repo.create("프로젝트1")
        repo.create("프로젝트2")
        assert repo.count_all() == 2

    def test_get_or_create_new(self, db_conn: sqlite3.Connection) -> None:
        """get_or_create: 새 프로젝트 생성."""
        repo = ProjectRepository(db_conn)
        project = repo.get_or_create("새 프로젝트", description="설명")

        assert project.name == "새 프로젝트"
        assert project.description == "설명"
        assert project.id is not None

    def test_get_or_create_existing(self, db_conn: sqlite3.Connection) -> None:
        """get_or_create: 기존 프로젝트 반환."""
        repo = ProjectRepository(db_conn)
        repo.create("기존 프로젝트", description="원래 설명")

        project = repo.get_or_create("기존 프로젝트", description="무시될 설명")
        assert project.description == "원래 설명"
        assert repo.count_all() == 1


class TestProjectMergeJobRelation:
    """프로젝트 ↔ merge_job 다대다 관계 테스트."""

    def test_add_merge_job_to_project(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트에 merge_job 연결."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("관계 테스트")
        merge_job_id = _create_merge_job(db_conn)

        repo.add_merge_job(project_id, merge_job_id)

        ids = repo.get_merge_job_ids(project_id)
        assert merge_job_id in ids

    def test_add_duplicate_merge_job_ignored(self, db_conn: sqlite3.Connection) -> None:
        """이미 연결된 merge_job 중복 추가 시 무시."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("중복 연결 테스트")
        merge_job_id = _create_merge_job(db_conn)

        repo.add_merge_job(project_id, merge_job_id)
        repo.add_merge_job(project_id, merge_job_id)  # 중복

        ids = repo.get_merge_job_ids(project_id)
        assert len(ids) == 1

    def test_multiple_merge_jobs_per_project(self, db_conn: sqlite3.Connection) -> None:
        """하나의 프로젝트에 여러 merge_job 연결."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("다중 영상 프로젝트")

        mj1 = _create_merge_job(db_conn, output_path="/out/day1.mp4", date="2025-01-15")
        mj2 = _create_merge_job(db_conn, output_path="/out/day2.mp4", date="2025-01-16")
        mj3 = _create_merge_job(db_conn, output_path="/out/day3.mp4", date="2025-01-17")

        repo.add_merge_job(project_id, mj1)
        repo.add_merge_job(project_id, mj2)
        repo.add_merge_job(project_id, mj3)

        ids = repo.get_merge_job_ids(project_id)
        assert len(ids) == 3

    def test_merge_job_in_multiple_projects(self, db_conn: sqlite3.Connection) -> None:
        """하나의 merge_job이 여러 프로젝트에 속하는 다대다 관계."""
        repo = ProjectRepository(db_conn)
        p1 = repo.create("프로젝트A")
        p2 = repo.create("프로젝트B")
        merge_job_id = _create_merge_job(db_conn)

        repo.add_merge_job(p1, merge_job_id)
        repo.add_merge_job(p2, merge_job_id)

        project_ids = repo.get_project_ids_for_merge_job(merge_job_id)
        assert len(project_ids) == 2
        assert p1 in project_ids
        assert p2 in project_ids

    def test_remove_merge_job(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트에서 merge_job 연결 해제."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("연결 해제 테스트")
        mj1 = _create_merge_job(db_conn, output_path="/out/v1.mp4")
        mj2 = _create_merge_job(db_conn, output_path="/out/v2.mp4")

        repo.add_merge_job(project_id, mj1)
        repo.add_merge_job(project_id, mj2)

        repo.remove_merge_job(project_id, mj1)

        ids = repo.get_merge_job_ids(project_id)
        assert len(ids) == 1
        assert mj2 in ids

    def test_get_merge_jobs(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트에 연결된 merge_job 객체 목록 조회."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("객체 조회 테스트")

        mj1 = _create_merge_job(
            db_conn,
            output_path="/out/day1.mp4",
            title="1일차",
            date="2025-03-10",
            duration=300.0,
        )
        mj2 = _create_merge_job(
            db_conn,
            output_path="/out/day2.mp4",
            title="2일차",
            date="2025-03-11",
            duration=450.0,
        )

        repo.add_merge_job(project_id, mj1)
        repo.add_merge_job(project_id, mj2)

        merge_jobs = repo.get_merge_jobs(project_id)
        assert len(merge_jobs) == 2
        assert merge_jobs[0].title == "1일차"
        assert merge_jobs[1].title == "2일차"

    def test_get_merge_jobs_empty_project(self, db_conn: sqlite3.Connection) -> None:
        """빈 프로젝트의 merge_job 목록은 빈 리스트."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("빈 프로젝트")

        assert repo.get_merge_jobs(project_id) == []
        assert repo.get_merge_job_ids(project_id) == []


class TestProjectDateRange:
    """프로젝트 날짜 범위 자동 갱신 테스트."""

    def test_date_range_set_on_add(self, db_conn: sqlite3.Connection) -> None:
        """merge_job 추가 시 날짜 범위 자동 설정."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("날짜 테스트")

        mj = _create_merge_job(db_conn, date="2025-06-15")
        repo.add_merge_job(project_id, mj)

        project = repo.get_by_id(project_id)
        assert project is not None
        assert project.date_range_start == "2025-06-15"
        assert project.date_range_end == "2025-06-15"

    def test_date_range_expands(self, db_conn: sqlite3.Connection) -> None:
        """여러 merge_job 추가 시 날짜 범위 확장."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("범위 확장 테스트")

        mj1 = _create_merge_job(db_conn, output_path="/out/a.mp4", date="2025-06-10")
        mj2 = _create_merge_job(db_conn, output_path="/out/b.mp4", date="2025-06-20")

        repo.add_merge_job(project_id, mj1)
        repo.add_merge_job(project_id, mj2)

        project = repo.get_by_id(project_id)
        assert project is not None
        assert project.date_range_start == "2025-06-10"
        assert project.date_range_end == "2025-06-20"

    def test_date_range_shrinks_on_remove(self, db_conn: sqlite3.Connection) -> None:
        """merge_job 제거 시 날짜 범위 축소."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("범위 축소 테스트")

        mj1 = _create_merge_job(db_conn, output_path="/out/a.mp4", date="2025-06-01")
        mj2 = _create_merge_job(db_conn, output_path="/out/b.mp4", date="2025-06-15")
        mj3 = _create_merge_job(db_conn, output_path="/out/c.mp4", date="2025-06-30")

        repo.add_merge_job(project_id, mj1)
        repo.add_merge_job(project_id, mj2)
        repo.add_merge_job(project_id, mj3)

        # 맨 마지막 날짜의 merge_job 제거
        repo.remove_merge_job(project_id, mj3)

        project = repo.get_by_id(project_id)
        assert project is not None
        assert project.date_range_start == "2025-06-01"
        assert project.date_range_end == "2025-06-15"

    def test_date_range_null_when_all_removed(self, db_conn: sqlite3.Connection) -> None:
        """모든 merge_job 제거 시 날짜 범위 NULL."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("빈 범위 테스트")

        mj = _create_merge_job(db_conn, date="2025-01-01")
        repo.add_merge_job(project_id, mj)
        repo.remove_merge_job(project_id, mj)

        project = repo.get_by_id(project_id)
        assert project is not None
        assert project.date_range_start is None
        assert project.date_range_end is None

    def test_date_range_with_null_date_merge_job(self, db_conn: sqlite3.Connection) -> None:
        """날짜 없는 merge_job은 날짜 범위에 영향 없음."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("날짜 없는 영상 테스트")

        mj1 = _create_merge_job(db_conn, output_path="/out/a.mp4", date="2025-03-01")
        mj2 = _create_merge_job(db_conn, output_path="/out/b.mp4", date=None)

        repo.add_merge_job(project_id, mj1)
        repo.add_merge_job(project_id, mj2)

        project = repo.get_by_id(project_id)
        assert project is not None
        assert project.date_range_start == "2025-03-01"
        assert project.date_range_end == "2025-03-01"

    def test_date_range_only_null_dates(self, db_conn: sqlite3.Connection) -> None:
        """날짜가 모두 NULL인 merge_job만 있으면 날짜 범위도 NULL."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("모두 NULL 날짜")

        mj = _create_merge_job(db_conn, date=None)
        repo.add_merge_job(project_id, mj)

        project = repo.get_by_id(project_id)
        assert project is not None
        assert project.date_range_start is None
        assert project.date_range_end is None


class TestProjectAllWithStats:
    """get_all_with_stats 배치 조회 테스트."""

    def test_all_with_stats_single_query(self, db_conn: sqlite3.Connection) -> None:
        """여러 프로젝트의 통계를 단일 쿼리로 정확하게 집계."""
        repo = ProjectRepository(db_conn)
        p1 = repo.create("프로젝트A")
        p2 = repo.create("프로젝트B")

        mj1 = _create_merge_job(db_conn, output_path="/out/a1.mp4", duration=600.0, size=5_000_000)
        mj2 = _create_merge_job(db_conn, output_path="/out/a2.mp4", duration=900.0, size=8_000_000)
        mj3 = _create_merge_job(db_conn, output_path="/out/b1.mp4", duration=300.0, size=2_000_000)

        repo.add_merge_job(p1, mj1)
        repo.add_merge_job(p1, mj2)
        repo.add_merge_job(p2, mj3)

        results = repo.get_all_with_stats()
        stats_map = {project.id: stats for project, stats in results}

        assert stats_map[p1].total_count == 2
        assert stats_map[p1].total_duration_seconds == 1500.0
        assert stats_map[p1].total_size_bytes == 13_000_000
        assert stats_map[p1].uploaded_count == 0

        assert stats_map[p2].total_count == 1
        assert stats_map[p2].total_duration_seconds == 300.0
        assert stats_map[p2].total_size_bytes == 2_000_000

    def test_all_with_stats_empty_project(self, db_conn: sqlite3.Connection) -> None:
        """빈 프로젝트의 통계는 모두 0."""
        repo = ProjectRepository(db_conn)
        repo.create("빈 프로젝트")

        results = repo.get_all_with_stats()
        assert len(results) == 1
        _, stats = results[0]
        assert stats.total_count == 0
        assert stats.total_duration_seconds == 0.0
        assert stats.total_size_bytes == 0
        assert stats.uploaded_count == 0

    def test_all_with_stats_uploaded_count(self, db_conn: sqlite3.Connection) -> None:
        """업로드된 merge_job만 uploaded_count에 집계."""
        repo = ProjectRepository(db_conn)
        p1 = repo.create("업로드 테스트")
        mj1 = _create_merge_job(db_conn, output_path="/out/u1.mp4")
        mj2 = _create_merge_job(db_conn, output_path="/out/u2.mp4")

        merge_repo = MergeJobRepository(db_conn)
        merge_repo.update_youtube_id(mj1, "yt123")

        repo.add_merge_job(p1, mj1)
        repo.add_merge_job(p1, mj2)

        results = repo.get_all_with_stats()
        _, stats = results[0]
        assert stats.uploaded_count == 1
        assert stats.total_count == 2

    def test_all_with_stats_no_projects(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트 없을 때 빈 리스트."""
        repo = ProjectRepository(db_conn)
        assert repo.get_all_with_stats() == []


class TestProjectDetail:
    """프로젝트 상세 조회 테스트."""

    def test_detail_with_merge_jobs(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트 상세: merge_job 포함 시 통계 정확."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("상세 테스트")

        mj1 = _create_merge_job(
            db_conn,
            output_path="/out/d1.mp4",
            title="Day 1",
            date="2025-07-01",
            duration=600.0,
            size=5_000_000,
        )
        mj2 = _create_merge_job(
            db_conn,
            output_path="/out/d2.mp4",
            title="Day 2",
            date="2025-07-02",
            duration=900.0,
            size=8_000_000,
        )

        repo.add_merge_job(project_id, mj1)
        repo.add_merge_job(project_id, mj2)

        detail = repo.get_detail(project_id)
        assert detail is not None
        assert detail.total_count == 2
        assert detail.total_duration_seconds == 1500.0
        assert detail.total_size_bytes == 13_000_000
        assert detail.uploaded_count == 0

    def test_detail_date_groups(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트 상세: 날짜별 자동 그룹핑."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("날짜 그룹핑")

        mj1 = _create_merge_job(
            db_conn,
            output_path="/out/a1.mp4",
            date="2025-08-01",
        )
        mj2 = _create_merge_job(
            db_conn,
            output_path="/out/a2.mp4",
            date="2025-08-01",
        )
        mj3 = _create_merge_job(
            db_conn,
            output_path="/out/b1.mp4",
            date="2025-08-02",
        )

        repo.add_merge_job(project_id, mj1)
        repo.add_merge_job(project_id, mj2)
        repo.add_merge_job(project_id, mj3)

        detail = repo.get_detail(project_id)
        assert detail is not None
        assert len(detail.date_groups["2025-08-01"]) == 2
        assert len(detail.date_groups["2025-08-02"]) == 1

    def test_detail_uploaded_count(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트 상세: 업로드 상태 집계."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("업로드 상태 테스트")

        mj1 = _create_merge_job(db_conn, output_path="/out/up1.mp4")
        mj2 = _create_merge_job(db_conn, output_path="/out/up2.mp4")

        # mj1만 업로드 완료 처리
        merge_repo = MergeJobRepository(db_conn)
        merge_repo.update_youtube_id(mj1, "ytid123")

        repo.add_merge_job(project_id, mj1)
        repo.add_merge_job(project_id, mj2)

        detail = repo.get_detail(project_id)
        assert detail is not None
        assert detail.uploaded_count == 1
        assert detail.total_count == 2

    def test_detail_not_found(self, db_conn: sqlite3.Connection) -> None:
        """존재하지 않는 프로젝트 상세 조회."""
        repo = ProjectRepository(db_conn)
        assert repo.get_detail(9999) is None

    def test_detail_empty_project(self, db_conn: sqlite3.Connection) -> None:
        """빈 프로젝트 상세 조회."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("빈 프로젝트")

        detail = repo.get_detail(project_id)
        assert detail is not None
        assert detail.total_count == 0
        assert detail.total_duration_seconds == 0.0
        assert detail.total_size_bytes == 0
        assert detail.uploaded_count == 0
        assert detail.date_groups == {}

    def test_detail_with_null_duration_and_size(self, db_conn: sqlite3.Connection) -> None:
        """duration/size가 NULL인 merge_job 포함 시 정상 집계."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("NULL 값 테스트")

        mj1 = _create_merge_job(
            db_conn,
            output_path="/out/n1.mp4",
            duration=None,
            size=None,
        )
        mj2 = _create_merge_job(
            db_conn,
            output_path="/out/n2.mp4",
            duration=300.0,
            size=2_000_000,
        )

        repo.add_merge_job(project_id, mj1)
        repo.add_merge_job(project_id, mj2)

        detail = repo.get_detail(project_id)
        assert detail is not None
        assert detail.total_duration_seconds == 300.0
        assert detail.total_size_bytes == 2_000_000

    def test_detail_date_group_with_null_date(self, db_conn: sqlite3.Connection) -> None:
        """날짜 없는 merge_job은 '날짜 미상' 그룹."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("NULL 날짜 그룹핑")

        mj = _create_merge_job(db_conn, date=None)
        repo.add_merge_job(project_id, mj)

        detail = repo.get_detail(project_id)
        assert detail is not None
        assert "날짜 미상" in detail.date_groups


class TestProjectDeleteCascade:
    """프로젝트 삭제 시 연관 데이터 정리 테스트."""

    def test_delete_clears_project_merge_links(self, db_conn: sqlite3.Connection) -> None:
        """프로젝트 삭제 시 project_merge_jobs 관계도 삭제."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("삭제 CASCADE 테스트")

        mj = _create_merge_job(db_conn)
        repo.add_merge_job(project_id, mj)

        repo.delete(project_id)

        # merge_job 자체는 남아있어야 함
        merge_repo = MergeJobRepository(db_conn)
        assert merge_repo.get_by_id(mj) is not None

        # project_merge_jobs 관계는 삭제됨
        cursor = db_conn.execute(
            "SELECT COUNT(*) FROM project_merge_jobs WHERE project_id = ?",
            (project_id,),
        )
        assert cursor.fetchone()[0] == 0

    def test_delete_merge_job_does_not_delete_project(self, db_conn: sqlite3.Connection) -> None:
        """merge_job 삭제 시 프로젝트는 유지."""
        repo = ProjectRepository(db_conn)
        project_id = repo.create("merge_job 삭제 테스트")

        mj = _create_merge_job(db_conn)
        repo.add_merge_job(project_id, mj)

        merge_repo = MergeJobRepository(db_conn)
        merge_repo.delete(mj)

        project = repo.get_by_id(project_id)
        assert project is not None
        assert repo.get_merge_job_ids(project_id) == []


class TestFormatDateRange:
    """날짜 범위 포맷 테스트."""

    def _make_project(self, start: str | None = None, end: str | None = None) -> Project:
        return Project(
            id=1,
            name="테스트",
            description=None,
            date_range_start=start,
            date_range_end=end,
            playlist_id=None,
            created_at=datetime(2025, 1, 1),
            updated_at=datetime(2025, 1, 1),
        )

    def test_same_start_end(self) -> None:
        """시작일과 종료일이 같으면 단일 날짜."""
        project = self._make_project("2025-01-15", "2025-01-15")
        assert _format_date_range(project) == "2025-01-15"

    def test_different_start_end(self) -> None:
        """시작일과 종료일이 다르면 범위 표시."""
        project = self._make_project("2025-01-15", "2025-01-20")
        assert _format_date_range(project) == "2025-01-15 ~ 2025-01-20"

    def test_no_dates(self) -> None:
        """날짜 없으면 대시."""
        project = self._make_project(None, None)
        assert _format_date_range(project) == "-"

    def test_start_only(self) -> None:
        """시작일만 있어도 대시 (end 필수)."""
        project = self._make_project("2025-01-15", None)
        assert _format_date_range(project) == "-"


class TestFormatProjectStatus:
    """프로젝트 상태 포맷 테스트."""

    def test_empty(self) -> None:
        """영상 없는 빈 프로젝트."""
        assert _format_project_status(0, 0) == "빈 프로젝트"

    def test_all_uploaded(self) -> None:
        """전체 업로드 완료."""
        assert _format_project_status(3, 3) == "전체 업로드 (3개)"

    def test_partial_upload(self) -> None:
        """부분 업로드."""
        assert _format_project_status(3, 1) == "부분 업로드 (1/3)"

    def test_no_upload(self) -> None:
        """업로드 없음."""
        assert _format_project_status(3, 0) == "영상 3개"


class TestPrintProjectList:
    """print_project_list 출력 테스트."""

    @pytest.fixture
    def db_conn_with_project(self, tmp_path: Path) -> sqlite3.Connection:
        """프로젝트 데이터가 포함된 DB."""
        conn = init_database(tmp_path / "test.db")
        repo = ProjectRepository(conn)
        project_id = repo.create("테스트 프로젝트")

        # merge_job 추가
        merge_repo = MergeJobRepository(conn)
        mj_id = merge_repo.create(
            output_path=tmp_path / "out.mp4",
            video_ids=[1],
            title="영상1",
            date="2025-01-15",
        )
        repo.add_merge_job(project_id, mj_id)
        conn.commit()
        return conn

    def test_json_with_projects(self, db_conn_with_project: sqlite3.Connection) -> None:
        """JSON 형식 출력 검증."""
        stream = io.StringIO()
        print_project_list(db_conn_with_project, output_format="json", stream=stream)
        output = stream.getvalue()
        data = json.loads(output)

        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["name"] == "테스트 프로젝트"
        assert "id" in data[0]
        assert "merge_job_count" in data[0]

    def test_json_empty(self, tmp_path: Path) -> None:
        """빈 프로젝트 목록 JSON."""
        conn = init_database(tmp_path / "test.db")
        stream = io.StringIO()
        print_project_list(conn, output_format="json", stream=stream)
        data = json.loads(stream.getvalue())
        assert data == []
        conn.close()

    def test_table_empty(self, tmp_path: Path) -> None:
        """빈 프로젝트 목록 테이블 → '프로젝트 없음' 메시지."""
        conn = init_database(tmp_path / "test.db")
        stream = io.StringIO()
        print_project_list(conn, output_format="table", stream=stream)
        output = stream.getvalue()
        assert "프로젝트 없음" in output
        conn.close()

    def test_table_with_projects(self, db_conn_with_project: sqlite3.Connection) -> None:
        """프로젝트 있는 테이블 출력 → '프로젝트 목록' 헤더."""
        stream = io.StringIO()
        with patch("tubearchive.commands.project.render_table"):
            print_project_list(db_conn_with_project, output_format="table", stream=stream)
        output = stream.getvalue()
        assert "프로젝트 목록" in output


class TestPrintProjectDetail:
    """print_project_detail 출력 테스트."""

    @pytest.fixture
    def db_with_detail(self, tmp_path: Path) -> tuple[sqlite3.Connection, int]:
        """상세 조회용 DB."""
        conn = init_database(tmp_path / "test.db")
        repo = ProjectRepository(conn)
        project_id = repo.create("상세 테스트")

        merge_repo = MergeJobRepository(conn)
        mj_id = merge_repo.create(
            output_path=tmp_path / "out.mp4",
            video_ids=[1],
            title="영상1",
            date="2025-01-15",
            total_duration_seconds=120.0,
            total_size_bytes=1024 * 1024,
        )
        repo.add_merge_job(project_id, mj_id)
        conn.commit()
        return conn, project_id

    def test_json_with_merge_jobs(self, db_with_detail: tuple[sqlite3.Connection, int]) -> None:
        """JSON 상세 출력 구조 검증."""
        conn, project_id = db_with_detail
        stream = io.StringIO()
        print_project_detail(conn, project_id, output_format="json", stream=stream)
        data = json.loads(stream.getvalue())

        assert "project" in data
        assert data["project"]["name"] == "상세 테스트"
        assert "summary" in data
        assert "merge_jobs" in data
        assert "date_groups" in data

    def test_not_found(self, tmp_path: Path) -> None:
        """존재하지 않는 프로젝트 → stderr에 에러 메시지."""
        conn = init_database(tmp_path / "test.db")
        stream = io.StringIO()
        # stderr를 캡처하기 위해 sys.stderr를 패치
        with patch("tubearchive.commands.project.sys") as mock_sys:
            mock_sys.stderr = io.StringIO()
            mock_sys.stdout = stream
            print_project_detail(conn, 9999, output_format="table", stream=stream)
            stderr_output = mock_sys.stderr.getvalue()
        assert "찾을 수 없습니다" in stderr_output
        conn.close()

    def test_table_with_data(self, db_with_detail: tuple[sqlite3.Connection, int]) -> None:
        """테이블 출력 시 프로젝트명 포함."""
        conn, project_id = db_with_detail
        stream = io.StringIO()
        with patch("tubearchive.commands.project.render_table"):
            print_project_detail(conn, project_id, output_format="table", stream=stream)
        output = stream.getvalue()
        assert "상세 테스트" in output

    def test_table_empty_project(self, tmp_path: Path) -> None:
        """영상 없는 프로젝트 → '영상 없음' 출력."""
        conn = init_database(tmp_path / "test.db")
        repo = ProjectRepository(conn)
        project_id = repo.create("빈 프로젝트")
        conn.commit()

        stream = io.StringIO()
        print_project_detail(conn, project_id, output_format="table", stream=stream)
        output = stream.getvalue()
        assert "영상 없음" in output
        conn.close()
