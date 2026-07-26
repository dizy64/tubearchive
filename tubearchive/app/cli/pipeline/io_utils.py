"""파이프라인 I/O 유틸리티 (사이드이펙트 없는 헬퍼).

경로/디스크/ffprobe 기반 미디어 조회 등 형제 모듈 의존이 없는 함수를 모은다.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from tubearchive.app.cli.context import (
    FileDoneEvent,
    FileProgressEvent,
    FileStartEvent,
    PipelineContext,
)
from tubearchive.domain.media.audio_sync import (
    AudioSyncError,
    probe_media_duration,
)
from tubearchive.domain.models.video import VideoFile

logger = logging.getLogger(__name__)

_FFPROBE_TIMEOUT_SECONDS = 30


def _emit_progress(
    context: PipelineContext | None,
    event: FileStartEvent | FileProgressEvent | FileDoneEvent,
) -> None:
    """on_progress 콜백을 안전하게 호출한다.

    콜백이 예외를 던져도 파이프라인이 중단되지 않도록 try/except로 감싼다.
    """
    if context is None or context.on_progress is None:
        return
    try:
        context.on_progress(event)
    except Exception:
        logger.debug("Progress callback raised an exception", exc_info=True)


def get_temp_dir() -> Path:
    """실행별 고유 임시 디렉토리 생성 및 반환.

    공유 디렉토리(/tmp/tubearchive/)를 사용하면 동시 실행 중
    한 쪽이 cleanup할 때 나머지의 임시 파일도 삭제되는 문제가 발생한다.
    원자적으로 생성한 서브디렉토리로 격리하여 각 실행이 독립적인
    트랜잭션을 갖도록 한다.
    """
    temp_base = Path("/tmp/tubearchive")  # noqa: S108
    temp_base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=temp_base))


def check_output_disk_space(output_dir: Path, required_bytes: int) -> bool:
    """
    출력 디렉토리 디스크 공간 확인.

    Args:
        output_dir: 출력 디렉토리
        required_bytes: 필요한 바이트 수

    Returns:
        공간이 충분하면 True
    """
    usage = shutil.disk_usage(output_dir)
    if usage.free < required_bytes:
        logger.warning(
            f"Insufficient disk space: {usage.free / (1024**3):.1f}GB available, "
            f"{required_bytes / (1024**3):.1f}GB required"
        )
        return False
    return True


def _to_video_file(path: Path) -> VideoFile:
    """템플릿 경로를 ``VideoFile`` 객체로 변환한다.

    ``scan_videos``와 동일한 생성 시간 계산 규칙을 사용한다.
    """
    stat = path.stat()
    if sys.platform == "darwin":
        creation_time = datetime.fromtimestamp(getattr(stat, "st_birthtime", stat.st_mtime))
    else:
        creation_time = datetime.fromtimestamp(stat.st_ctime)

    return VideoFile(path=path, creation_time=creation_time, size_bytes=stat.st_size)


def get_output_filename(targets: list[Path]) -> str:
    """
    입력 타겟에서 출력 파일명 생성.

    디렉토리명 또는 첫 번째 파일의 부모 디렉토리명을 사용.

    Args:
        targets: 입력 타겟 목록

    Returns:
        출력 파일명 (확장자 포함)
    """
    if not targets:
        return "output.mp4"

    first_target = targets[0]
    name = first_target.name if first_target.is_dir() else first_target.parent.name

    # 빈 이름이거나 현재 디렉토리면 기본값
    if not name or name == ".":
        name = "output"

    return f"{name}.mp4"


def _get_media_duration(media_path: Path) -> float:
    """ffprobe를 사용하여 미디어 파일의 길이를 초 단위로 반환한다.

    Args:
        media_path: 미디어 파일 경로

    Returns:
        길이 (초)

    Raises:
        RuntimeError: ffprobe 실행 실패 또는 길이 파싱 실패
    """
    try:
        return probe_media_duration(media_path)
    except AudioSyncError as e:
        raise RuntimeError(f"Failed to probe duration: {media_path} - {e}") from e


def _has_audio_stream(media_path: Path) -> bool:
    """ffprobe를 사용하여 미디어 파일에 오디오 스트림이 있는지 확인한다.

    Args:
        media_path: 미디어 파일 경로

    Returns:
        오디오 스트림 존재 여부
    """
    probe_result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_streams",
            "-select_streams",
            "a",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=_FFPROBE_TIMEOUT_SECONDS,
    )
    info = json.loads(probe_result.stdout)
    streams = info.get("streams", [])
    if not isinstance(streams, list):
        raise ValueError("ffprobe streams must be a list")
    return bool(streams)
