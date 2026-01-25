"""SQLite 데이터베이스 스키마."""

import sqlite3
from pathlib import Path

# 기본 데이터베이스 파일 경로
DEFAULT_DB_PATH = Path.cwd() / "tubearchive.db"

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

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    return conn
