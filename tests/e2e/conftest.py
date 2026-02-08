"""E2E 테스트 공통 fixture 및 헬퍼.

모든 E2E 테스트에서 공유하는 영상 생성, 분석 헬퍼와 fixture를 제공한다.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tubearchive.cli import ValidatedArgs
from tubearchive.database.schema import init_database

# ffmpeg 없으면 전체 E2E 모듈 스킵
pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg not installed",
)


# ---------- 영상/오디오 생성 헬퍼 ----------


def create_test_video(
    path: Path,
    *,
    duration: float = 3.0,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    codec: str = "h264",
    audio: bool = True,
) -> Path:
    """ffmpeg로 테스트용 영상을 생성한다.

    Args:
        path: 출력 파일 경로
        duration: 길이(초)
        width: 가로 해상도
        height: 세로 해상도
        fps: 프레임 레이트
        codec: 비디오 코덱
        audio: 오디오 포함 여부
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size={width}x{height}:rate={fps}",
    ]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}"]
        cmd += ["-c:a", "aac", "-b:a", "128k"]

    cmd += ["-c:v", codec, "-pix_fmt", "yuv420p", str(path)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
    return path


def create_silent_video(
    path: Path,
    *,
    duration: float = 3.0,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """완전 무음 오디오가 포함된 테스트 영상 생성.

    오디오 트랙은 존재하지만 무음(anullsrc)인 영상.
    loudnorm -inf 처리 등 극단값 테스트용.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:s={width}x{height}:d={duration}:r=30",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r=48000:cl=stereo:d={duration}",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-t",
        str(duration),
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
    return path


def create_no_audio_video(
    path: Path,
    *,
    duration: float = 3.0,
    width: int = 1920,
    height: int = 1080,
) -> Path:
    """오디오 트랙이 없는 테스트 영상 생성."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=red:s={width}x{height}:d={duration}:r=30",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
    return path


def create_bgm_audio(
    path: Path,
    *,
    duration: float = 5.0,
    frequency: int = 880,
) -> Path:
    """sine 톤 오디오 파일 생성 (BGM 테스트용).

    Args:
        path: 출력 MP3 파일 경로
        duration: 길이(초)
        frequency: 주파수(Hz)
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration={duration}:sample_rate=48000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "128k",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
    return path


def create_identity_lut(path: Path) -> Path:
    """Identity .cube LUT 파일 생성.

    모든 색상을 원래대로 매핑하는 identity LUT.
    LUT 파이프라인 동작 테스트용.
    """
    lines = [
        'TITLE "Identity LUT"',
        "LUT_3D_SIZE 2",
        "",
        "0.0 0.0 0.0",
        "1.0 0.0 0.0",
        "0.0 1.0 0.0",
        "1.0 1.0 0.0",
        "0.0 0.0 1.0",
        "1.0 0.0 1.0",
        "0.0 1.0 1.0",
        "1.0 1.0 1.0",
    ]
    path.write_text("\n".join(lines) + "\n")
    return path


# ---------- 분석 헬퍼 ----------


def probe_video(path: Path) -> dict[str, Any]:
    """ffprobe로 영상 메타데이터를 조회한다."""
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def get_video_duration(path: Path) -> float:
    """ffprobe로 영상/오디오 길이(초) 조회."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def get_audio_stream_count(path: Path) -> int:
    """ffprobe로 오디오 스트림 개수 조회."""
    info = probe_video(path)
    return sum(1 for s in info["streams"] if s["codec_type"] == "audio")


def has_vidstab_filter() -> bool:
    """ffmpeg에 vidstab 필터가 설치되어 있는지 확인."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "vidstab" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------- Fixtures ----------


@pytest.fixture
def e2e_video_dir(tmp_path: Path) -> Path:
    """E2E 테스트용 영상이 담긴 디렉토리."""
    video_dir = tmp_path / "2025-01-31_E2E-Test"
    video_dir.mkdir()
    return video_dir


@pytest.fixture
def e2e_output_dir(tmp_path: Path) -> Path:
    """E2E 출력 디렉토리."""
    out = tmp_path / "output"
    out.mkdir()
    return out


@pytest.fixture
def e2e_db(tmp_path: Path) -> Path:
    """E2E 전용 DB (테스트 간 격리)."""
    db_path = tmp_path / "e2e_test.db"
    conn = init_database(db_path)
    conn.close()
    return db_path


def make_pipeline_args(
    targets: list[Path],
    output: Path,
    *,
    db_path: Path | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
    **overrides: Any,
) -> ValidatedArgs:
    """ValidatedArgs 팩토리.

    기본값으로 최소 파이프라인 인자를 생성하고, overrides로 필드를 덮어쓴다.
    db_path와 monkeypatch가 주어지면 환경변수도 설정한다.
    """
    if db_path is not None and monkeypatch is not None:
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(db_path))

    defaults: dict[str, Any] = {
        "targets": targets,
        "output": output,
        "output_dir": None,
        "no_resume": False,
        "keep_temp": False,
        "dry_run": False,
    }
    defaults.update(overrides)
    return ValidatedArgs(**defaults)
