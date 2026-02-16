"""영상 파일 스캐너.

지정된 파일·디렉토리 목록에서 영상 파일을 재귀 탐색하고,
각 파일의 메타데이터(크기, 생성 시간)를 수집하여
:class:`~tubearchive.models.video.VideoFile` 리스트로 반환한다.

지원 확장자:
    ``VIDEO_EXTENSIONS`` 에 정의된 컨테이너
    (``.mp4``, ``.mov``, ``.mkv``, ``.avi`` 등)

생성 시간 감지:
    macOS ``st_birthtime`` → ``st_mtime`` → ``st_ctime`` 순으로 폴백
"""

import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Final

from tubearchive.models.video import VideoFile
from tubearchive.utils.validators import VIDEO_EXTENSIONS

logger = logging.getLogger(__name__)

REMOTE_MOUNT_PREFIXES: Final[tuple[Path, ...]] = (Path("/Volumes"), Path("/mnt"))
NETWORK_SPEED_WARNING_THRESHOLD_MBPS: Final[float] = 30.0
SPEED_CHECK_SAMPLE_BYTES: Final[int] = 8 * 1024 * 1024
SPEED_CHECK_CHUNK_BYTES: Final[int] = 1024 * 1024


def _get_remote_source_root(path: Path) -> Path | None:
    """
    네트워크/외장 스토리지 경로면 최상위 마운트 루트를 반환한다.

    - /Volumes/<name>/...
    - /mnt/<name>/...
    """
    resolved = path.resolve()
    for prefix in REMOTE_MOUNT_PREFIXES:
        try:
            relative = resolved.relative_to(prefix)
        except ValueError:
            continue
        if not relative.parts:
            return prefix
        return prefix / relative.parts[0]
    return None


def _check_remote_source(path: Path) -> None:
    """원격/외장 스토리지 경로의 접근성만 간단히 검증한다."""
    try:
        next(path.iterdir())
    except StopIteration:
        return
    except OSError as exc:
        raise FileNotFoundError(f"Cannot access source path: {path}") from exc


def _probe_file_for_speed(path: Path) -> Path | None:
    """속도 측정에 사용할 첫 번째 파일을 찾는다."""
    if path.is_file():
        return path
    if not path.is_dir():
        return None

    try:
        for candidate in path.rglob("*"):
            if candidate.is_file():
                return candidate
    except OSError as exc:
        logger.warning("Failed to locate probe file in %s: %s", path, exc)
    return None


def _measure_source_read_speed(path: Path) -> float | None:
    """
    지정 경로에서 샘플 파일을 읽어 대역폭(B/s) 계산.

    반환 불가 시 None.
    """
    probe = _probe_file_for_speed(path)
    if probe is None or not probe.is_file():
        logger.warning("Cannot find a file for speed check: %s", path)
        return None

    size = min(SPEED_CHECK_SAMPLE_BYTES, probe.stat().st_size)
    if size <= 0:
        return None

    start = time.perf_counter()
    read_total = 0
    try:
        with probe.open("rb") as fh:
            while read_total < size:
                chunk = fh.read(min(SPEED_CHECK_CHUNK_BYTES, size - read_total))
                if not chunk:
                    break
                read_total += len(chunk)
    except OSError as exc:
        logger.warning("Speed probe failed for %s: %s", probe, exc)
        return None

    elapsed = time.perf_counter() - start
    if read_total <= 0 or elapsed <= 0:
        return None

    return read_total / elapsed


def _warn_if_slow_network_source(path: Path) -> None:
    """읽기 속도 경고를 출력한다."""
    source_root = _get_remote_source_root(path)
    if source_root is None:
        return

    logger.info("원격/외장 스토리지 감지: %s", source_root)

    _check_remote_source(source_root)

    speed_bps = _measure_source_read_speed(source_root)
    if speed_bps is None:
        logger.warning("원격/외장 스토리지에서 속도 측정에 실패했습니다: %s", source_root)
        return

    speed_mbps = speed_bps / (1024 * 1024)
    if speed_mbps < NETWORK_SPEED_WARNING_THRESHOLD_MBPS:
        logger.warning(
            "원격/외장 경로 읽기 속도: %.1fMB/s (권장 임계값 %.1fMB/s). "
            "로컬 복사 후 처리하는 것을 권장합니다.",
            speed_mbps,
            NETWORK_SPEED_WARNING_THRESHOLD_MBPS,
        )


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
    validated_remote_roots: set[Path] = set()

    for target in targets:
        if not target.exists():
            raise FileNotFoundError(f"Target not found: {target}")

        remote_root = _get_remote_source_root(target)
        if remote_root is not None and remote_root not in validated_remote_roots:
            _warn_if_slow_network_source(remote_root)
            validated_remote_roots.add(remote_root)

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
