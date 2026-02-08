"""SQLite 데이터베이스 스키마 및 초기화.

테이블 4개로 구성된 스키마를 정의하고, DB 연결·초기화·마이그레이션을 담당한다.

테이블:
    - ``videos``: 원본 영상 파일 메타데이터 (경로, 생성 시간, 기기 모델 등)
    - ``transcoding_jobs``: 트랜스코딩 작업 상태 추적 (Resume 지원)
    - ``merge_jobs``: 병합 작업 이력 및 YouTube 업로드 상태
    - ``split_jobs``: 영상 분할 작업 이력
    - ``archive_history``: 원본 파일 아카이브(이동/삭제) 이력
    - ``projects``: 프로젝트 관리 (여러 날의 촬영을 하나의 단위로 묶음)
    - ``project_merge_jobs``: 프로젝트 ↔ merge_jobs 다대다 관계

DB 위치:
    ``TUBEARCHIVE_DB_PATH`` 환경변수 > ``~/.tubearchive/tubearchive.db``
"""

import os
import sqlite3
from pathlib import Path

# 환경 변수
ENV_DB_PATH = "TUBEARCHIVE_DB_PATH"


def get_default_db_path() -> Path:
    """
    기본 데이터베이스 경로 반환.

    우선순위:
    1. TUBEARCHIVE_DB_PATH 환경 변수 (파일 또는 디렉토리)
    2. ~/.tubearchive/tubearchive.db

    Returns:
        데이터베이스 파일 경로
    """
    env_path = os.environ.get(ENV_DB_PATH)
    if env_path:
        path = Path(env_path)
        # 디렉토리인 경우 파일명 자동 추가
        if path.is_dir():
            return path / "tubearchive.db"
        # 파일 확장자가 없거나 디렉토리처럼 보이면 파일명 추가
        if path.suffix == "" and not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            return path / "tubearchive.db"
        return path

    # 홈 디렉토리에 고정 위치 사용
    db_dir = Path.home() / ".tubearchive"
    db_dir.mkdir(exist_ok=True)
    return db_dir / "tubearchive.db"


# 기본 데이터베이스 파일 경로 (호환성 유지)
DEFAULT_DB_PATH = get_default_db_path()

# SQL 스키마 정의
SCHEMA = """
-- videos: 원본 영상 정보
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL UNIQUE,
    creation_time TEXT NOT NULL,
    duration_seconds REAL,
    device_model TEXT,
    is_portrait INTEGER DEFAULT 0,
    metadata_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- transcoding_jobs: Resume 상태 추적
CREATE TABLE IF NOT EXISTS transcoding_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL,
    temp_file_path TEXT,
    status TEXT DEFAULT 'pending'
        CHECK(status IN ('pending','processing','completed','failed','merged')),
    progress_percent INTEGER DEFAULT 0
        CHECK(progress_percent >= 0 AND progress_percent <= 100),
    started_at TEXT,
    completed_at TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
);

-- merge_jobs: 병합 작업 이력
CREATE TABLE IF NOT EXISTS merge_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    output_path TEXT NOT NULL,
    video_ids TEXT NOT NULL,
    status TEXT CHECK(status IN ('pending', 'processing', 'completed', 'failed')) DEFAULT 'pending',
    youtube_id TEXT,
    title TEXT,
    date TEXT,
    total_duration_seconds REAL,
    total_size_bytes INTEGER,
    clips_info_json TEXT,
    summary_markdown TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- split_jobs: 영상 분할 작업 이력
CREATE TABLE IF NOT EXISTS split_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merge_job_id INTEGER NOT NULL,
    split_criterion TEXT NOT NULL
        CHECK(split_criterion IN ('duration', 'size')),
    split_value TEXT NOT NULL,
    output_files TEXT NOT NULL,
    youtube_ids TEXT,
    error_message TEXT,
    status TEXT DEFAULT 'completed'
        CHECK(status IN ('pending', 'processing', 'completed', 'failed')),
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merge_job_id) REFERENCES merge_jobs(id) ON DELETE CASCADE
);

-- archive_history: 원본 파일 아카이브 이력
CREATE TABLE IF NOT EXISTS archive_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL,
    operation TEXT NOT NULL
        CHECK(operation IN ('move', 'delete')),
    original_path TEXT NOT NULL,
    destination_path TEXT,
    archived_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
);

-- projects: 프로젝트 관리 (여러 날의 촬영을 하나의 단위로 묶음)
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT,
    date_range_start TEXT,
    date_range_end TEXT,
    playlist_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- project_merge_jobs: 프로젝트 ↔ merge_jobs 다대다 관계
CREATE TABLE IF NOT EXISTS project_merge_jobs (
    project_id INTEGER NOT NULL,
    merge_job_id INTEGER NOT NULL,
    added_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, merge_job_id),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (merge_job_id) REFERENCES merge_jobs(id) ON DELETE CASCADE
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_transcoding_status ON transcoding_jobs(status);
CREATE INDEX IF NOT EXISTS idx_transcoding_video_id ON transcoding_jobs(video_id);
CREATE INDEX IF NOT EXISTS idx_videos_path ON videos(original_path);
CREATE INDEX IF NOT EXISTS idx_split_merge_job ON split_jobs(merge_job_id);
CREATE INDEX IF NOT EXISTS idx_archive_video_id ON archive_history(video_id);
CREATE INDEX IF NOT EXISTS idx_archive_operation ON archive_history(operation);
CREATE INDEX IF NOT EXISTS idx_project_merge_jobs_merge ON project_merge_jobs(merge_job_id);
CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name);
"""


def _migrate_add_merged_status(conn: sqlite3.Connection) -> None:
    """기존 DB의 transcoding_jobs CHECK 제약에 'merged' 상태 추가."""
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='transcoding_jobs'"
    )
    row = cursor.fetchone()
    if row is None or "'merged'" in row[0]:
        return

    conn.executescript("""
        CREATE TABLE transcoding_jobs_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id INTEGER NOT NULL,
            temp_file_path TEXT,
            status TEXT DEFAULT 'pending'
                CHECK(status IN ('pending','processing','completed','failed','merged')),
            progress_percent INTEGER DEFAULT 0
                CHECK(progress_percent >= 0 AND progress_percent <= 100),
            started_at TEXT,
            completed_at TEXT,
            error_message TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
        );
        INSERT INTO transcoding_jobs_new SELECT * FROM transcoding_jobs;
        DROP TABLE transcoding_jobs;
        ALTER TABLE transcoding_jobs_new RENAME TO transcoding_jobs;
        CREATE INDEX IF NOT EXISTS idx_transcoding_status ON transcoding_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_transcoding_video_id ON transcoding_jobs(video_id);
    """)


def _migrate_split_jobs_columns(conn: sqlite3.Connection) -> None:
    """기존 split_jobs 테이블에 youtube_ids, error_message 컬럼 추가."""
    cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='split_jobs'")
    row = cursor.fetchone()
    if row is None:
        return
    schema_sql = row[0]
    if "youtube_ids" not in schema_sql:
        conn.execute("ALTER TABLE split_jobs ADD COLUMN youtube_ids TEXT")
    if "error_message" not in schema_sql:
        conn.execute("ALTER TABLE split_jobs ADD COLUMN error_message TEXT")


def _migrate_add_projects_tables(conn: sqlite3.Connection) -> None:
    """기존 DB에 projects, project_merge_jobs 테이블 추가."""
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='projects'")
    if cursor.fetchone() is not None:
        return

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT,
            date_range_start TEXT,
            date_range_end TEXT,
            playlist_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS project_merge_jobs (
            project_id INTEGER NOT NULL,
            merge_job_id INTEGER NOT NULL,
            added_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (project_id, merge_job_id),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY (merge_job_id) REFERENCES merge_jobs(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_project_merge_jobs_merge
            ON project_merge_jobs(merge_job_id);
        CREATE INDEX IF NOT EXISTS idx_projects_name ON projects(name);
    """)


def init_database(db_path: Path | None = None) -> sqlite3.Connection:
    """
    데이터베이스 초기화.

    Args:
        db_path: 데이터베이스 파일 경로 (None이면 기본 경로 사용)

    Returns:
        SQLite 연결 객체
    """
    if db_path is None:
        db_path = get_default_db_path()

    # 부모 디렉토리 생성 보장
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # 스키마 적용
    conn.executescript(SCHEMA)

    # 기존 DB 마이그레이션
    _migrate_add_merged_status(conn)
    _migrate_split_jobs_columns(conn)
    _migrate_add_projects_tables(conn)

    conn.commit()

    return conn


def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    """
    데이터베이스 연결 획득.

    Args:
        db_path: 데이터베이스 파일 경로

    Returns:
        SQLite 연결 객체
    """
    if db_path is None:
        db_path = get_default_db_path()

    # 부모 디렉토리 생성 보장
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    return conn
