"""SQLite 데이터베이스 스키마."""

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
    status TEXT CHECK(status IN ('pending', 'processing', 'completed', 'failed')) DEFAULT 'pending',
    progress_percent INTEGER DEFAULT 0 CHECK(progress_percent >= 0 AND progress_percent <= 100),
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

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_transcoding_status ON transcoding_jobs(status);
CREATE INDEX IF NOT EXISTS idx_transcoding_video_id ON transcoding_jobs(video_id);
CREATE INDEX IF NOT EXISTS idx_videos_path ON videos(original_path);
"""


def init_database(db_path: Path | None = None) -> sqlite3.Connection:
    """
    데이터베이스 초기화.

    Args:
        db_path: 데이터베이스 파일 경로 (None이면 기본 경로 사용)

    Returns:
        SQLite 연결 객체
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH

    # 부모 디렉토리 생성 보장
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # 스키마 적용
    conn.executescript(SCHEMA)
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
        db_path = DEFAULT_DB_PATH

    # 부모 디렉토리 생성 보장
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    return conn
