"""영상 파일 스캐너."""

import sys
from datetime import datetime
from pathlib import Path

from tubearchive.models.video import VideoFile

# 지원하는 영상 확장자
VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mts",
    ".m4v",
    ".avi",
    ".mkv",
    ".wmv",
    ".flv",
    ".webm",
}


def scan_videos(targets: list[Path]) -> list[VideoFile]:
    """
    영상 파일 스캔.

    Args:
        targets: 스캔할 대상 (빈 리스트, 파일들, 디렉토리)

    Returns:
        생성 시간 순으로 정렬된 VideoFile 리스트

    Raises:
        FileNotFoundError: 존재하지 않는 파일/디렉토리
    """
    # Case 1: 빈 리스트 → 현재 디렉토리
    if not targets:
        targets = [Path.cwd()]

    # 모든 영상 파일 수집
    video_paths: set[Path] = set()

    for target in targets:
        if not target.exists():
            raise FileNotFoundError(f"Target not found: {target}")

        if target.is_file():
            # Case 2: 파일
            if _is_video_file(target):
                video_paths.add(target.resolve())
        elif target.is_dir():
            # Case 3: 디렉토리 (재귀 스캔)
            for video_path in _scan_directory(target):
                video_paths.add(video_path.resolve())

    # VideoFile 객체 생성
    videos = [_create_video_file(path) for path in video_paths]

    # 생성 시간 순 정렬
    videos.sort(key=lambda v: v.creation_time)

    return videos


def _is_video_file(path: Path) -> bool:
    """영상 파일 여부 확인."""
    return path.suffix.lower() in VIDEO_EXTENSIONS


def _scan_directory(directory: Path) -> list[Path]:
    """디렉토리 재귀 스캔."""
    video_files = []

    for path in directory.rglob("*"):
        if path.is_file() and _is_video_file(path):
            video_files.append(path)

    return video_files


def _create_video_file(path: Path) -> VideoFile:
    """VideoFile 객체 생성."""
    stat = path.stat()

    # 파일 생성 시간 추출
    creation_time = _get_creation_time(path)

    return VideoFile(
        path=path,
        creation_time=creation_time,
        size_bytes=stat.st_size,
    )


def _get_creation_time(path: Path) -> datetime:
    """
    파일 생성 시간 추출.

    macOS: st_birthtime 사용
    기타: st_ctime 폴백 (생성 시간이 아닌 메타데이터 변경 시간일 수 있음)
    """
    stat = path.stat()

    if sys.platform == "darwin":
        # macOS: st_birthtime (mypy recognizes sys.platform checks)
        timestamp = stat.st_birthtime
    else:
        # Linux/Windows: st_ctime 폴백 (메타데이터 변경 시간)
        timestamp = stat.st_ctime

    return datetime.fromtimestamp(timestamp)
