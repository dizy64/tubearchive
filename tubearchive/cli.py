"""TubeArchive CLI 진입점.

다양한 기기(Nikon, GoPro, DJI, iPhone)의 4K 영상을 HEVC 10-bit로
표준화·병합하는 파이프라인을 제공한다.

파이프라인 흐름::

    scan_videos → Transcoder.transcode_video → Merger.merge
    → save_merge_job_to_db → [프로젝트 연결] → [upload_to_youtube]

주요 서브커맨드:
    - 기본(인자 없음): 영상 스캔 → 트랜스코딩 → 병합
    - ``--project NAME``: 병합 결과를 프로젝트에 연결 (자동 생성)
    - ``--project-list`` / ``--project-detail ID``: 프로젝트 관리
    - ``--upload`` / ``--upload-only``: YouTube 업로드
    - ``--status`` / ``--catalog``: 작업 현황·메타데이터 조회
    - ``--setup-youtube`` / ``--youtube-auth``: 인증 관리
"""

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Generator, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, NamedTuple

from tubearchive import __version__

if TYPE_CHECKING:
    from tubearchive.notification.notifier import Notifier
from tubearchive.commands.catalog import (
    CATALOG_STATUS_SENTINEL,
    STATUS_ICONS,
    cmd_catalog,
    format_duration,
    normalize_status_filter,
)
from tubearchive.config import (
    ENV_FADE_DURATION,
    ENV_GROUP_SEQUENCES,
    ENV_OUTPUT_DIR,
    ENV_PARALLEL,
    ENV_YOUTUBE_PLAYLIST,
    get_default_auto_lut,
    get_default_bgm_loop,
    get_default_bgm_path,
    get_default_bgm_volume,
    get_default_denoise,
    get_default_denoise_level,
    get_default_fade_duration,
    get_default_group_sequences,
    get_default_normalize_audio,
    get_default_notify,
    get_default_output_dir,
    get_default_parallel,
    get_default_stabilize,
    get_default_stabilize_crop,
    get_default_stabilize_strength,
)
from tubearchive.core.detector import detect_metadata
from tubearchive.core.grouper import (
    FileSequenceGroup,
    compute_fade_map,
    group_sequences,
    reorder_with_groups,
)
from tubearchive.core.merger import Merger
from tubearchive.core.ordering import (
    SortKey,
    filter_videos,
    interactive_reorder,
    print_video_list,
    sort_videos,
)
from tubearchive.core.scanner import scan_videos
from tubearchive.core.splitter import probe_duration
from tubearchive.core.transcoder import Transcoder
from tubearchive.database.repository import (
    MergeJobRepository,
    SplitJobRepository,
    TranscodingJobRepository,
    VideoRepository,
)
from tubearchive.database.schema import init_database
from tubearchive.ffmpeg.effects import LUT_SUPPORTED_EXTENSIONS, SilenceSegment
from tubearchive.models.video import FadeConfig, VideoFile
from tubearchive.utils import truncate_path
from tubearchive.utils.progress import MultiProgressBar, ProgressInfo, format_size
from tubearchive.utils.summary_generator import generate_single_file_description

logger = logging.getLogger(__name__)

SUPPORTED_THUMBNAIL_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})


# NOTE: STATUS_ICONS, CATALOG_STATUS_SENTINEL, format_duration, normalize_status_filter 등
#       카탈로그/상태 관련 상수와 유틸리티는 tubearchive.commands.catalog에서 import합니다.


def safe_input(prompt: str) -> str:
    """
    터미널에서 안전하게 입력 받기.

    tmux 등 환경에서도 동작하도록 bash read 사용.

    Args:
        prompt: 입력 프롬프트

    Returns:
        사용자 입력 (strip 적용)
    """
    sys.stdout.write(prompt)
    sys.stdout.flush()

    try:
        # bash read 사용 (터미널 설정에 덜 민감)
        result = subprocess.run(
            ["bash", "-c", "read -r line </dev/tty && printf '%s' \"$line\""],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    # fallback: 기본 input
    try:
        return input().strip()
    except (EOFError, KeyboardInterrupt):
        return ""


# NOTE: 환경변수 상수(ENV_*)와 기본값 헬퍼(get_default_*)는
#       tubearchive.config 모듈에서 import합니다.


@contextmanager
def database_session() -> Generator[sqlite3.Connection]:
    """DB 연결을 자동으로 닫아주는 context manager.

    ``init_database()`` 로 연결을 열고, 블록이 끝나면 (예외 발생 포함)
    자동으로 ``conn.close()`` 를 호출한다.

    Yields:
        sqlite3.Connection: 초기화된 DB 연결
    """
    conn = init_database()
    try:
        yield conn
    finally:
        conn.close()


class ClipInfo(NamedTuple):
    """영상 클립 메타데이터 (Summary·타임라인용).

    ``_collect_clip_info`` 의 반환값으로, 기존 ``tuple[str, float, str|None, str|None]``
    을 대체하여 필드 의미를 명확히 한다. NamedTuple이므로 기존 tuple 언패킹과
    역호환된다.

    Attributes:
        name: 파일명 (예: ``GH010042.MP4``)
        duration: 재생시간 (초)
        device: 촬영 기기명 (예: ``Nikon Z6III``, ``GoPro HERO12``)
        shot_time: 촬영 시각 문자열 (``HH:MM:SS``, None이면 알 수 없음)
    """

    name: str
    duration: float
    device: str | None
    shot_time: str | None


# YYYYMMDD 패턴 (파일명 시작 부분)
DATE_PATTERN = re.compile(r"^(\d{4})(\d{2})(\d{2})\s*(.*)$")


def format_youtube_title(title: str) -> str:
    """
    YouTube 제목 포맷팅.

    YYYYMMDD 형식의 날짜를 'YYYY년 M월 D일'로 변환합니다.
    예: '20240115 도쿄 여행' → '2024년 1월 15일 도쿄 여행'

    Args:
        title: 원본 제목

    Returns:
        포맷팅된 제목
    """
    match = DATE_PATTERN.match(title)
    if match:
        year, month, day, rest = match.groups()
        # 앞의 0 제거 (01 → 1)
        month_int = int(month)
        day_int = int(day)
        formatted = f"{year}년 {month_int}월 {day_int}일"
        if rest:
            formatted += f" {rest}"
        return formatted
    return title


def get_temp_dir() -> Path:
    """시스템 임시 디렉토리 내 tubearchive 폴더 반환."""
    temp_base = Path(tempfile.gettempdir()) / "tubearchive"
    temp_base.mkdir(exist_ok=True)
    return temp_base


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


@dataclass
class ValidatedArgs:
    """검증된 CLI 인자.

    ``argparse.Namespace`` 를 타입 안전하게 변환한 데이터클래스.
    :func:`validate_args` 에서 생성된다.
    """

    targets: list[Path]
    output: Path | None
    output_dir: Path | None
    no_resume: bool
    keep_temp: bool
    dry_run: bool
    denoise: bool = False
    denoise_level: str = "medium"
    normalize_audio: bool = False
    group_sequences: bool = True
    fade_duration: float = 0.5
    upload: bool = False
    parallel: int = 1
    thumbnail: bool = False
    thumbnail_timestamps: list[str] | None = None
    thumbnail_quality: int = 2
    set_thumbnail: Path | None = None
    generated_thumbnail_paths: list[Path] | None = None
    detect_silence: bool = False
    trim_silence: bool = False
    silence_threshold: str = "-30dB"
    silence_min_duration: float = 2.0
    bgm_path: Path | None = None
    bgm_volume: float = 0.2
    bgm_loop: bool = False
    exclude_patterns: list[str] | None = None
    include_only_patterns: list[str] | None = None
    sort_key: str = "time"
    reorder: bool = False
    split_duration: str | None = None
    split_size: str | None = None
    archive_originals: Path | None = None
    archive_force: bool = False
    timelapse_speed: int | None = None
    timelapse_audio: bool = False
    timelapse_resolution: str | None = None
    stabilize: bool = False
    stabilize_strength: str = "medium"
    stabilize_crop: str = "crop"
    project: str | None = None
    lut_path: Path | None = None
    auto_lut: bool = False
    lut_before_hdr: bool = False
    device_luts: dict[str, str] | None = None
    notify: bool = False
    schedule: str | None = None
    quality_report: bool = False


@dataclass(frozen=True)
class TranscodeOptions:
    """트랜스코딩 공통 옵션.

    ``_transcode_single``, ``_transcode_parallel``, ``_transcode_sequential``
    에서 공유하는 오디오·페이드 설정을 묶는다.

    Attributes:
        denoise: 오디오 노이즈 제거 여부 (afftdn)
        denoise_level: 노이즈 제거 강도 (``light`` | ``medium`` | ``heavy``)
        normalize_audio: EBU R128 loudnorm 2-pass 적용 여부
        fade_map: 파일별 페이드 설정 맵 (그룹 경계 기반)
        fade_duration: 기본 페이드 시간 (초)
        trim_silence: 무음 구간 제거 여부
        silence_threshold: 무음 기준 데시벨
        silence_min_duration: 최소 무음 길이 (초)
        lut_path: LUT 파일 경로 (직접 지정, auto_lut보다 우선)
        auto_lut: 기기 모델 기반 자동 LUT 매칭 활성화
        lut_before_hdr: LUT를 HDR→SDR 변환 전에 적용
        device_luts: 기기 키워드 → LUT 파일 경로 매핑
    """

    denoise: bool = False
    denoise_level: str = "medium"
    normalize_audio: bool = False
    fade_map: dict[Path, FadeConfig] | None = None
    fade_duration: float = 0.5
    trim_silence: bool = False
    silence_threshold: str = "-30dB"
    silence_min_duration: float = 2.0
    stabilize: bool = False
    stabilize_strength: str = "medium"
    stabilize_crop: str = "crop"
    lut_path: Path | None = None
    auto_lut: bool = False
    lut_before_hdr: bool = False
    device_luts: dict[str, str] | None = None


@dataclass(frozen=True)
class TranscodeResult:
    """단일 트랜스코딩 결과.

    Attributes:
        output_path: 트랜스코딩된 임시 파일 경로
        video_id: DB ``videos`` 테이블 ID
        clip_info: 클립 메타데이터 (파일명, 길이, 기기명, 촬영시각)
        silence_segments: 무음 구간 리스트 (trim_silence 활성화 시)
    """

    output_path: Path
    video_id: int
    clip_info: ClipInfo
    silence_segments: list[SilenceSegment] | None = None


def create_parser() -> argparse.ArgumentParser:
    """
    CLI 파서 생성.

    Returns:
        argparse.ArgumentParser 인스턴스
    """
    parser = argparse.ArgumentParser(
        prog="tubearchive",
        description=f"다양한 기기의 4K 영상을 표준화하여 병합합니다. (v{__version__})",
        epilog=(
            "예시:\n"
            "  tubearchive video1.mp4 video2.mov -o merged.mp4  # 병합\n"
            "  tubearchive ~/Videos/ --upload                   # 병합 후 업로드\n"
            "  tubearchive --upload-only merged.mp4             # 업로드만"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    parser.add_argument(
        "targets",
        nargs="*",
        default=[],
        help="영상 파일 또는 디렉토리 (기본: 현재 디렉토리)",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="출력 파일 경로 (기본: merged_output.mp4)",
    )

    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Resume 기능 비활성화",
    )

    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="임시 파일 보존 (디버깅용)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실행 계획만 출력 (실제 실행 안 함)",
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="상세 로그 출력",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help=f"출력 파일 저장 디렉토리 (환경변수: {ENV_OUTPUT_DIR})",
    )

    # YouTube 업로드 옵션
    parser.add_argument(
        "--upload",
        action="store_true",
        help="병합 완료 후 YouTube에 업로드",
    )

    parser.add_argument(
        "--upload-only",
        type=str,
        metavar="FILE",
        default=None,
        help="지정된 파일을 YouTube에 업로드 (병합 없이)",
    )

    parser.add_argument(
        "--upload-title",
        type=str,
        default=None,
        help="YouTube 업로드 시 영상 제목 (기본: 파일명)",
    )

    parser.add_argument(
        "--upload-privacy",
        type=str,
        default=None,
        choices=["public", "unlisted", "private"],
        help="YouTube 공개 설정 (기본: unlisted)",
    )

    parser.add_argument(
        "--schedule",
        type=str,
        default=None,
        metavar="DATETIME",
        help=(
            "YouTube 예약 공개 시간 (ISO 8601 형식, "
            "예: 2026-02-01T18:00 또는 2026-02-01T18:00:00+09:00)"
        ),
    )

    parser.add_argument(
        "--playlist",
        type=str,
        action="append",
        default=None,
        metavar="ID",
        help=(f"업로드 후 플레이리스트에 추가 (환경변수: {ENV_YOUTUBE_PLAYLIST}, 쉼표로 구분)"),
    )

    parser.add_argument(
        "--upload-chunk",
        type=int,
        default=None,
        metavar="MB",
        help="업로드 청크 크기 MB (1-256, 환경변수: TUBEARCHIVE_UPLOAD_CHUNK_MB, 기본: 32)",
    )

    parser.add_argument(
        "--setup-youtube",
        action="store_true",
        help="YouTube 인증 상태 확인 및 설정 가이드 출력",
    )

    parser.add_argument(
        "--youtube-auth",
        action="store_true",
        help="YouTube 브라우저 인증 실행",
    )

    parser.add_argument(
        "--list-playlists",
        action="store_true",
        help="내 플레이리스트 목록 조회",
    )

    parser.add_argument(
        "--parallel",
        "-j",
        type=int,
        default=None,
        metavar="N",
        help=f"병렬 트랜스코딩 수 (환경변수: {ENV_PARALLEL}, 기본: 1)",
    )

    parser.add_argument(
        "--denoise",
        action="store_true",
        help="FFmpeg 오디오 노이즈 제거 활성화 (afftdn)",
    )

    parser.add_argument(
        "--denoise-level",
        type=str,
        choices=["light", "medium", "heavy"],
        default=None,
        help="노이즈 제거 강도 (light/medium/heavy, 기본: medium)",
    )

    parser.add_argument(
        "--normalize-audio",
        action="store_true",
        help="EBU R128 오디오 라우드니스 정규화 활성화 (loudnorm 2-pass)",
    )

    parser.add_argument(
        "--bgm",
        type=str,
        default=None,
        metavar="PATH",
        help="배경음악 파일 경로 (MP3, AAC, WAV 등)",
    )

    parser.add_argument(
        "--bgm-volume",
        type=float,
        default=None,
        metavar="0.0-1.0",
        help="배경음악 상대 볼륨 (0.0~1.0, 기본: 0.2)",
    )

    parser.add_argument(
        "--bgm-loop",
        action="store_true",
        help="BGM 길이 < 영상 길이일 때 루프 재생",
    )

    parser.add_argument(
        "--detect-silence",
        action="store_true",
        help="무음 구간 감지 및 목록 출력 (제거하지 않음)",
    )

    parser.add_argument(
        "--trim-silence",
        action="store_true",
        help="시작/끝 무음 자동 제거",
    )

    parser.add_argument(
        "--silence-threshold",
        type=str,
        default="-30dB",
        metavar="DB",
        help="무음 기준 dB (기본: -30dB)",
    )

    parser.add_argument(
        "--silence-duration",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="최소 무음 길이(초, 기본: 2.0)",
    )

    parser.add_argument(
        "--stabilize",
        action="store_true",
        help="영상 안정화 활성화 (vidstab 2-pass, 트랜스코딩 시간 증가)",
    )

    parser.add_argument(
        "--stabilize-strength",
        type=str,
        choices=["light", "medium", "heavy"],
        default=None,
        help="영상 안정화 강도 (light/medium/heavy, 기본: medium)",
    )

    parser.add_argument(
        "--stabilize-crop",
        type=str,
        choices=["crop", "expand"],
        default=None,
        help="안정화 후 프레임 처리 (crop: 잘라냄, expand: 검은색 채움, 기본: crop)",
    )

    group_toggle = parser.add_mutually_exclusive_group()
    group_toggle.add_argument(
        "--group",
        action="store_true",
        help=f"연속 파일 시퀀스 그룹핑 활성화 (환경변수: {ENV_GROUP_SEQUENCES})",
    )
    group_toggle.add_argument(
        "--no-group",
        action="store_true",
        help="연속 파일 시퀀스 그룹핑 비활성화",
    )

    parser.add_argument(
        "--fade-duration",
        type=float,
        default=None,
        metavar="SECONDS",
        help=f"기본 페이드 시간(초) 설정 (환경변수: {ENV_FADE_DURATION}, 기본: 0.5)",
    )

    parser.add_argument(
        "--exclude",
        type=str,
        action="append",
        default=None,
        metavar="PATTERN",
        help="제외할 파일명 패턴 (글로브, 반복 가능, 예: 'GH*' '*.mts')",
    )

    parser.add_argument(
        "--include-only",
        type=str,
        action="append",
        default=None,
        metavar="PATTERN",
        help="포함할 파일명 패턴만 선택 (글로브, 반복 가능, 예: '*.mp4')",
    )

    parser.add_argument(
        "--sort",
        type=str,
        default=None,
        choices=[k.value for k in SortKey],
        help="정렬 기준 변경 (기본: time, 옵션: name/size/device)",
    )

    parser.add_argument(
        "--reorder",
        action="store_true",
        help="인터랙티브 모드로 클립 순서 수동 편집",
    )

    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="PATH",
        help="설정 파일 경로 (기본: ~/.tubearchive/config.toml)",
    )

    parser.add_argument(
        "--init-config",
        action="store_true",
        help="기본 설정 파일(config.toml) 생성",
    )

    parser.add_argument(
        "--reset-build",
        type=str,
        nargs="?",
        const="",
        metavar="PATH",
        help="트랜스코딩/병합 기록 초기화 (다시 빌드, 경로 지정 또는 목록에서 선택)",
    )

    parser.add_argument(
        "--reset-upload",
        type=str,
        nargs="?",
        const="",
        metavar="PATH",
        help="YouTube 업로드 기록 초기화 (다시 업로드, 경로 지정 또는 목록에서 선택)",
    )

    # 썸네일 옵션
    parser.add_argument(
        "--thumbnail",
        action="store_true",
        help="병합 영상에서 썸네일 자동 생성 (기본: 10%%, 33%%, 50%% 지점)",
    )

    parser.add_argument(
        "--thumbnail-at",
        type=str,
        action="append",
        default=None,
        metavar="TIMESTAMP",
        help="특정 시점에서 썸네일 추출 (예: '00:01:30', 반복 가능)",
    )

    parser.add_argument(
        "--thumbnail-quality",
        type=int,
        default=2,
        metavar="Q",
        help="썸네일 JPEG 품질 (1-31, 낮을수록 고품질, 기본: 2)",
    )

    parser.add_argument(
        "--set-thumbnail",
        type=str,
        default=None,
        metavar="PATH",
        help="YouTube 업로드 시 사용할 썸네일 이미지 경로 (JPG/PNG)",
    )

    parser.add_argument(
        "--quality-report",
        action="store_true",
        help="트랜스코딩 결과 SSIM/PSNR/VMAF 지표 출력 (가능한 필터만 계산)",
    )

    # 영상 분할 옵션
    parser.add_argument(
        "--split-duration",
        type=str,
        default=None,
        metavar="DURATION",
        help="시간 기준 분할 (예: 1h, 30m, 1h30m), YouTube 12시간 제한 대응",
    )

    parser.add_argument(
        "--split-size",
        type=str,
        default=None,
        metavar="SIZE",
        help="파일 크기 기준 분할 (예: 10G, 256M), YouTube 256GB 제한 대응",
    )

    # 타임랩스 옵션
    parser.add_argument(
        "--timelapse",
        type=str,
        default=None,
        metavar="SPEED",
        help="타임랩스 배속 (예: 10x, 범위: 2x-60x)",
    )

    parser.add_argument(
        "--timelapse-audio",
        action="store_true",
        help="타임랩스에서 오디오 가속 (기본: 오디오 제거)",
    )

    parser.add_argument(
        "--timelapse-resolution",
        type=str,
        default=None,
        metavar="RES",
        help="타임랩스 출력 해상도 (예: 1080p, 4k, 1920x1080, 기본: 원본 유지)",
    )

    # LUT 컬러 그레이딩 옵션
    parser.add_argument(
        "--lut",
        type=str,
        default=None,
        metavar="PATH",
        help="LUT 파일 경로 (.cube, .3dl) — 트랜스코딩 시 lut3d 필터 적용",
    )

    # default=None으로 "CLI에서 명시하지 않음"과 "명시적 True"를 구분.
    # None이면 환경변수/config 기본값(get_default_auto_lut())으로 결정.
    parser.add_argument(
        "--auto-lut",
        action="store_true",
        default=None,
        help="기기 모델 기반 자동 LUT 매칭 (config.toml [color_grading.device_luts] 참조)",
    )

    parser.add_argument(
        "--no-auto-lut",
        action="store_true",
        help="자동 LUT 매칭 비활성화 (환경변수/config 설정 무시)",
    )

    parser.add_argument(
        "--lut-before-hdr",
        action="store_true",
        help="LUT를 HDR→SDR 변환 전에 적용 (기본: HDR 변환 후 적용)",
    )

    parser.add_argument(
        "--status",
        nargs="?",
        const=CATALOG_STATUS_SENTINEL,
        default=None,
        metavar="STATUS",
        help=(
            "작업 현황 조회 (값 지정 시 메타데이터 검색 상태 필터로 사용: "
            "pending/processing/completed/failed/merged/untracked)"
        ),
    )

    parser.add_argument(
        "--status-detail",
        type=int,
        metavar="ID",
        default=None,
        help="특정 작업 상세 조회 (merge_job ID)",
    )

    parser.add_argument(
        "--stats",
        action="store_true",
        help="전체 처리 통계 대시보드 조회",
    )

    parser.add_argument(
        "--period",
        type=str,
        default=None,
        metavar="PERIOD",
        help="통계 기간 필터 (예: 2026-01, 2026). --stats와 함께 사용",
    )

    parser.add_argument(
        "--catalog",
        action="store_true",
        help="영상 메타데이터 전체 목록 조회 (기기별 그룹핑)",
    )

    parser.add_argument(
        "--search",
        nargs="?",
        const="",
        default=None,
        metavar="PATTERN",
        help="영상 메타데이터 검색 (예: 2026-01)",
    )

    parser.add_argument(
        "--device",
        type=str,
        default=None,
        metavar="NAME",
        help="메타데이터 검색 시 기기 필터 (예: GoPro)",
    )

    # 프로젝트 옵션
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        metavar="NAME",
        help='프로젝트에 병합 결과 연결 (없으면 자동 생성, 예: "제주도 여행")',
    )

    parser.add_argument(
        "--project-list",
        action="store_true",
        help="프로젝트 목록 조회 (--json 옵션으로 JSON 출력)",
    )

    parser.add_argument(
        "--project-detail",
        type=int,
        default=None,
        metavar="ID",
        help="프로젝트 상세 조회 (프로젝트 ID, --json 옵션으로 JSON 출력)",
    )

    # 아카이브 옵션
    parser.add_argument(
        "--archive-originals",
        type=str,
        default=None,
        metavar="PATH",
        help="트랜스코딩 완료 후 원본 파일을 지정 경로로 이동",
    )

    parser.add_argument(
        "--archive-force",
        action="store_true",
        help="원본 파일 삭제(delete 정책) 시 확인 프롬프트 우회",
    )

    # 알림 옵션
    parser.add_argument(
        "--notify",
        action="store_true",
        help="파이프라인 완료/에러 시 알림 전송 (config.toml [notification] 설정 필요)",
    )
    parser.add_argument(
        "--notify-test",
        action="store_true",
        help="설정된 알림 채널에 테스트 알림 전송 후 종료",
    )

    output_format_group = parser.add_mutually_exclusive_group()
    output_format_group.add_argument(
        "--json",
        action="store_true",
        help="메타데이터 출력 형식을 JSON으로 지정",
    )
    output_format_group.add_argument(
        "--csv",
        action="store_true",
        help="메타데이터 출력 형식을 CSV로 지정",
    )

    return parser


def parse_schedule_datetime(schedule_str: str) -> str:
    """ISO 8601 형식의 날짜/시간 문자열을 파싱하고 검증한다.

    공백 구분 형식(``2026-02-01 18:00``)은 자동으로 T로 변환된다.
    타임존이 없으면 로컬 타임존이 자동으로 추가된다.

    Args:
        schedule_str: ISO 8601 형식 날짜/시간 문자열

    Returns:
        YouTube API가 요구하는 RFC 3339 형식 문자열

    Raises:
        ValueError: 형식이 잘못되었거나 과거 시간일 때
    """
    # 공백 구분 형식을 T 구분으로 변환 (예: "2026-02-01 18:00" → "2026-02-01T18:00")
    normalized = schedule_str.strip()
    if " " in normalized and "T" not in normalized:
        normalized = normalized.replace(" ", "T", 1)

    try:
        # Python 3.11+는 fromisoformat이 대부분 ISO 8601 형식 지원
        parsed_dt = datetime.fromisoformat(normalized)
    except ValueError as e:
        raise ValueError(
            f"Invalid datetime format: {schedule_str}. "
            "Expected ISO 8601 format (e.g., 2026-02-01T18:00, "
            "2026-02-01 18:00, or 2026-02-01T18:00:00+09:00)"
        ) from e

    # 타임존 없으면 로컬 타임존 자동 추가
    if parsed_dt.tzinfo is None:
        try:
            # 시스템 로컬 타임존 가져오기
            local_tz = datetime.now().astimezone().tzinfo
            if local_tz is not None:
                parsed_dt = parsed_dt.replace(tzinfo=local_tz)
                tz_name = local_tz.tzname(parsed_dt) or "local"
                logger.info(f"Local timezone automatically added: {tz_name}")
        except Exception:
            # 타임존 가져오기 실패 시 경고만 출력
            logger.warning(
                "Could not determine local timezone. "
                "YouTube will interpret the time as UTC. "
                "Consider specifying timezone explicitly (e.g., +09:00)."
            )

    # 과거 시간 검증
    now = datetime.now(parsed_dt.tzinfo)
    if parsed_dt < now:
        # 얼마나 과거인지 계산
        time_diff = now - parsed_dt
        hours_ago = time_diff.total_seconds() / 3600

        if hours_ago < 1:
            time_desc = f"{int(time_diff.total_seconds() / 60)}분 전"
        elif hours_ago < 24:
            time_desc = f"{int(hours_ago)}시간 전"
        else:
            time_desc = f"{int(hours_ago / 24)}일 전"

        raise ValueError(
            f"Schedule time must be in the future. "
            f"Specified time is {time_desc}. "
            f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

    # YouTube API는 RFC 3339 형식 요구 (ISO 8601의 엄격한 서브셋)
    # isoformat()이 RFC 3339 호환 형식 반환
    return parsed_dt.isoformat()


def _resolve_set_thumbnail_path(
    set_thumbnail_arg: str | Path | None,
) -> Path | None:
    """`--set-thumbnail` 입력값을 정규화하고 검증한다.

    - 경로 확장: `~` 전개 + `resolve()`
    - 존재 여부 검증
    - 포맷 검증 (`.jpg`, `.jpeg`, `.png`)

    Args:
        set_thumbnail_arg: CLI 입력값(`--set-thumbnail`)

    Returns:
        검증된 Path 또는 미지정 시 None
    """
    if not set_thumbnail_arg:
        return None

    set_thumbnail = Path(set_thumbnail_arg).expanduser().resolve()
    if not set_thumbnail.is_file():
        raise FileNotFoundError(f"Thumbnail file not found: {set_thumbnail_arg}")

    if set_thumbnail.suffix.lower() not in SUPPORTED_THUMBNAIL_EXTENSIONS:
        raise ValueError(
            f"Unsupported thumbnail format: {set_thumbnail.suffix} (supported: .jpg, .jpeg, .png)"
        )

    return set_thumbnail


def validate_args(
    args: argparse.Namespace,
    device_luts: dict[str, str] | None = None,
) -> ValidatedArgs:
    """CLI 인자를 검증하고 :class:`ValidatedArgs` 로 변환한다.

    각 설정의 우선순위: **CLI 옵션 > 환경변수 > config.toml > 기본값**.
    ``get_default_*()`` 헬퍼가 환경변수·config.toml을 이미 반영하므로,
    여기서는 CLI 인자가 명시되었는지만 확인한다.

    Args:
        args: ``argparse`` 파싱 결과

    Returns:
        타입-안전하게 검증된 인자 데이터클래스

    Raises:
        FileNotFoundError: 대상 파일/디렉토리가 없을 때
        ValueError: fade_duration < 0 또는 thumbnail_quality 범위 초과
    """
    # targets 검증
    targets: list[Path] = []
    if not args.targets:
        targets = [Path.cwd()]
    else:
        for target in args.targets:
            path = Path(target)
            if not path.exists():
                raise FileNotFoundError(f"Target not found: {target}")
            targets.append(path)

    # output 검증
    output: Path | None = None
    if args.output:
        output = Path(args.output)
        if not output.parent.exists():
            raise FileNotFoundError(f"Output directory not found: {output.parent}")

    # output_dir 검증 (CLI 인자 > 환경 변수 > None)
    output_dir: Path | None = None
    if args.output_dir:
        output_dir = Path(args.output_dir)
        if not output_dir.is_dir():
            raise FileNotFoundError(f"Output directory not found: {args.output_dir}")
    else:
        output_dir = get_default_output_dir()

    # upload 플래그 확인
    upload = getattr(args, "upload", False)

    # parallel 값 결정 (CLI 인자 > 환경 변수 > 기본값)
    parallel = args.parallel if args.parallel is not None else get_default_parallel()
    if parallel < 1:
        parallel = 1

    # denoise 설정 (CLI 인자 > 환경 변수 > 기본값)
    denoise_flag = bool(getattr(args, "denoise", False))
    denoise_level = getattr(args, "denoise_level", None)
    env_denoise = get_default_denoise()
    env_denoise_level = get_default_denoise_level()
    if denoise_level is not None:
        denoise_flag = True
    resolved_denoise_level = denoise_level or env_denoise_level or "medium"
    if env_denoise_level is not None or env_denoise:
        denoise_flag = True

    # normalize_audio 설정 (CLI 인자 > 환경 변수 > 기본값)
    normalize_audio = bool(getattr(args, "normalize_audio", False)) or get_default_normalize_audio()

    # 그룹핑 설정 (CLI 인자 > 환경 변수 > 기본값)
    group_flag = bool(getattr(args, "group", False))
    no_group_flag = bool(getattr(args, "no_group", False))
    if group_flag:
        group_sequences = True
    elif no_group_flag:
        group_sequences = False
    else:
        group_sequences = get_default_group_sequences()

    # fade_duration 설정 (CLI 인자 > 환경 변수 > 기본값)
    fade_duration_arg = getattr(args, "fade_duration", None)
    fade_duration = (
        fade_duration_arg if fade_duration_arg is not None else get_default_fade_duration()
    )
    if fade_duration < 0:
        raise ValueError(f"Fade duration must be >= 0, got: {fade_duration}")

    # 썸네일 옵션 검증
    thumbnail = getattr(args, "thumbnail", False)
    thumbnail_at: list[str] | None = getattr(args, "thumbnail_at", None)
    thumbnail_quality: int = getattr(args, "thumbnail_quality", 2)
    set_thumbnail_arg = getattr(args, "set_thumbnail", None)
    set_thumbnail = _resolve_set_thumbnail_path(set_thumbnail_arg)

    # --thumbnail-at만 지정해도 암묵적 활성화
    if thumbnail_at and not thumbnail:
        thumbnail = True

    # quality 범위 검증
    if not 1 <= thumbnail_quality <= 31:
        raise ValueError(f"Thumbnail quality must be 1-31, got: {thumbnail_quality}")

    # 화질 리포트 옵션
    quality_report = bool(getattr(args, "quality_report", False))

    # 무음 관련 옵션
    detect_silence = getattr(args, "detect_silence", False)
    trim_silence = getattr(args, "trim_silence", False)
    silence_threshold = getattr(args, "silence_threshold", "-30dB")
    silence_min_duration = getattr(args, "silence_duration", 2.0)

    # silence_min_duration 범위 검증
    if silence_min_duration <= 0:
        raise ValueError(f"Silence duration must be > 0, got: {silence_min_duration}")

    # BGM 옵션 검증 (CLI 인자 > 환경 변수 > 기본값)
    bgm_path_arg = getattr(args, "bgm", None)
    bgm_path: Path | None = None
    if bgm_path_arg:
        bgm_path = Path(bgm_path_arg).expanduser()
        if not bgm_path.is_file():
            raise FileNotFoundError(f"BGM file not found: {bgm_path_arg}")
    else:
        # 환경변수/설정파일에서 기본값
        bgm_path = get_default_bgm_path()

    bgm_volume_arg = getattr(args, "bgm_volume", None)
    if bgm_volume_arg is not None:
        if not (0.0 <= bgm_volume_arg <= 1.0):
            raise ValueError(f"BGM volume must be in range [0.0, 1.0], got: {bgm_volume_arg}")
        bgm_volume = bgm_volume_arg
    else:
        # 환경변수/설정파일에서 기본값, 없으면 0.2
        env_bgm_volume = get_default_bgm_volume()
        bgm_volume = env_bgm_volume if env_bgm_volume is not None else 0.2

    bgm_loop_arg = getattr(args, "bgm_loop", False)
    bgm_loop = bgm_loop_arg or get_default_bgm_loop()

    # stabilize 설정 (CLI 인자 > 환경 변수 > 기본값)
    # --stabilize-strength 또는 --stabilize-crop 지정 시 암묵적으로 활성화
    stabilize_strength_arg: str | None = getattr(args, "stabilize_strength", None)
    stabilize_crop_arg: str | None = getattr(args, "stabilize_crop", None)
    env_stabilize = get_default_stabilize()
    env_stabilize_strength = get_default_stabilize_strength()
    env_stabilize_crop = get_default_stabilize_crop()
    stabilize_flag = (
        bool(getattr(args, "stabilize", False))
        or stabilize_strength_arg is not None
        or stabilize_crop_arg is not None
        or env_stabilize
    )
    resolved_stabilize_strength = stabilize_strength_arg or env_stabilize_strength or "medium"
    resolved_stabilize_crop = stabilize_crop_arg or env_stabilize_crop or "crop"

    exclude_patterns: list[str] | None = getattr(args, "exclude", None)
    include_only_patterns: list[str] | None = getattr(args, "include_only", None)
    sort_key_str: str = getattr(args, "sort", None) or "time"
    reorder_flag: bool = getattr(args, "reorder", False)

    # 영상 분할 옵션
    split_duration: str | None = getattr(args, "split_duration", None)
    split_size: str | None = getattr(args, "split_size", None)

    # 아카이브 옵션
    archive_originals_arg = getattr(args, "archive_originals", None)
    archive_originals: Path | None = None
    if archive_originals_arg:
        archive_originals = Path(archive_originals_arg).expanduser().resolve()
        # 디렉토리가 존재하지 않으면 생성 예정이므로 검증 생략

    archive_force_flag: bool = getattr(args, "archive_force", False)

    # 타임랩스 옵션 검증
    timelapse_speed: int | None = None
    if hasattr(args, "timelapse") and args.timelapse:
        timelapse_str = args.timelapse.lower().rstrip("x")
        try:
            timelapse_speed = int(timelapse_str)
        except ValueError:
            raise ValueError(f"Invalid timelapse speed format: {args.timelapse}") from None

        from tubearchive.ffmpeg.effects import TIMELAPSE_MAX_SPEED, TIMELAPSE_MIN_SPEED

        if timelapse_speed < TIMELAPSE_MIN_SPEED or timelapse_speed > TIMELAPSE_MAX_SPEED:
            raise ValueError(
                f"Timelapse speed must be between {TIMELAPSE_MIN_SPEED}x and "
                f"{TIMELAPSE_MAX_SPEED}x, got {timelapse_speed}x"
            )

    timelapse_audio: bool = getattr(args, "timelapse_audio", False)
    timelapse_resolution: str | None = getattr(args, "timelapse_resolution", None)

    # LUT 옵션 검증 (CLI 인자 > 환경변수 > config > 기본값)
    lut_path_arg = getattr(args, "lut", None)
    lut_path: Path | None = None
    if lut_path_arg:
        lut_path = Path(lut_path_arg).expanduser().resolve()
        if not lut_path.is_file():
            raise FileNotFoundError(f"LUT file not found: {lut_path_arg}")
        ext = lut_path.suffix.lower()
        if ext not in LUT_SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported LUT format: {ext} "
                f"(supported: {', '.join(sorted(LUT_SUPPORTED_EXTENSIONS))})"
            )

    auto_lut_flag = getattr(args, "auto_lut", None)
    no_auto_lut_flag = getattr(args, "no_auto_lut", False)
    # --no-auto-lut이 --auto-lut보다 우선 (명시적 비활성화)
    if no_auto_lut_flag:
        if auto_lut_flag:
            logger.warning("--auto-lut and --no-auto-lut both set; --no-auto-lut wins")
        auto_lut = False
    elif auto_lut_flag:
        auto_lut = True
    else:
        auto_lut = get_default_auto_lut()

    lut_before_hdr: bool = getattr(args, "lut_before_hdr", False)

    # 스케줄 옵션 검증
    schedule_arg: str | None = getattr(args, "schedule", None)
    schedule: str | None = None
    if schedule_arg:
        schedule = parse_schedule_datetime(schedule_arg)
        logger.info(f"Parsed schedule time: {schedule}")

    return ValidatedArgs(
        targets=targets,
        output=output,
        output_dir=output_dir,
        no_resume=args.no_resume,
        keep_temp=args.keep_temp,
        dry_run=args.dry_run,
        denoise=denoise_flag,
        denoise_level=resolved_denoise_level,
        normalize_audio=normalize_audio,
        group_sequences=group_sequences,
        fade_duration=fade_duration,
        upload=upload,
        parallel=parallel,
        thumbnail=thumbnail,
        thumbnail_timestamps=thumbnail_at,
        thumbnail_quality=thumbnail_quality,
        set_thumbnail=set_thumbnail,
        generated_thumbnail_paths=None,
        detect_silence=detect_silence,
        trim_silence=trim_silence,
        silence_threshold=silence_threshold,
        silence_min_duration=silence_min_duration,
        bgm_path=bgm_path,
        bgm_volume=bgm_volume,
        bgm_loop=bgm_loop,
        exclude_patterns=exclude_patterns,
        include_only_patterns=include_only_patterns,
        sort_key=sort_key_str,
        reorder=reorder_flag,
        split_duration=split_duration,
        split_size=split_size,
        archive_originals=archive_originals,
        archive_force=archive_force_flag,
        timelapse_speed=timelapse_speed,
        timelapse_audio=timelapse_audio,
        timelapse_resolution=timelapse_resolution,
        stabilize=stabilize_flag,
        stabilize_strength=resolved_stabilize_strength,
        stabilize_crop=resolved_stabilize_crop,
        project=getattr(args, "project", None),
        lut_path=lut_path,
        auto_lut=auto_lut,
        lut_before_hdr=lut_before_hdr,
        device_luts=device_luts if device_luts else None,
        quality_report=quality_report,
        notify=bool(getattr(args, "notify", False)) or get_default_notify(),
        schedule=schedule,
    )


def setup_logging(verbose: bool = False) -> None:
    """
    로깅 설정.

    Args:
        verbose: 상세 로그 여부
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


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
        probe_result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(media_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        info = json.loads(probe_result.stdout)
        return float(info["format"]["duration"])
    except (subprocess.CalledProcessError, KeyError, ValueError) as e:
        raise RuntimeError(f"Failed to probe duration: {media_path} - {e}") from e


def _has_audio_stream(media_path: Path) -> bool:
    """ffprobe를 사용하여 미디어 파일에 오디오 스트림이 있는지 확인한다.

    Args:
        media_path: 미디어 파일 경로

    Returns:
        오디오 스트림 존재 여부
    """
    try:
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
        )
        info = json.loads(probe_result.stdout)
        streams = info.get("streams", [])
        return len(streams) > 0
    except (subprocess.CalledProcessError, ValueError):
        return False


def _apply_bgm_mixing(
    video_path: Path,
    bgm_path: Path,
    bgm_volume: float,
    bgm_loop: bool,
    output_path: Path,
) -> Path:
    """병합된 영상에 BGM을 믹싱한다.

    ffprobe로 영상/BGM 길이와 오디오 스트림 존재 여부를 확인한 뒤
    :func:`~tubearchive.ffmpeg.effects.create_bgm_filter` 로 필터를 생성하고
    ffmpeg로 오디오만 재인코딩한다 (영상은 ``-c:v copy``).

    Args:
        video_path: 병합된 영상 파일 경로
        bgm_path: BGM 파일 경로
        bgm_volume: BGM 상대 볼륨 (0.0~1.0)
        bgm_loop: BGM 루프 재생 여부
        output_path: 출력 파일 경로

    Returns:
        BGM이 믹싱된 최종 파일 경로

    Raises:
        RuntimeError: FFmpeg 실행 실패
    """
    from tubearchive.ffmpeg.effects import create_bgm_filter

    logger.info(f"Applying BGM mixing: {bgm_path.name}")

    video_duration = _get_media_duration(video_path)
    bgm_duration = _get_media_duration(bgm_path)
    has_audio = _has_audio_stream(video_path)

    logger.info(
        f"Video duration: {video_duration:.2f}s, BGM duration: {bgm_duration:.2f}s, "
        f"has_audio: {has_audio}"
    )

    bgm_filter = create_bgm_filter(
        bgm_duration=bgm_duration,
        video_duration=video_duration,
        bgm_volume=bgm_volume,
        bgm_loop=bgm_loop,
        has_audio=has_audio,
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(bgm_path),
        "-filter_complex",
        bgm_filter,
        "-map",
        "0:v",
        "-map",
        "[a_out]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "320k",
        str(output_path),
    ]

    logger.info(f"Running BGM mixing: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.error(f"BGM mixing failed: {result.stderr}")
        raise RuntimeError(f"BGM mixing failed: {result.stderr}")

    logger.info(f"BGM mixing completed: {output_path}")
    return output_path


def handle_single_file_upload(
    video_file: VideoFile,
    args: ValidatedArgs,
) -> Path:
    """
    단일 파일 직접 업로드 처리.

    인코딩/병합 없이 DB 저장 후 원본 파일 경로 반환.

    Args:
        video_file: VideoFile 객체
        args: 검증된 CLI 인자

    Returns:
        원본 파일 경로
    """
    logger.info(f"Single file detected with --upload, skipping transcode: {video_file.path.name}")

    # 1. 메타데이터 수집
    metadata = detect_metadata(video_file.path)

    # 2. YouTube 제목 생성 (디렉토리명 기반)
    title = get_output_filename([video_file.path]).replace(".mp4", "")

    # 3. 촬영 시간 추출
    creation_time_str = video_file.creation_time.strftime("%H:%M:%S")

    # 4. 클립 정보 생성
    clip = ClipInfo(
        name=video_file.path.name,
        duration=metadata.duration_seconds,
        device=metadata.device_model or "Unknown",
        shot_time=creation_time_str,
    )

    # 5. YouTube 설명 생성 (단일 파일용)
    youtube_description = generate_single_file_description(
        device=clip.device, shot_time=clip.shot_time
    )

    # 6. DB 저장 (타임라인 dict: start/end 포함)
    clip_dict: dict[str, str | float | None] = {
        "name": clip.name,
        "duration": clip.duration,
        "start": 0.0,
        "end": clip.duration,
        "device": clip.device,
        "shot_time": clip.shot_time,
    }
    with database_session() as conn:
        repo = MergeJobRepository(conn)
        today = date.today().isoformat()

        repo.create(
            output_path=video_file.path,
            video_ids=[],  # 트랜스코딩 안 함
            title=title,
            date=today,
            total_duration_seconds=metadata.duration_seconds,
            total_size_bytes=video_file.path.stat().st_size,
            clips_info_json=json.dumps([clip_dict]),
            summary_markdown=youtube_description,
        )

    # 7. 콘솔 출력
    logger.info(f"Saved to DB: {title}")
    print("\n📁 단일 파일 업로드 모드 (트랜스코딩 생략)")
    print(f"📹 파일: {video_file.path.name}")
    minutes = int(metadata.duration_seconds // 60)
    seconds = int(metadata.duration_seconds % 60)
    print(f"⏱️  길이: {minutes}분 {seconds}초")
    if metadata.device_model:
        print(f"📷 기기: {metadata.device_model}")

    return video_file.path


def _collect_clip_info(video_file: VideoFile) -> ClipInfo:
    """영상 파일에서 Summary·타임라인용 클립 메타데이터를 수집한다.

    ffprobe로 해상도·코덱·길이 등을 추출하고, 파일 생성 시간에서
    촬영 시각 문자열을 만든다. ffprobe 실패 시 duration=0.0 폴백.

    Args:
        video_file: 대상 영상 파일

    Returns:
        ClipInfo(name, duration, device, shot_time)
    """
    try:
        metadata = detect_metadata(video_file.path)
        creation_time_str = video_file.creation_time.strftime("%H:%M:%S")
        return ClipInfo(
            name=video_file.path.name,
            duration=metadata.duration_seconds,
            device=metadata.device_model,
            shot_time=creation_time_str,
        )
    except Exception as e:
        logger.warning(f"Failed to get metadata for {video_file.path}: {e}")
        return ClipInfo(name=video_file.path.name, duration=0.0, device=None, shot_time=None)


def _transcode_single(
    video_file: VideoFile,
    temp_dir: Path,
    opts: TranscodeOptions,
) -> TranscodeResult:
    """단일 파일을 독립 Transcoder 컨텍스트에서 트랜스코딩한다.

    ``_transcode_parallel`` 에서 ThreadPoolExecutor에 제출되는 단위 작업이다.
    각 호출마다 Transcoder를 새로 생성하여 스레드 안전성을 보장한다.

    Args:
        video_file: 트랜스코딩할 원본 영상
        temp_dir: 트랜스코딩 출력 임시 디렉토리
        opts: 공통 트랜스코딩 옵션 (denoise, loudnorm, fade 등)

    Returns:
        ``TranscodeResult`` (출력 경로, video DB ID, 클립 메타데이터)
    """
    fade_config = opts.fade_map.get(video_file.path) if opts.fade_map else None
    fade_in = fade_config.fade_in if fade_config else None
    fade_out = fade_config.fade_out if fade_config else None

    with Transcoder(temp_dir=temp_dir) as transcoder:
        output_path, video_id, silence_segments = transcoder.transcode_video(
            video_file,
            denoise=opts.denoise,
            denoise_level=opts.denoise_level,
            normalize_audio=opts.normalize_audio,
            fade_duration=opts.fade_duration,
            fade_in_duration=fade_in,
            fade_out_duration=fade_out,
            trim_silence=opts.trim_silence,
            silence_threshold=opts.silence_threshold,
            silence_min_duration=opts.silence_min_duration,
            stabilize=opts.stabilize,
            stabilize_strength=opts.stabilize_strength,
            stabilize_crop=opts.stabilize_crop,
            lut_path=str(opts.lut_path) if opts.lut_path else None,
            auto_lut=opts.auto_lut,
            lut_before_hdr=opts.lut_before_hdr,
            device_luts=opts.device_luts,
        )
        clip_info = _collect_clip_info(video_file)
        return TranscodeResult(
            output_path=output_path,
            video_id=video_id,
            clip_info=clip_info,
            silence_segments=silence_segments,
        )


def _transcode_parallel(
    video_files: list[VideoFile],
    temp_dir: Path,
    max_workers: int,
    opts: TranscodeOptions,
) -> list[TranscodeResult]:
    """``ThreadPoolExecutor`` 를 사용한 병렬 트랜스코딩.

    각 파일을 독립된 :class:`Transcoder` 컨텍스트에서 처리하며,
    완료 순서에 관계없이 **원본 인덱스 순** 으로 결과를 정렬하여 반환한다.

    Args:
        video_files: 트랜스코딩 대상 파일 목록
        temp_dir: 임시 출력 디렉토리
        max_workers: 최대 동시 워커 수
        opts: 트랜스코딩 공통 옵션 (denoise, loudnorm, fade 등)

    Returns:
        원본 순서가 유지된 트랜스코딩 결과 리스트

    Raises:
        RuntimeError: 하나 이상의 워커가 실패한 경우
    """
    results: dict[int, TranscodeResult] = {}
    completed_count = 0
    total_count = len(video_files)
    print_lock = Lock()

    def on_complete(idx: int, filename: str, status: str) -> None:
        """병렬 워커 완료 콜백 -- 진행 카운터 갱신 및 콘솔 출력."""
        nonlocal completed_count
        with print_lock:
            completed_count += 1
            print(
                f"\r🎬 트랜스코딩: [{completed_count}/{total_count}] {status}: {filename}",
                end="",
                flush=True,
            )
            if completed_count == total_count:
                print()  # 줄바꿈

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                _transcode_single,
                video_file,
                temp_dir,
                opts,
            ): i
            for i, video_file in enumerate(video_files)
        }
        for future in as_completed(futures):
            idx = futures[future]
            try:
                result = future.result()
                results[idx] = result
                on_complete(idx, video_files[idx].path.name, "완료")
            except Exception as e:
                logger.error(f"Failed to transcode {video_files[idx].path}: {e}")
                on_complete(idx, video_files[idx].path.name, "실패")
                raise

    return [results[i] for i in range(total_count)]


def _transcode_sequential(
    video_files: list[VideoFile],
    temp_dir: Path,
    opts: TranscodeOptions,
) -> list[TranscodeResult]:
    """영상 파일을 순차적으로 트랜스코딩한다.

    :class:`MultiProgressBar` 로 파일별 진행률(fps, ETA)을 실시간 표시한다.
    ``parallel=1`` 이거나 파일이 1개일 때 사용된다.

    Args:
        video_files: 트랜스코딩할 영상 목록
        temp_dir: 트랜스코딩 결과 저장 임시 디렉토리
        opts: 트랜스코딩 공통 옵션 (오디오·페이드 설정)

    Returns:
        트랜스코딩 결과 리스트 (출력 경로, video_id, 클립 정보)
    """
    results: list[TranscodeResult] = []
    progress = MultiProgressBar(total_files=len(video_files))

    with Transcoder(temp_dir=temp_dir) as transcoder:
        for video_file in video_files:
            progress.start_file(video_file.path.name)

            def on_progress_info(info: ProgressInfo) -> None:
                """FFmpeg 상세 진행률을 MultiProgressBar에 전달."""
                progress.update_with_info(info)

            fade_config = opts.fade_map.get(video_file.path) if opts.fade_map else None
            fade_in = fade_config.fade_in if fade_config else None
            fade_out = fade_config.fade_out if fade_config else None

            output_path, video_id, silence_segments = transcoder.transcode_video(
                video_file,
                denoise=opts.denoise,
                denoise_level=opts.denoise_level,
                normalize_audio=opts.normalize_audio,
                fade_duration=opts.fade_duration,
                fade_in_duration=fade_in,
                fade_out_duration=fade_out,
                trim_silence=opts.trim_silence,
                silence_threshold=opts.silence_threshold,
                silence_min_duration=opts.silence_min_duration,
                stabilize=opts.stabilize,
                stabilize_strength=opts.stabilize_strength,
                stabilize_crop=opts.stabilize_crop,
                lut_path=str(opts.lut_path) if opts.lut_path else None,
                auto_lut=opts.auto_lut,
                lut_before_hdr=opts.lut_before_hdr,
                device_luts=opts.device_luts,
                progress_info_callback=on_progress_info,
            )
            clip_info = _collect_clip_info(video_file)
            results.append(
                TranscodeResult(
                    output_path=output_path,
                    video_id=video_id,
                    clip_info=clip_info,
                    silence_segments=silence_segments,
                )
            )
            progress.finish_file()

    return results


def _apply_ordering(
    video_files: list[VideoFile],
    validated_args: ValidatedArgs,
    *,
    allow_interactive: bool = True,
) -> list[VideoFile]:
    """필터링·정렬·인터랙티브 재정렬을 순차 적용한다.

    Args:
        video_files: 스캔된 영상 파일 리스트
        validated_args: 검증된 CLI 인자
        allow_interactive: ``--reorder`` 인터랙티브 모드 허용 여부
            (dry-run에서는 False)

    Returns:
        최종 순서의 영상 파일 리스트

    Raises:
        ValueError: 필터 적용 후 파일이 없거나 재정렬 후 파일이 없을 때
    """
    if validated_args.exclude_patterns or validated_args.include_only_patterns:
        video_files = filter_videos(
            video_files,
            exclude_patterns=validated_args.exclude_patterns,
            include_only_patterns=validated_args.include_only_patterns,
        )
        if not video_files:
            raise ValueError("All files excluded by filter patterns")

    if validated_args.sort_key != "time":
        video_files = sort_videos(video_files, SortKey(validated_args.sort_key))

    if allow_interactive and validated_args.reorder:
        video_files = interactive_reorder(video_files)
        if not video_files:
            raise ValueError("No files remaining after reorder")

    return video_files


def _resolve_output_path(validated_args: ValidatedArgs) -> Path:
    """출력 파일 경로를 결정한다.

    우선순위: ``--output`` 직접 지정 > ``--output-dir`` + 자동 파일명.

    Args:
        validated_args: 검증된 CLI 인자

    Returns:
        최종 출력 파일 경로
    """
    if validated_args.output:
        return validated_args.output
    output_filename = get_output_filename(validated_args.targets)
    output_dir = validated_args.output_dir or Path.cwd()
    return output_dir / output_filename


def _cleanup_temp(
    temp_dir: Path,
    results: list[TranscodeResult],
    final_path: Path,
    video_ids: list[int],
) -> None:
    """임시 파일 및 폴더를 정리하고 DB 상태를 업데이트한다."""
    logger.info("Cleaning up temporary files...")
    for r in results:
        if r.output_path.exists() and r.output_path != final_path:
            r.output_path.unlink()
            logger.debug(f"  Removed: {r.output_path}")

    # DB 상태 업데이트: completed → merged
    _mark_transcoding_jobs_merged(video_ids)

    # 임시 폴더 삭제
    if temp_dir.exists():
        try:
            shutil.rmtree(temp_dir)
            logger.info(f"Removed temp directory: {temp_dir}")
        except OSError as e:
            logger.warning(f"Failed to remove temp directory: {e}")


def _print_summary(summary_markdown: str | None) -> None:
    """병합 요약 마크다운을 구분선과 함께 콘솔에 출력한다.

    Args:
        summary_markdown: 출력할 마크다운 문자열. ``None`` 이면 무시.
    """
    if not summary_markdown:
        return
    print("\n" + "=" * 60)
    print("📋 SUMMARY (Copy & Paste)")
    print("=" * 60)
    print(summary_markdown)
    print("=" * 60 + "\n")


def run_pipeline(
    validated_args: ValidatedArgs,
    notifier: Notifier | None = None,
    generated_thumbnail_paths: list[Path] | None = None,
) -> Path:
    """
    전체 파이프라인 실행.

    스캔 → 트랜스코딩 → 병합 → DB 저장 → 정리 → Summary 출력

    Args:
        validated_args: 검증된 인자
        notifier: 알림 오케스트레이터 (None이면 알림 비활성화)
        generated_thumbnail_paths: 썸네일 생성 결과 저장용 출력 버퍼 (기본값 None)

    Returns:
        최종 출력 파일 경로
    """
    # 1. 파일 스캔
    logger.info("Scanning video files...")
    video_files = scan_videos(validated_args.targets)

    if not video_files:
        logger.error("No video files found")
        raise ValueError("No video files found")

    logger.info(f"Found {len(video_files)} video files")
    for video_file in video_files:
        logger.info(f"  - {video_file.path.name}")

    video_files = _apply_ordering(video_files, validated_args)

    # --detect-silence: 분석만 수행 후 종료
    if validated_args.detect_silence:
        _detect_silence_only(video_files, validated_args)
        return Path()  # 빈 경로 반환

    # 단일 파일 + --upload 시 빠른 경로
    if len(video_files) == 1 and validated_args.upload:
        return handle_single_file_upload(video_files[0], validated_args)

    # 1.5 그룹핑 및 재정렬
    if validated_args.group_sequences:
        groups = group_sequences(video_files)
        video_files = reorder_with_groups(video_files, groups)
        for group in groups:
            if len(group.files) > 1:
                logger.info(
                    "연속 시퀀스 감지: %s (%d개 파일)",
                    group.group_id,
                    len(group.files),
                )
    else:
        groups = [
            FileSequenceGroup(files=(video_file,), group_id=f"s_{i}")
            for i, video_file in enumerate(video_files)
        ]

    fade_map = compute_fade_map(groups, default_fade=validated_args.fade_duration)

    # 2. 트랜스코딩
    temp_dir = get_temp_dir()
    logger.info(f"Using temp directory: {temp_dir}")

    transcode_opts = TranscodeOptions(
        denoise=validated_args.denoise,
        denoise_level=validated_args.denoise_level,
        normalize_audio=validated_args.normalize_audio,
        fade_map=fade_map,
        fade_duration=validated_args.fade_duration,
        trim_silence=validated_args.trim_silence,
        silence_threshold=validated_args.silence_threshold,
        silence_min_duration=validated_args.silence_min_duration,
        stabilize=validated_args.stabilize,
        stabilize_strength=validated_args.stabilize_strength,
        stabilize_crop=validated_args.stabilize_crop,
        lut_path=validated_args.lut_path,
        auto_lut=validated_args.auto_lut,
        lut_before_hdr=validated_args.lut_before_hdr,
        device_luts=validated_args.device_luts,
    )

    if validated_args.stabilize:
        logger.info(
            "영상 안정화 활성화 (vidstab 2-pass, strength=%s, crop=%s) "
            "— 트랜스코딩 시간이 증가합니다",
            validated_args.stabilize_strength,
            validated_args.stabilize_crop,
        )

    parallel = validated_args.parallel
    if parallel > 1:
        logger.info(f"Starting parallel transcoding (workers: {parallel})...")
        results = _transcode_parallel(video_files, temp_dir, parallel, transcode_opts)
    else:
        logger.info("Starting transcoding...")
        results = _transcode_sequential(video_files, temp_dir, transcode_opts)

    # 알림: 트랜스코딩 완료
    if notifier:
        from tubearchive.notification import transcode_complete_event

        notifier.notify(
            transcode_complete_event(
                file_count=len(results),
                total_duration=sum(r.clip_info.duration for r in results),
            )
        )

    # 3. 병합
    logger.info("Merging videos...")
    output_path = _resolve_output_path(validated_args)
    final_path = Merger(temp_dir=temp_dir).merge(
        [r.output_path for r in results],
        output_path,
    )
    logger.info(f"Final output: {final_path}")

    # 알림: 병합 완료
    if notifier:
        from tubearchive.notification import merge_complete_event

        notifier.notify(
            merge_complete_event(
                output_path=str(final_path),
                file_count=len(results),
                total_size_bytes=final_path.stat().st_size if final_path.exists() else 0,
            )
        )

    # 3.5 BGM 믹싱 (옵션)
    if validated_args.bgm_path:
        logger.info("Applying BGM mixing...")
        temp_bgm_output = temp_dir / f"bgm_mixed_{final_path.name}"
        bgm_mixed_path = _apply_bgm_mixing(
            video_path=final_path,
            bgm_path=validated_args.bgm_path,
            bgm_volume=validated_args.bgm_volume,
            bgm_loop=validated_args.bgm_loop,
            output_path=temp_bgm_output,
        )
        # 원본을 BGM 믹싱된 파일로 대체
        shutil.move(str(bgm_mixed_path), str(final_path))
        logger.info(f"BGM mixing applied: {final_path}")

    # 4.1 화질 리포트 출력 (선택)
    if validated_args.quality_report:
        _print_quality_report(video_files, results)

    # 4. DB 저장 및 Summary 생성
    video_ids = [r.video_id for r in results]
    video_clips = [r.clip_info for r in results]
    summary, merge_job_id = save_merge_job_to_db(
        final_path,
        video_clips,
        validated_args.targets,
        video_ids,
        groups=groups,
    )

    # 4.1 프로젝트 연결 (--project 옵션 시)
    if validated_args.project and merge_job_id is not None:
        _link_merge_job_to_project(validated_args.project, merge_job_id)

    # 4.5 썸네일 생성 (비필수)
    if generated_thumbnail_paths is not None:
        generated_thumbnail_paths.clear()

    if validated_args.thumbnail:
        thumbnail_paths = _generate_thumbnails(final_path, validated_args)
        if generated_thumbnail_paths is not None:
            generated_thumbnail_paths.extend(thumbnail_paths)
        if thumbnail_paths:
            print(f"\n🖼️  썸네일 {len(thumbnail_paths)}장 생성:")
            for tp in thumbnail_paths:
                print(f"  - {tp}")

    # 4.6 영상 분할 (비필수)
    if validated_args.split_duration or validated_args.split_size:
        from tubearchive.core.splitter import SplitOptions, VideoSplitter

        splitter = VideoSplitter()
        split_opts = SplitOptions(
            duration=(
                splitter.parse_duration(validated_args.split_duration)
                if validated_args.split_duration
                else None
            ),
            size=(
                splitter.parse_size(validated_args.split_size)
                if validated_args.split_size
                else None
            ),
        )

        split_output_dir = final_path.parent
        split_criterion = "duration" if split_opts.duration else "size"
        split_value = validated_args.split_duration or validated_args.split_size or ""
        logger.info("Splitting video...")
        try:
            split_files = splitter.split_video(final_path, split_output_dir, split_opts)
            if split_files:
                print(f"\n✂️  영상 {len(split_files)}개로 분할:")
                for sf in split_files:
                    file_size = sf.stat().st_size if sf.exists() else 0
                    size_str = format_size(file_size)
                    print(f"  - {sf.name} ({size_str})")

                # DB에 split job 저장
                if merge_job_id is not None:
                    try:
                        with database_session() as conn:
                            split_repo = SplitJobRepository(conn)
                            split_repo.create(
                                merge_job_id=merge_job_id,
                                split_criterion=split_criterion,
                                split_value=split_value,
                                output_files=split_files,
                            )
                        logger.debug("Split job saved to database")
                    except Exception as e:
                        logger.warning(f"Failed to save split job to DB: {e}")
        except Exception as e:
            logger.warning(f"Failed to split video: {e}")
            print(f"\n⚠️  영상 분할 실패: {e}")

    # 4.7 타임랩스 생성 (비필수)
    timelapse_path: Path | None = None
    if validated_args.timelapse_speed:
        timelapse_path = _generate_timelapse(final_path, validated_args)
        if timelapse_path:
            print(f"\n⏩ 타임랩스 ({validated_args.timelapse_speed}x) 생성:")
            print(f"  - {timelapse_path}")
    # 5. 임시 파일 정리
    if not validated_args.keep_temp:
        _cleanup_temp(temp_dir, results, final_path, video_ids)

    # 5.5 원본 파일 아카이빙 (CLI 옵션 또는 config 정책)
    video_paths_for_archive = [
        (r.video_id, vf.path) for r, vf in zip(results, video_files, strict=True)
    ]
    _archive_originals(video_paths_for_archive, validated_args)

    # 6. Summary 출력
    _print_summary(summary)

    return final_path


def _archive_originals(
    video_paths: list[tuple[int, Path]],
    validated_args: ValidatedArgs,
) -> None:
    """원본 파일들을 정책에 따라 아카이빙한다.

    CLI 옵션(``--archive-originals``) 또는 설정 파일(``[archive]``)의
    정책을 읽어 원본 파일을 이동/삭제/유지한다.

    우선순위: CLI ``--archive-originals`` > config ``[archive].policy``

    Args:
        video_paths: (video_id, original_path) 튜플 리스트
        validated_args: 검증된 CLI 인자
    """
    from tubearchive.config import get_default_archive_destination, get_default_archive_policy
    from tubearchive.core.archiver import ArchivePolicy, Archiver

    if not video_paths:
        logger.warning("아카이빙할 원본 파일이 없습니다.")
        return

    # 정책 결정: CLI 옵션 > config > 기본값(KEEP)
    if validated_args.archive_originals:
        policy = ArchivePolicy.MOVE
        destination: Path | None = validated_args.archive_originals
    else:
        policy_str = get_default_archive_policy()
        policy = ArchivePolicy(policy_str)
        destination = get_default_archive_destination()

    # KEEP 정책이면 아무것도 하지 않음
    if policy == ArchivePolicy.KEEP:
        logger.debug("아카이브 정책이 KEEP입니다. 원본 파일 유지.")
        return

    # MOVE 정책인데 destination이 없으면 경고
    if policy == ArchivePolicy.MOVE and not destination:
        logger.warning("MOVE 정책이 설정되었으나 destination이 없습니다. 원본 파일 유지.")
        return

    # DELETE 정책 시 확인 프롬프트 (core 모듈이 아닌 CLI 계층에서 처리)
    if (
        policy == ArchivePolicy.DELETE
        and not validated_args.archive_force
        and not _prompt_archive_delete_confirmation(len(video_paths))
    ):
        logger.info("사용자가 삭제를 취소했습니다.")
        return

    logger.info("원본 파일 아카이빙 시작 (정책: %s)...", policy.value)

    with database_session() as conn:
        from tubearchive.database.repository import ArchiveHistoryRepository

        archive_repo = ArchiveHistoryRepository(conn)
        archiver = Archiver(
            repo=archive_repo,
            policy=policy,
            destination=destination,
        )
        stats = archiver.archive_files(video_paths)

    if policy == ArchivePolicy.MOVE:
        logger.info("아카이빙 완료: 이동 %d, 실패 %d", stats.moved, stats.failed)
    elif policy == ArchivePolicy.DELETE:
        logger.info("아카이빙 완료: 삭제 %d, 실패 %d", stats.deleted, stats.failed)


def _prompt_archive_delete_confirmation(file_count: int) -> bool:
    """원본 파일 삭제 확인 프롬프트를 표시한다.

    Args:
        file_count: 삭제 대상 파일 개수

    Returns:
        True: 삭제 승인, False: 취소
    """
    print(f"\n⚠️  {file_count}개의 원본 파일을 영구 삭제하려고 합니다.")
    print("이 작업은 되돌릴 수 없습니다.")
    response = input("계속하시겠습니까? (y/N): ").strip().lower()
    return response in {"y", "yes"}


def _detect_silence_only(
    video_files: list[VideoFile],
    validated_args: ValidatedArgs,
) -> None:
    """
    무음 구간 감지 전용 모드.

    각 영상의 무음 구간을 감지하고 콘솔에 출력한다.
    """
    from tubearchive.ffmpeg.effects import (
        create_silence_detect_filter,
        parse_silence_segments,
    )
    from tubearchive.ffmpeg.executor import FFmpegExecutor

    executor = FFmpegExecutor()

    threshold = validated_args.silence_threshold
    min_duration = validated_args.silence_min_duration

    for video_file in video_files:
        print(f"\n🔍 분석 중: {video_file.path.name}")

        # silencedetect 필터 생성
        detect_filter = create_silence_detect_filter(
            threshold=threshold,
            min_duration=min_duration,
        )

        # 분석 명령 실행
        cmd = executor.build_silence_detection_command(
            input_path=video_file.path,
            audio_filter=detect_filter,
        )
        stderr = executor.run_analysis(cmd)

        # 파싱
        segments = parse_silence_segments(stderr)

        if not segments:
            print("  무음 구간 없음")
        else:
            print(f"  무음 구간 {len(segments)}개 발견:")
            for i, seg in enumerate(segments, 1):
                print(f"    {i}. {seg.start:.2f}s - {seg.end:.2f}s (길이: {seg.duration:.2f}s)")


def _generate_thumbnails(
    video_path: Path,
    validated_args: ValidatedArgs,
) -> list[Path]:
    """병합 영상에서 썸네일 생성.

    실패 시 경고만 남기고 빈 리스트 반환 (파이프라인 중단 없음).
    """
    from tubearchive.ffmpeg.thumbnail import extract_thumbnails, parse_timestamp

    timestamps: list[float] | None = None
    if validated_args.thumbnail_timestamps:
        parsed: list[float] = []
        for ts in validated_args.thumbnail_timestamps:
            try:
                parsed.append(parse_timestamp(ts))
            except ValueError as e:
                logger.warning("Invalid thumbnail timestamp '%s': %s", ts, e)
        timestamps = parsed if parsed else None

    try:
        return extract_thumbnails(
            video_path,
            timestamps=timestamps,
            output_dir=validated_args.output_dir,
            quality=validated_args.thumbnail_quality,
        )
    except Exception:
        logger.warning("Failed to generate thumbnails", exc_info=True)
        return []


def _print_quality_report(
    video_files: list[VideoFile],
    results: list[TranscodeResult],
) -> None:
    """트랜스코딩 전/후 SSIM/PSNR/VMAF 지표를 출력한다."""
    from tubearchive.core.quality import generate_quality_reports

    pairs = [
        (source.path, result.output_path)
        for source, result in zip(video_files, results, strict=True)
    ]
    reports = generate_quality_reports(pairs)
    if not reports:
        print("\n🔬 화질 리포트: 계산 대상 없음")
        return

    print("\n🔬 화질 리포트:")
    for report in reports:
        print(f"\n  - 원본: {report.source_path.name}")
        print(f"    결과: {report.output_path.name}")
        if report.ssim is not None:
            print(f"    SSIM: {report.ssim:.4f}")
        if report.psnr is not None:
            print(f"    PSNR: {report.psnr:.4f} dB")
        if report.vmaf is not None:
            print(f"    VMAF: {report.vmaf:.4f}")

        if report.unavailable:
            missing = ", ".join(sorted(report.unavailable))
            print(f"    미지원/실패 지표: {missing}")
        if report.errors:
            for err in report.errors:
                print(f"    경고: {err}")


def _generate_timelapse(
    video_path: Path,
    validated_args: ValidatedArgs,
) -> Path | None:
    """병합 영상에서 타임랩스 생성.

    실패 시 경고만 남기고 None 반환 (파이프라인 중단 없음).

    Args:
        video_path: 입력 병합 영상 경로
        validated_args: 검증된 CLI 인자

    Returns:
        타임랩스 파일 경로 (실패 시 None)
    """
    from tubearchive.core.timelapse import TimelapseGenerator

    if validated_args.timelapse_speed is None:
        return None

    # 출력 경로 생성
    stem = video_path.stem
    suffix = video_path.suffix
    output_dir = validated_args.output_dir or video_path.parent
    output_path = output_dir / f"{stem}_timelapse_{validated_args.timelapse_speed}x{suffix}"

    try:
        logger.info(f"Generating {validated_args.timelapse_speed}x timelapse: {output_path.name}")
        generator = TimelapseGenerator()
        return generator.generate(
            input_path=video_path,
            output_path=output_path,
            speed=validated_args.timelapse_speed,
            keep_audio=validated_args.timelapse_audio,
            resolution=validated_args.timelapse_resolution,
        )
    except Exception:
        logger.warning("Failed to generate timelapse", exc_info=True)
        return None


def _mark_transcoding_jobs_merged(video_ids: list[int]) -> None:
    """트랜스코딩 작업 상태를 merged로 업데이트 (임시 파일 정리 후)."""
    if not video_ids:
        return
    try:
        with database_session() as conn:
            job_repo = TranscodingJobRepository(conn)
            count = job_repo.mark_merged_by_video_ids(video_ids)
        logger.debug(f"Marked {count} transcoding jobs as merged")
    except Exception:
        logger.warning("Failed to mark transcoding jobs as merged", exc_info=True)


def save_merge_job_to_db(
    output_path: Path,
    video_clips: list[ClipInfo],
    targets: list[Path],
    video_ids: list[int],
    groups: list[FileSequenceGroup] | None = None,
) -> tuple[str | None, int | None]:
    """병합 작업 정보를 DB에 저장 (타임라인 및 Summary 포함).

    Args:
        output_path: 출력 파일 경로
        video_clips: 클립 메타데이터 리스트
        targets: 입력 타겟 목록 (제목 추출용)
        video_ids: 병합된 영상들의 DB ID 목록
        groups: 시퀀스 그룹 목록 (Summary 생성용)

    Returns:
        (콘솔 출력용 Summary 마크다운, merge_job_id) 튜플. 실패 시 (None, None).
    """
    from tubearchive.utils.summary_generator import (
        generate_clip_summary,
        generate_youtube_description,
    )

    try:
        with database_session() as conn:
            repo = MergeJobRepository(conn)

            # 타임라인 정보 생성 (각 클립의 메타데이터 포함)
            timeline: list[dict[str, str | float | None]] = []
            current_time = 0.0
            for clip in video_clips:
                timeline.append(
                    {
                        "name": clip.name,
                        "duration": clip.duration,
                        "start": current_time,
                        "end": current_time + clip.duration,
                        "device": clip.device,
                        "shot_time": clip.shot_time,
                    }
                )
                current_time += clip.duration

            clips_json = json.dumps(timeline, ensure_ascii=False)

            # 제목: 디렉토리명
            title = None
            if targets:
                first_target = targets[0]
                title = first_target.name if first_target.is_dir() else first_target.parent.name
                if not title or title == ".":
                    title = output_path.stem

            today = date.today().isoformat()

            total_duration = sum(c.duration for c in video_clips)
            total_size = output_path.stat().st_size if output_path.exists() else 0

            # 콘솔 출력용 요약 (마크다운 형식)
            console_summary = generate_clip_summary(video_clips, groups=groups)
            # YouTube 설명용 (타임스탬프 + 촬영기기)
            youtube_description = generate_youtube_description(video_clips, groups=groups)

            merge_job_id = repo.create(
                output_path=output_path,
                video_ids=video_ids,
                title=title,
                date=today,
                total_duration_seconds=total_duration,
                total_size_bytes=total_size,
                clips_info_json=clips_json,
                summary_markdown=youtube_description,
            )

        logger.debug("Merge job saved to database with summary")
        return console_summary, merge_job_id

    except Exception as e:
        logger.warning(f"Failed to save merge job to DB: {e}")
        return None, None


def _link_merge_job_to_project(project_name: str, merge_job_id: int) -> None:
    """병합 결과를 프로젝트에 연결한다.

    프로젝트가 없으면 자동 생성하고, merge_job을 연결한다.
    날짜 범위도 자동으로 갱신된다.

    Args:
        project_name: 프로젝트 이름
        merge_job_id: merge_job ID
    """
    from tubearchive.database.repository import ProjectRepository

    try:
        with database_session() as conn:
            repo = ProjectRepository(conn)
            project = repo.get_or_create(project_name)
            if project.id is None:
                logger.warning("Project created but has no ID")
                return
            repo.add_merge_job(project.id, merge_job_id)
            logger.info(f"Merge job {merge_job_id} linked to project '{project_name}'")
            print(f"\n📁 프로젝트 '{project_name}'에 병합 결과 연결됨")
    except Exception as e:
        logger.warning(f"Failed to link merge job to project: {e}")


def upload_to_youtube(
    file_path: Path,
    title: str | None = None,
    description: str = "",
    privacy: str = "unlisted",
    publish_at: str | None = None,
    merge_job_id: int | None = None,
    playlist_ids: list[str] | None = None,
    chunk_mb: int | None = None,
    thumbnail: Path | None = None,
) -> str | None:
    """
    영상을 YouTube에 업로드.

    Args:
        file_path: 업로드할 영상 파일 경로
        title: 영상 제목 (None이면 파일명 사용)
        description: 영상 설명
        privacy: 공개 설정 (public, unlisted, private)
        publish_at: 예약 공개 시간 (ISO 8601 형식, 설정 시 privacy는 private로 자동 변경)
        merge_job_id: DB에 저장할 MergeJob ID
        playlist_ids: 추가할 플레이리스트 ID 리스트 (None이면 추가 안 함)
        chunk_mb: 업로드 청크 크기 MB (None이면 환경변수/기본값)
        thumbnail: 썸네일 이미지 경로

    Returns:
        업로드된 YouTube 영상 ID. 실패 시 None.
    """
    from tubearchive.youtube.auth import YouTubeAuthError, get_authenticated_service
    from tubearchive.youtube.playlist import PlaylistError, add_to_playlist
    from tubearchive.youtube.uploader import (
        YouTubeUploader,
        YouTubeUploadError,
        validate_upload,
    )

    if not file_path.exists():
        raise FileNotFoundError(f"Video file not found: {file_path}")

    # 업로드 가능 여부 검증
    validation = validate_upload(file_path)
    print(f"\n{validation.get_summary()}")

    if not validation.is_valid:
        print("\n💡 해결 방법:")
        print("   - 영상을 더 작은 파트로 분할하여 업로드")
        print("   - 비트레이트를 낮춰 재인코딩")
        raise YouTubeUploadError("Video exceeds YouTube limits")

    if validation.warnings:
        # 경고가 있으면 사용자 확인
        try:
            response = safe_input("\n계속 업로드하시겠습니까? (y/N): ").lower()
            if response not in ("y", "yes"):
                print("업로드가 취소되었습니다.")
                return None
        except KeyboardInterrupt:
            print("\n업로드가 취소되었습니다.")
            return None

    # 제목 결정: 지정값 > 파일명(확장자 제외)
    # YYYYMMDD 형식을 'YYYY년 M월 D일'로 변환
    raw_title = title or file_path.stem
    video_title = format_youtube_title(raw_title)

    logger.info(f"Uploading to YouTube: {file_path}")
    logger.info(f"  Title: {video_title}")
    logger.info(f"  Privacy: {privacy}")

    # 인증 상태 확인
    from tubearchive.youtube.auth import check_auth_status

    status = check_auth_status()

    if not status.has_client_secrets:
        print("\n❌ YouTube 설정이 필요합니다.")
        print(f"\n{status.get_setup_guide()}")
        print("\n설정 완료 후 다시 실행해주세요.")
        raise YouTubeAuthError("client_secrets.json not found")

    if not status.has_valid_token:
        print("\n🔐 YouTube 인증이 필요합니다.")
        print("   브라우저에서 Google 계정 인증을 진행합니다...\n")

    try:
        # 인증 (토큰 없으면 자동으로 브라우저 열림)
        service = get_authenticated_service()

        # 업로드
        uploader = YouTubeUploader(service, chunk_mb=chunk_mb)

        # 프로그레스 바 설정
        file_size_bytes = file_path.stat().st_size
        file_size_mb = file_size_bytes / (1024 * 1024)
        bar_width = 30
        last_percent = -1

        def on_progress(percent: int) -> None:
            """업로드 진행률 콜백 -- 프로그레스 바 갱신."""
            nonlocal last_percent
            if percent == last_percent:
                return  # 중복 업데이트 방지
            last_percent = percent

            filled = int(bar_width * percent / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            uploaded_mb = file_size_mb * percent / 100
            # 줄 전체를 지우고 다시 출력 (\033[K: 커서부터 줄 끝까지 지움)
            sys.stdout.write(
                f"\r\033[K📤 [{bar}] {percent:3d}% ({uploaded_mb:.1f}/{file_size_mb:.1f}MB)"
            )
            sys.stdout.flush()
            if percent >= 100:
                sys.stdout.write("\n")
                sys.stdout.flush()

        result = uploader.upload(
            file_path=file_path,
            title=video_title,
            description=description,
            privacy=privacy,
            publish_at=publish_at,
            on_progress=on_progress,
        )

        if thumbnail is not None:
            try:
                uploader.set_thumbnail(result.video_id, thumbnail)
                print("🖼️  썸네일 업로드 완료")
            except Exception as e:
                logger.warning(f"Failed to set thumbnail for {result.video_id}: {e}")
                print(f"⚠️  썸네일 업로드 실패: {e}")

        print("\n✅ YouTube 업로드 완료!")
        print(f"🎬 URL: {result.url}")
        if result.scheduled_publish_at:
            print(f"📅 예약 공개: {result.scheduled_publish_at}")

        # 플레이리스트에 추가
        if playlist_ids:
            for pid in playlist_ids:
                try:
                    item_id = add_to_playlist(service, pid, result.video_id)
                    print(f"📋 플레이리스트에 추가됨: {pid} (item: {item_id})")
                except PlaylistError as e:
                    logger.warning(f"Failed to add to playlist {pid}: {e}")
                    print(f"⚠️ 플레이리스트 추가 실패 ({pid}): {e}")

        # DB에 YouTube ID 저장
        if merge_job_id is not None:
            try:
                with database_session() as conn:
                    repo = MergeJobRepository(conn)
                    repo.update_youtube_id(merge_job_id, result.video_id)
                logger.debug(f"YouTube ID {result.video_id} saved to merge job {merge_job_id}")
            except Exception as e:
                logger.warning(f"Failed to save YouTube ID to DB: {e}")

        return result.video_id

    except YouTubeAuthError as e:
        logger.error(f"YouTube authentication failed: {e}")
        print(f"\n❌ YouTube 인증 실패: {e}")
        print("\n설정 가이드: tubearchive --setup-youtube")
        raise
    except YouTubeUploadError as e:
        logger.error(f"YouTube upload failed: {e}")
        print(f"\n❌ YouTube 업로드 실패: {e}")
        raise
    return None


def cmd_setup_youtube() -> None:
    """
    --setup-youtube 옵션 처리.

    YouTube 인증 상태를 확인하고 설정 가이드를 출력합니다.
    """
    from tubearchive.youtube.auth import check_auth_status

    print("\n🎬 YouTube 업로드 설정 상태\n")
    print("=" * 50)

    status = check_auth_status()
    print(status.get_setup_guide())

    print("=" * 50)

    # 브라우저 인증이 필요하면 바로 실행 제안
    if status.needs_browser_auth:
        print("\n💡 지금 바로 인증하려면:")
        print("   tubearchive --youtube-auth")
        print("   (브라우저가 열리며 Google 계정 인증이 진행됩니다)")


def cmd_youtube_auth() -> None:
    """
    --youtube-auth 옵션 처리.

    브라우저를 열어 YouTube OAuth 인증을 실행합니다.
    """
    from tubearchive.youtube.auth import (
        YouTubeAuthError,
        check_auth_status,
        get_client_secrets_path,
        get_token_path,
        run_auth_flow,
        save_credentials,
    )

    print("\n🔐 YouTube 인증 시작\n")

    # 먼저 상태 확인
    status = check_auth_status()

    if status.has_valid_token:
        print("✅ 이미 인증되어 있습니다!")
        print(f"   토큰 위치: {status.token_path}")
        return

    if not status.has_client_secrets:
        print("❌ client_secrets.json이 없습니다.")
        print(f"   필요한 위치: {status.client_secrets_path}")
        print("\n설정 가이드를 보려면: tubearchive --setup-youtube")
        raise YouTubeAuthError("client_secrets.json not found")

    # 브라우저 인증 실행
    print("🌐 브라우저에서 Google 계정 인증을 진행합니다...")
    print("   (브라우저가 자동으로 열립니다)\n")

    try:
        secrets_path = get_client_secrets_path()
        token_path = get_token_path()

        credentials = run_auth_flow(secrets_path)
        save_credentials(credentials, token_path)

        print("\n✅ 인증 완료!")
        print(f"   토큰 저장됨: {token_path}")
        print("\n이제 업로드할 수 있습니다:")
        print("   tubearchive --upload ~/Videos/")
        print("   tubearchive --upload-only video.mp4")

    except Exception as e:
        logger.error(f"YouTube authentication failed: {e}")
        print(f"\n❌ 인증 실패: {e}")
        raise


def cmd_list_playlists() -> None:
    """
    --list-playlists 옵션 처리.

    내 플레이리스트 목록을 조회하여 ID와 함께 출력합니다.
    """
    from tubearchive.youtube.auth import get_authenticated_service
    from tubearchive.youtube.playlist import list_playlists

    print("\n📋 내 플레이리스트 목록\n")

    try:
        service = get_authenticated_service()
        playlists = list_playlists(service)

        if not playlists:
            print("플레이리스트가 없습니다.")
            return

        print(f"{'번호':<4} {'제목':<40} {'영상수':<8} ID")
        print("-" * 80)
        for i, pl in enumerate(playlists, 1):
            print(f"{i:<4} {pl.title:<40} {pl.item_count:<8} {pl.id}")

        print("-" * 80)
        print("\n💡 환경 변수 설정 예시:")
        print(f"   export {ENV_YOUTUBE_PLAYLIST}={playlists[0].id}")
        if len(playlists) > 1:
            ids = ",".join(pl.id for pl in playlists[:2])
            print(f"   export {ENV_YOUTUBE_PLAYLIST}={ids}  # 여러 개")

    except Exception as e:
        logger.error(f"Failed to list playlists: {e}")
        print(f"\n❌ 플레이리스트 조회 실패: {e}")

        # 스코프 부족 에러 처리
        if "insufficient" in str(e).lower() or "scope" in str(e).lower():
            from tubearchive.youtube.auth import get_token_path

            token_path = get_token_path()
            print("\n💡 권한이 부족합니다. 토큰을 삭제하고 재인증하세요:")
            print(f"   rm {token_path}")
            print("   tubearchive --youtube-auth")
        raise


def _delete_build_records(conn: sqlite3.Connection, video_ids: list[int]) -> None:
    """빌드 관련 레코드 삭제 (transcoding_jobs → videos 순서).

    트랜스코딩 작업을 먼저 삭제한 뒤 원본 영상 레코드를 삭제한다.
    외래키 참조 순서를 지키기 위해 transcoding_jobs를 먼저 정리한다.

    Args:
        conn: DB 연결
        video_ids: 삭제할 영상 ID 목록
    """
    if not video_ids:
        return
    TranscodingJobRepository(conn).delete_by_video_ids(video_ids)
    VideoRepository(conn).delete_by_ids(video_ids)


def _interactive_select(items: Sequence[object], prompt: str) -> int | None:
    """
    대화형 목록 선택.

    Args:
        items: 선택 대상 목록
        prompt: 사용자에게 표시할 프롬프트

    Returns:
        선택된 인덱스(0-based) 또는 취소 시 None
    """
    try:
        choice = safe_input(prompt)
        if not choice or choice == "0":
            print("취소됨")
            return None

        idx = int(choice) - 1
        if 0 <= idx < len(items):
            return idx

        print("잘못된 번호입니다.")
        return None
    except ValueError:
        print("숫자를 입력해주세요.")
        return None
    except KeyboardInterrupt:
        print("\n취소됨")
        return None


def _resolve_upload_thumbnail(
    explicit_thumbnail: Path | None,
    generated_thumbnail_paths: list[Path] | None = None,
) -> Path | None:
    """업로드용 썸네일 경로를 결정한다.

    우선순위:
    1. --set-thumbnail 지정값
    2. 생성된 썸네일이 1개면 자동 사용
    3. 생성된 썸네일이 여러 개면 인터랙티브 선택

    선택을 건너뛰면 None을 반환한다.
    """
    if explicit_thumbnail is not None:
        return explicit_thumbnail

    if not generated_thumbnail_paths:
        return None

    if len(generated_thumbnail_paths) == 1:
        return generated_thumbnail_paths[0]

    print("\n썸네일을 선택하세요 (0: 건너뛰기).")
    for i, path in enumerate(generated_thumbnail_paths, start=1):
        size_mb = path.stat().st_size / (1024 * 1024)
        print(f"  {i}. {path.name} ({size_mb:.1f}MB)")

    selected = _interactive_select(generated_thumbnail_paths, "선택: ")
    if selected is None:
        return None
    return generated_thumbnail_paths[selected]


def cmd_reset_build(path_arg: str) -> None:
    """``--reset-build`` 옵션 처리.

    병합 기록과 관련 트랜스코딩 기록을 삭제하여 다시 빌드할 수 있도록 한다.

    Args:
        path_arg: 파일 경로 (빈 문자열이면 대화형 목록에서 선택)
    """
    with database_session() as conn:
        repo = MergeJobRepository(conn)

        if path_arg:
            target_path = Path(path_arg).resolve()

            # merge_job에서 video_ids 조회 → 관련 레코드 삭제
            merge_job = repo.get_by_output_path(target_path)
            if merge_job:
                _delete_build_records(conn, merge_job.video_ids)

            deleted = repo.delete_by_output_path(target_path)
            if deleted > 0:
                print(f"✅ 빌드 기록 삭제됨: {target_path}")
                print("   이제 다시 빌드할 수 있습니다.")
            else:
                print(f"⚠️ 해당 경로의 기록이 없습니다: {target_path}")
        else:
            jobs = repo.get_all()
            if not jobs:
                print("📋 빌드 기록이 없습니다.")
                return

            print("\n📋 빌드 기록 목록")
            print("=" * 80)
            print(f"{'번호':<4} {'제목':<30} {'날짜':<12} {'YouTube':<10} 경로")
            print("-" * 80)
            for i, job in enumerate(jobs, 1):
                title = (job.title or "-")[:28]
                job_date = job.date or "-"
                yt_status = "✅ 업로드됨" if job.youtube_id else "-"
                path = truncate_path(str(job.output_path), max_len=40)
                print(f"{i:<4} {title:<30} {job_date:<12} {yt_status:<10} {path}")
            print("=" * 80)

            idx = _interactive_select(jobs, "\n삭제할 번호 입력 (0: 취소): ")
            if idx is None:
                return

            job = jobs[idx]
            _delete_build_records(conn, job.video_ids)
            if job.id is not None:
                repo.delete(job.id)
            print(f"\n✅ 빌드 기록 삭제됨: {job.title or job.output_path}")
            print("   이제 다시 빌드할 수 있습니다.")


def cmd_reset_upload(path_arg: str) -> None:
    """``--reset-upload`` 옵션 처리.

    YouTube 업로드 기록을 초기화하여 다시 업로드할 수 있도록 한다.

    Args:
        path_arg: 파일 경로 (빈 문자열이면 대화형 목록에서 선택)
    """
    with database_session() as conn:
        repo = MergeJobRepository(conn)

        if path_arg:
            target_path = Path(path_arg).resolve()
            merge_job = repo.get_by_output_path(target_path)
            if merge_job and merge_job.youtube_id:
                if merge_job.id is not None:
                    repo.clear_youtube_id(merge_job.id)
                print(f"✅ 업로드 기록 초기화됨: {target_path}")
                print(f"   이전 YouTube ID: {merge_job.youtube_id}")
                print("   이제 다시 업로드할 수 있습니다.")
            elif merge_job:
                print(f"⚠️ 이미 업로드 기록이 없습니다: {target_path}")
            else:
                print(f"⚠️ 해당 경로의 기록이 없습니다: {target_path}")
        else:
            jobs = repo.get_uploaded()
            if not jobs:
                print("📋 업로드된 영상이 없습니다.")
                return

            print("\n📋 업로드된 영상 목록")
            print("=" * 90)
            print(f"{'번호':<4} {'제목':<30} {'날짜':<12} {'YouTube ID':<15} 경로")
            print("-" * 90)
            for i, job in enumerate(jobs, 1):
                title = (job.title or "-")[:28]
                job_date = job.date or "-"
                yt_id = job.youtube_id or "-"
                path = truncate_path(str(job.output_path), max_len=30)
                print(f"{i:<4} {title:<30} {job_date:<12} {yt_id:<15} {path}")
            print("=" * 90)

            idx = _interactive_select(jobs, "\n초기화할 번호 입력 (0: 취소): ")
            if idx is None:
                return

            job = jobs[idx]
            if job.id is not None:
                repo.clear_youtube_id(job.id)
            print(f"\n✅ 업로드 기록 초기화됨: {job.title or job.output_path}")
            print(f"   이전 YouTube ID: {job.youtube_id}")
            print("   이제 다시 업로드할 수 있습니다.")


def resolve_playlist_ids(playlist_args: list[str] | None) -> list[str]:
    """
    플레이리스트 인자 처리.

    우선순위:
    1. --playlist 옵션이 명시적으로 지정됨 → 해당 값 사용
    2. --playlist 옵션 없음 + 환경 변수 설정됨 → 환경 변수 값 사용
    3. 둘 다 없음 → 빈 리스트 (플레이리스트 추가 안 함)

    Args:
        playlist_args: --playlist 인자 값 리스트
            - None: 환경 변수 확인
            - 빈 문자열 포함: 목록에서 선택
            - 기타: 플레이리스트 ID로 사용

    Returns:
        플레이리스트 ID 리스트 (사용 안 함 또는 취소 시 빈 리스트)
    """
    # 환경 변수에서 기본 플레이리스트 확인
    if playlist_args is None:
        env_playlist = os.environ.get(ENV_YOUTUBE_PLAYLIST)
        if env_playlist:
            ids = [pid.strip() for pid in env_playlist.split(",") if pid.strip()]
            if ids:
                logger.info(f"Using playlists from env: {ids}")
                return ids
        return []

    # 빈 문자열이 있으면 선택 모드
    needs_selection = any(arg == "" for arg in playlist_args)
    direct_ids = [arg for arg in playlist_args if arg and arg != ""]

    if needs_selection:
        # 플레이리스트 목록에서 선택
        from tubearchive.youtube.auth import get_authenticated_service
        from tubearchive.youtube.playlist import list_playlists, select_playlist_interactive

        print("\n📋 플레이리스트 목록을 가져오는 중...")
        service = get_authenticated_service()
        playlists = list_playlists(service)

        selected = select_playlist_interactive(playlists)
        if selected:
            for pl in selected:
                print(f"   선택됨: {pl.title}")
            direct_ids.extend([pl.id for pl in selected])

    return direct_ids


def cmd_upload_only(args: argparse.Namespace) -> None:
    """
    --upload-only 옵션 처리.

    Args:
        args: 파싱된 인자
    """
    file_path = Path(args.upload_only)

    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        sys.exit(1)

    # DB에서 MergeJob 조회 (경로로 찾기)
    merge_job_id = None
    description = ""

    try:
        with database_session() as conn:
            merge_job = MergeJobRepository(conn).get_by_output_path(file_path)
            if merge_job:
                merge_job_id = merge_job.id
                if merge_job.summary_markdown:
                    description = merge_job.summary_markdown
                    logger.info("Using summary from database as description")
    except Exception as e:
        logger.warning(f"Failed to load merge job from DB: {e}")

    # 플레이리스트 처리
    playlist_ids = resolve_playlist_ids(args.playlist)

    # 스케줄 처리
    publish_at: str | None = None
    if hasattr(args, "schedule") and args.schedule:
        publish_at = parse_schedule_datetime(args.schedule)

    set_thumbnail = getattr(args, "set_thumbnail", None)
    set_thumbnail_path = _resolve_set_thumbnail_path(set_thumbnail)

    # 업로드 실행
    upload_to_youtube(
        file_path=file_path,
        title=args.upload_title,
        description=description,
        privacy=args.upload_privacy,
        publish_at=publish_at,
        merge_job_id=merge_job_id,
        playlist_ids=playlist_ids,
        chunk_mb=args.upload_chunk,
        thumbnail=set_thumbnail_path,
    )


def cmd_status() -> None:
    """``--status`` 옵션 처리: 전체 작업 현황 출력."""
    with database_session() as conn:
        video_repo = VideoRepository(conn)
        transcoding_repo = TranscodingJobRepository(conn)
        merge_repo = MergeJobRepository(conn)

        print("\n📊 TubeArchive 작업 현황\n")

        # 1. 진행 중인 트랜스코딩 작업
        processing_jobs = transcoding_repo.get_active_with_paths(limit=10)

        if processing_jobs:
            print("🔄 진행 중인 트랜스코딩:")
            print("-" * 70)
            for tc_row in processing_jobs:
                path = Path(tc_row["original_path"]).name
                status = "⏳ 대기" if tc_row["status"] == "pending" else "🔄 진행"
                progress = tc_row["progress_percent"] or 0
                print(f"  {status} [{progress:3d}%] {path}")
            print()

        # 2. 최근 병합 작업
        recent_merge_jobs = merge_repo.get_recent(limit=10)

        if recent_merge_jobs:
            print("📁 최근 병합 작업:")
            print("-" * 90)
            print(f"{'ID':<4} {'상태':<10} {'제목':<25} {'날짜':<12} {'길이':<10} {'YouTube':<12}")
            print("-" * 90)
            for job in recent_merge_jobs:
                title = (job.title or "-")[:23]
                job_date = job.date or "-"
                status_icon = STATUS_ICONS.get(job.status.value, job.status.value)
                duration_str = format_duration(job.total_duration_seconds or 0)
                yt_status = f"✅ {job.youtube_id[:8]}..." if job.youtube_id else "- 미업로드"
                row_str = (
                    f"{job.id:<4} {status_icon:<10} {title:<25} {job_date:<12} {duration_str:<10}"
                )
                print(f"{row_str} {yt_status}")

            print("-" * 90)
        else:
            print("📁 병합 작업 없음\n")

        # 3. 통계 요약
        video_count = video_repo.count_all()
        total_jobs = merge_repo.count_all()
        uploaded_count = merge_repo.count_uploaded()

        print(
            f"\n📈 통계: 영상 {video_count}개 등록"
            f" | 병합 {total_jobs}건 | 업로드 {uploaded_count}건"
        )


def cmd_status_detail(job_id: int) -> None:
    """``--status-detail`` 옵션 처리: 특정 작업의 상세 정보를 출력한다.

    Args:
        job_id: merge_job ID
    """
    with database_session() as conn:
        job = MergeJobRepository(conn).get_by_id(job_id)

        if not job:
            print(f"❌ 작업 ID {job_id}를 찾을 수 없습니다.")
            return

        print(f"\n📋 작업 상세 (ID: {job_id})\n")
        print("=" * 60)

        print(f"📌 제목: {job.title or '-'}")
        print(f"📅 날짜: {job.date or '-'}")
        print(f"📁 출력: {job.output_path}")
        print(f"📊 상태: {STATUS_ICONS.get(job.status.value, job.status.value)}")
        print(f"⏱️  길이: {format_duration(job.total_duration_seconds or 0)}")
        print(f"💾 크기: {format_size(job.total_size_bytes or 0)}")

        if job.youtube_id:
            print(f"🎬 YouTube: https://youtu.be/{job.youtube_id}")
        else:
            print("🎬 YouTube: 미업로드")

        # 클립 정보
        if job.clips_info_json:
            try:
                clips = json.loads(job.clips_info_json)
                print(f"\n📹 클립 ({len(clips)}개):")
                print("-" * 60)
                for i, clip in enumerate(clips, 1):
                    name = clip.get("name", "-")
                    clip_duration = clip.get("duration", 0)
                    device = clip.get("device", "-")
                    shot_time = clip.get("shot_time", "-")
                    print(f"  {i}. {name}")
                    print(f"     기기: {device} | 촬영: {shot_time} | 길이: {clip_duration:.1f}s")
            except json.JSONDecodeError:
                pass

        print("=" * 60)


def _cmd_dry_run(validated_args: ValidatedArgs) -> None:
    """실행 계획만 출력하고 실제 트랜스코딩은 수행하지 않는다.

    입력 파일 목록, 출력 경로, 각종 옵션 설정값을 사람이 읽기 좋은
    형태로 콘솔에 표시한다. ``--dry-run`` 플래그 처리용.

    Args:
        validated_args: 검증된 CLI 인자
    """
    logger.info("Dry run mode - showing execution plan only")

    video_files = scan_videos(validated_args.targets)
    original_count = len(video_files)
    video_files = _apply_ordering(video_files, validated_args, allow_interactive=False)
    output_str = str(_resolve_output_path(validated_args))

    print("\n=== Dry Run Execution Plan ===")
    print(f"Input targets: {[str(t) for t in validated_args.targets]}")

    if original_count != len(video_files):
        print(f"Video files found: {original_count} (filtered to {len(video_files)})")
        if validated_args.exclude_patterns:
            print(f"  Exclude patterns: {validated_args.exclude_patterns}")
        if validated_args.include_only_patterns:
            print(f"  Include-only patterns: {validated_args.include_only_patterns}")
    else:
        print(f"Video files found: {len(video_files)}")

    if validated_args.sort_key != "time":
        print(f"Sort key: {validated_args.sort_key}")

    print_video_list(video_files, header="최종 클립 순서")

    print(f"Output: {output_str}")
    print(f"Temp dir: {get_temp_dir()}")
    print(f"Resume enabled: {not validated_args.no_resume}")
    print(f"Keep temp files: {validated_args.keep_temp}")
    print(f"Parallel workers: {validated_args.parallel}")
    print(f"Denoise enabled: {validated_args.denoise}")
    print(f"Denoise level: {validated_args.denoise_level}")
    print(f"Normalize audio: {validated_args.normalize_audio}")
    print(f"Group sequences: {validated_args.group_sequences}")
    print(f"Fade duration: {validated_args.fade_duration}")
    if validated_args.stabilize:
        strength = validated_args.stabilize_strength
        crop = validated_args.stabilize_crop
        print(f"Stabilize: enabled (strength={strength}, crop={crop})")
    else:
        print("Stabilize: disabled")
    if validated_args.thumbnail:
        print(f"Thumbnail: enabled (quality={validated_args.thumbnail_quality})")
        if validated_args.thumbnail_timestamps:
            print(f"  timestamps: {validated_args.thumbnail_timestamps}")
        else:
            print("  timestamps: auto (10%, 33%, 50%)")
    if validated_args.bgm_path:
        print(f"BGM: {validated_args.bgm_path}")
        print(f"  volume: {validated_args.bgm_volume}")
        print(f"  loop: {validated_args.bgm_loop}")
    print("=" * 30)


def _upload_split_files(
    split_files: list[Path],
    title: str | None,
    clips_info_json: str | None,
    privacy: str,
    merge_job_id: int | None,
    playlist_ids: list[str] | None,
    chunk_mb: int | None,
    split_job_id: int | None = None,
    publish_at: str | None = None,
    thumbnail: Path | None = None,
) -> None:
    """분할 파일을 순차적으로 YouTube에 업로드한다.

    각 파일에 대해 챕터를 리매핑하여 설명을 생성하고,
    제목에 ``(Part N/M)`` 형식을 추가한다.
    썸네일은 모든 파트에 동일하게 적용한다.

    Args:
        split_files: 분할된 파일 경로 목록
        title: 원본 영상 제목 (None이면 파일명 사용)
        clips_info_json: 클립 메타데이터 JSON 문자열
        privacy: 공개 설정
        merge_job_id: MergeJob DB ID
        playlist_ids: 플레이리스트 ID 목록
        chunk_mb: 업로드 청크 크기 MB
        split_job_id: SplitJob DB ID (파트별 youtube_id 저장용)
        publish_at: 예약 공개 시간 (ISO 8601 형식, 설정 시 privacy는 private로 자동 변경)
        thumbnail: 썸네일 이미지 경로
    """
    from tubearchive.utils.summary_generator import (
        generate_split_youtube_description,
    )

    # clips_info_json → ClipInfo 리스트 복원
    video_clips: list[ClipInfo] = []
    if clips_info_json:
        try:
            raw = json.loads(clips_info_json)
            for item in raw:
                video_clips.append(
                    ClipInfo(
                        name=item.get("name", ""),
                        duration=float(item.get("duration", 0)),
                        device=item.get("device"),
                        shot_time=item.get("shot_time"),
                    )
                )
        except (json.JSONDecodeError, KeyError, TypeError):
            logger.warning("Failed to parse clips_info_json for split upload")

    # 각 분할 파일의 실제 길이 조회
    split_durations = [probe_duration(f) for f in split_files]

    total = len(split_files)
    for i, split_file in enumerate(split_files):
        part_title = f"{title} (Part {i + 1}/{total})" if title else None

        # 챕터 리매핑된 설명 생성
        description = ""
        if video_clips and any(d > 0 for d in split_durations):
            try:
                description = generate_split_youtube_description(
                    video_clips=video_clips,
                    split_durations=split_durations,
                    part_index=i,
                )
            except Exception as e:
                logger.warning(f"Failed to generate split description: {e}")

        print(f"\n📤 Part {i + 1}/{total} 업로드: {split_file.name}")
        try:
            # merge_job_id=None: 분할 파트는 merge_job의 youtube_id를 덮어쓰지 않음
            video_id = upload_to_youtube(
                file_path=split_file,
                title=part_title,
                description=description,
                privacy=privacy,
                publish_at=publish_at,
                merge_job_id=None,
                playlist_ids=playlist_ids,
                chunk_mb=chunk_mb,
                thumbnail=thumbnail,
            )
            # 파트별 youtube_id를 split_job에 저장
            if video_id and split_job_id is not None:
                try:
                    with database_session() as conn:
                        split_repo = SplitJobRepository(conn)
                        split_repo.append_youtube_id(split_job_id, video_id)
                except Exception as e:
                    logger.warning(f"Failed to save youtube_id for part {i + 1}: {e}")
        except Exception as e:
            logger.error(f"Part {i + 1}/{total} upload failed: {e}")
            print(f"  ⚠️  Part {i + 1} 업로드 실패: {e}")
            continue


def _get_or_create_project_playlist(
    project_name: str,
    merge_job_id: int,
    privacy: str = "unlisted",
) -> str | None:
    """프로젝트 전용 YouTube 플레이리스트를 조회하거나 생성한다.

    DB에 저장된 playlist_id가 있으면 그대로 사용하고,
    없으면 YouTube에 새 플레이리스트를 생성하여 DB에 저장한다.

    Args:
        project_name: 프로젝트 이름
        merge_job_id: merge_job ID (프로젝트 조회용)
        privacy: 플레이리스트 공개 설정

    Returns:
        플레이리스트 ID 또는 실패 시 None
    """
    from tubearchive.database.repository import ProjectRepository

    try:
        # Phase 1: DB 조회 — 프로젝트와 기존 플레이리스트 확인
        with database_session() as conn:
            repo = ProjectRepository(conn)
            project_ids = repo.get_project_ids_for_merge_job(merge_job_id)
            if not project_ids:
                return None

            project = repo.get_by_id(project_ids[0])
            if project is None or project.id is None:
                return None

            if project.playlist_id:
                logger.info(f"Reusing project playlist: {project.playlist_id}")
                return project.playlist_id

            project_id = project.id

        # Phase 2: YouTube API 호출 — DB 세션 밖에서 네트워크 호출
        from tubearchive.youtube.auth import get_authenticated_service
        from tubearchive.youtube.playlist import create_playlist

        service = get_authenticated_service()
        playlist_id = create_playlist(
            service,
            title=project_name,
            description=f"TubeArchive 프로젝트: {project_name}",
            privacy=privacy,
        )

        # Phase 3: DB 업데이트 — 생성된 플레이리스트 ID 저장
        with database_session() as conn:
            repo = ProjectRepository(conn)
            repo.update_playlist_id(project_id, playlist_id)

        print(f"  📋 프로젝트 플레이리스트 생성됨: {project_name}")
        return playlist_id

    except Exception as e:
        logger.warning(f"Failed to get/create project playlist: {e}")
        return None


def _upload_after_pipeline(
    output_path: Path,
    args: argparse.Namespace,
    notifier: Notifier | None = None,
    publish_at: str | None = None,
    generated_thumbnail_paths: list[Path] | None = None,
    explicit_thumbnail: Path | None = None,
) -> None:
    """파이프라인 완료 후 YouTube 업로드를 수행한다.

    DB에서 최신 merge_job을 조회하여 제목·설명을 가져온 뒤,
    분할 파일이 있으면 순차 업로드, 없으면 단일 업로드한다.

    Args:
        output_path: 업로드할 병합 영상 파일 경로
        args: 원본 CLI 인자 (playlist, upload_privacy, upload_chunk 등)
        notifier: 알림 오케스트레이터 (None이면 알림 비활성화)
        publish_at: 예약 공개 시간 (이미 검증된 값, 재파싱하지 않음)
        generated_thumbnail_paths: 썸네일 후보 경로 목록 (생성된 썸네일)
        explicit_thumbnail: --set-thumbnail에서 지정한 썸네일 경로
    """
    print("\n📤 YouTube 업로드 시작...")

    thumbnail = _resolve_upload_thumbnail(
        explicit_thumbnail=explicit_thumbnail,
        generated_thumbnail_paths=generated_thumbnail_paths,
    )
    if thumbnail is not None:
        logger.info(
            "Using thumbnail for upload: %s",
            getattr(thumbnail, "name", str(thumbnail)),
        )
    else:
        logger.info("No thumbnail selected for upload.")

    merge_job_id = None
    title = None
    description = ""
    clips_info_json: str | None = None
    try:
        with database_session() as conn:
            repo = MergeJobRepository(conn)
            job = repo.get_latest()
            if job:
                merge_job_id = job.id
                title = job.title
                description = job.summary_markdown or ""
                clips_info_json = job.clips_info_json
    except Exception as e:
        logger.warning(f"Failed to get merge job: {e}")

    playlist_ids = resolve_playlist_ids(args.playlist)

    # 프로젝트 플레이리스트 자동 생성/사용
    project_name = getattr(args, "project", None)
    if project_name and merge_job_id is not None:
        project_playlist_id = _get_or_create_project_playlist(
            project_name, merge_job_id, privacy=args.upload_privacy
        )
        if project_playlist_id and project_playlist_id not in playlist_ids:
            playlist_ids.append(project_playlist_id)

    # 분할 파일 확인
    split_files: list[Path] = []
    split_job_id: int | None = None
    if merge_job_id is not None:
        try:
            with database_session() as conn:
                split_repo = SplitJobRepository(conn)
                split_jobs = split_repo.get_by_merge_job_id(merge_job_id)
                for sj in split_jobs:
                    existing = [f for f in sj.output_files if f.exists()]
                    if existing:
                        split_files.extend(existing)
                        split_job_id = sj.id
        except Exception as e:
            logger.warning(f"Failed to get split jobs: {e}")

    if split_files:
        _upload_split_files(
            split_files=split_files,
            title=title,
            clips_info_json=clips_info_json,
            privacy=args.upload_privacy,
            merge_job_id=merge_job_id,
            playlist_ids=playlist_ids,
            chunk_mb=args.upload_chunk,
            split_job_id=split_job_id,
            publish_at=publish_at,
            thumbnail=thumbnail,
        )
    else:
        upload_to_youtube(
            file_path=output_path,
            title=title,
            description=description,
            privacy=args.upload_privacy,
            publish_at=publish_at,
            merge_job_id=merge_job_id,
            playlist_ids=playlist_ids,
            chunk_mb=args.upload_chunk,
            thumbnail=thumbnail,
        )

    # 알림: 업로드 완료
    if notifier:
        from tubearchive.notification import upload_complete_event

        # DB에서 youtube_id 조회
        youtube_id = ""
        if merge_job_id is not None:
            try:
                with database_session() as conn:
                    repo = MergeJobRepository(conn)
                    job = repo.get_by_id(merge_job_id)
                    if job and job.youtube_id:
                        youtube_id = job.youtube_id
            except Exception:
                logger.debug("알림용 youtube_id 조회 실패", exc_info=True)
        notifier.notify(
            upload_complete_event(
                video_title=title or output_path.stem,
                youtube_id=youtube_id,
            )
        )


def cmd_init_config() -> None:
    """
    --init-config 옵션 처리.

    기본 설정 파일(config.toml) 템플릿을 생성합니다.
    """
    from tubearchive.config import generate_default_config, get_default_config_path

    config_path = get_default_config_path()

    if config_path.exists():
        response = safe_input(f"이미 존재합니다: {config_path}\n덮어쓰시겠습니까? (y/N): ")
        if response.lower() not in ("y", "yes"):
            print("취소됨")
            return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(generate_default_config())
    print(f"설정 파일 생성됨: {config_path}")


def main() -> None:
    """CLI 진입점.

    인자를 파싱하고 설정 파일을 로드한 뒤, 요청된 서브커맨드를
    적절한 핸들러 함수로 라우팅한다. 서브커맨드가 지정되지 않은
    기본 동작은 :func:`run_pipeline` (트랜스코딩 + 병합).
    """
    parser = create_parser()
    args = parser.parse_args()

    # --init-config 처리 (가장 먼저, 로깅/설정 로드 전)
    if args.init_config:
        cmd_init_config()
        return

    # 설정 파일 로드 및 환경변수 적용
    from tubearchive.config import apply_config_to_env, load_config

    config_path = Path(args.config) if args.config else None
    config = load_config(config_path)
    apply_config_to_env(config)

    # --notify-test 처리 (서브커맨드 전)
    if getattr(args, "notify_test", False):
        setup_logging(args.verbose)
        from tubearchive.notification import Notifier as _Notifier

        test_notifier = _Notifier(config.notification)
        if not test_notifier.has_providers:
            print("활성화된 알림 채널이 없습니다.")
            print("config.toml의 [notification] 섹션을 확인하세요.")
            return
        results = test_notifier.test_notification()
        for provider_name, success in results.items():
            icon = "OK" if success else "FAIL"
            status = "성공" if success else "실패"
            print(f"  [{icon}] {provider_name}: {status}")
        return

    # upload_privacy: CLI > config > "unlisted"
    if args.upload_privacy is None:
        args.upload_privacy = config.youtube.upload_privacy or "unlisted"

    setup_logging(args.verbose)

    notifier: Notifier | None = None

    try:
        # --setup-youtube 옵션 처리 (설정 가이드)
        if args.setup_youtube:
            cmd_setup_youtube()
            return

        # --youtube-auth 옵션 처리 (브라우저 인증)
        if args.youtube_auth:
            cmd_youtube_auth()
            return

        # --list-playlists 옵션 처리 (플레이리스트 목록)
        if args.list_playlists:
            cmd_list_playlists()
            return

        # --reset-build 옵션 처리 (빌드 기록 초기화)
        if args.reset_build is not None:
            cmd_reset_build(args.reset_build)
            return

        # --reset-upload 옵션 처리 (업로드 기록 초기화)
        if args.reset_upload is not None:
            cmd_reset_upload(args.reset_upload)
            return

        # --project-list 옵션 처리 (프로젝트 목록 조회)
        if args.project_list:
            from tubearchive.commands.project import cmd_project_list

            cmd_project_list(output_json=args.json)
            return

        # --project-detail 옵션 처리 (프로젝트 상세 조회)
        if args.project_detail is not None:
            from tubearchive.commands.project import cmd_project_detail

            cmd_project_detail(args.project_detail, output_json=args.json)
            return

        # --status-detail 옵션 처리 (작업 상세 조회)
        if args.status_detail is not None:
            cmd_status_detail(args.status_detail)
            return

        # --status 옵션 처리 (작업 현황 조회)
        if args.status == CATALOG_STATUS_SENTINEL:
            cmd_status()
            return

        # --period 단독 사용 경고
        if args.period and not args.stats:
            logger.warning("--period 옵션은 --stats와 함께 사용해야 합니다.")

        # --stats 옵션 처리 (통계 대시보드)
        if args.stats:
            from tubearchive.commands.stats import cmd_stats as _cmd_stats

            with database_session() as conn:
                _cmd_stats(conn, period=args.period)
            return

        # --catalog / --search 옵션 처리 (메타데이터 조회)
        if (args.json or args.csv) and not (
            args.catalog
            or args.search is not None
            or args.device is not None
            or normalize_status_filter(args.status) is not None
        ):
            raise ValueError("--json/--csv 옵션은 --catalog 또는 --search와 함께 사용하세요.")

        if (
            args.catalog
            or args.search is not None
            or args.device is not None
            or normalize_status_filter(args.status) is not None
        ):
            cmd_catalog(args)
            return

        # --upload-only 옵션 처리 (업로드만)
        if args.upload_only:
            cmd_upload_only(args)
            return

        # config의 device_luts를 validate_args에 전달하여 초기화 시 주입
        cfg_device_luts = config.color_grading.device_luts or None
        validated_args = validate_args(args, device_luts=cfg_device_luts)

        if validated_args.dry_run:
            _cmd_dry_run(validated_args)
            return

        # Notifier 초기화
        if validated_args.notify:
            from tubearchive.notification import Notifier as _Notifier

            notifier = _Notifier(config.notification)
            if notifier.has_providers:
                logger.info("알림 시스템 활성화 (%d개 채널)", notifier.provider_count)

        pipeline_generated_thumbnail_paths: list[Path] = []
        output_path = run_pipeline(
            validated_args,
            notifier=notifier,
            generated_thumbnail_paths=pipeline_generated_thumbnail_paths,
        )
        print("\n✅ 완료!")
        print(f"📹 출력 파일: {output_path}")

        if validated_args.upload:
            _upload_after_pipeline(
                output_path,
                args,
                notifier=notifier,
                publish_at=validated_args.schedule,
                generated_thumbnail_paths=pipeline_generated_thumbnail_paths,
                explicit_thumbnail=validated_args.set_thumbnail,
            )

    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        # 에러 알림
        if notifier is not None:
            from tubearchive.notification import error_event

            notifier.notify(error_event(error_message=str(e), stage="pipeline"))
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
