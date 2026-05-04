"""CLI 파서 모듈 — argparse 파서 생성 및 스케줄 datetime 파싱."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime

from tubearchive import __version__
from tubearchive.app.queries.catalog import CATALOG_STATUS_SENTINEL
from tubearchive.config import (
    ENV_FADE_DURATION,
    ENV_GROUP_SEQUENCES,
    ENV_OUTPUT_DIR,
    ENV_PARALLEL,
    ENV_YOUTUBE_PLAYLIST,
)
from tubearchive.domain.media.ordering import SortKey
from tubearchive.domain.media.subtitle import (
    SUPPORTED_SUBTITLE_FORMATS,
    SUPPORTED_SUBTITLE_MODELS,
    SubtitleFormat,
    SubtitleModel,
)

logger = logging.getLogger(__name__)


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

    parser.add_argument(
        "--watch",
        action="append",
        default=None,
        metavar="PATH",
        help=(
            "감시 대상 디렉토리 지정 (반복 사용 가능). 지정하지 않으면 "
            "config.toml [watch].paths 사용"
        ),
    )

    parser.add_argument(
        "--watch-log",
        type=str,
        default=None,
        metavar="PATH",
        help="watch 모드 로그 파일 경로 (config.toml [watch].log_path/환경변수 대체)",
    )

    # YouTube 업로드 옵션
    parser.add_argument(
        "--upload",
        action="store_true",
        help="병합 완료 후 YouTube에 업로드",
    )

    parser.add_argument(
        "--run-hook",
        choices=["on_transcode", "on_merge", "on_upload", "on_error"],
        help="설정 파일([hooks]) 이벤트 훅 수동 실행 (on_transcode/on_merge/on_upload/on_error)",
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

    # 자막 생성/하드코딩 옵션
    parser.add_argument(
        "--subtitle",
        action="store_true",
        help="병합 영상 자막 생성",
    )

    parser.add_argument(
        "--subtitle-model",
        type=SubtitleModel,
        default=None,
        choices=list(SUPPORTED_SUBTITLE_MODELS),
        help="Whisper 모델 (tiny/base/small/medium/large, 기본: config/env/기본값)",
    )

    parser.add_argument(
        "--subtitle-format",
        type=SubtitleFormat,
        default=None,
        choices=list(SUPPORTED_SUBTITLE_FORMATS),
        help="자막 출력 포맷 (srt/vtt, 기본: config/env/기본값)",
    )

    parser.add_argument(
        "--subtitle-lang",
        type=str,
        default=None,
        metavar="LANG",
        help="자막 언어 코드 (예: en, ko). 미지정 시 자동 감지",
    )

    parser.add_argument(
        "--subtitle-burn",
        action="store_true",
        help="자막을 영상에 하드코딩 (ffmpeg subtitles 필터)",
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
        "--no-fade",
        action="store_true",
        default=False,
        help="영상 간 페이드 효과 비활성화 (--fade-duration 0과 동일)",
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
        "--template-intro",
        type=str,
        default=None,
        metavar="PATH",
        help="병합 맨 앞에 붙일 템플릿 파일 경로",
    )

    parser.add_argument(
        "--template-outro",
        type=str,
        default=None,
        metavar="PATH",
        help="병합 맨 뒤에 붙일 템플릿 파일 경로",
    )

    parser.add_argument(
        "--watermark",
        action="store_true",
        help="트랜스코딩 영상을 워터마크 텍스트로 오버레이",
    )

    parser.add_argument(
        "--watermark-pos",
        type=str,
        default="bottom-right",
        choices=[
            "top-left",
            "top-right",
            "bottom-left",
            "bottom-right",
            "center",
        ],
        help="워터마크 오버레이 위치 (기본: bottom-right)",
    )

    parser.add_argument(
        "--watermark-size",
        type=int,
        default=48,
        help="워터마크 글자 크기 (기본: 48)",
    )

    parser.add_argument(
        "--watermark-color",
        type=str,
        default="white",
        help="워터마크 글자 색상 (기본: white)",
    )

    parser.add_argument(
        "--watermark-alpha",
        type=float,
        default=0.85,
        help="워터마크 투명도 (0.0~1.0, 기본: 0.85)",
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
        "--fix-device-models",
        action="store_true",
        help="DB에서 device_model이 비어 있는 영상을 재스캔하여 기기 모델을 채운다",
    )

    # 마이그레이션 옵션
    parser.add_argument(
        "--export-db",
        type=str,
        default=None,
        metavar="FILE",
        help="DB 전체를 JSON 파일로 내보낸다 (예: ~/backup.json)",
    )

    parser.add_argument(
        "--import-db",
        type=str,
        default=None,
        metavar="FILE",
        help="JSON 파일에서 DB로 데이터를 가져온다 (예: ~/backup.json)",
    )

    parser.add_argument(
        "--src-prefix",
        type=str,
        default=None,
        metavar="PATH",
        help="--import-db 경로 remapping: 원본 접두사 (예: /Users/old)",
    )

    parser.add_argument(
        "--dst-prefix",
        type=str,
        default=None,
        metavar="PATH",
        help="--import-db 경로 remapping: 대상 접두사 (예: /Users/new)",
    )

    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="--import-db 충돌 시 기존 레코드를 덮어쓴다 (기본: skip)",
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

    # 클라우드 백업 옵션
    parser.add_argument(
        "--backup",
        type=str,
        default=None,
        metavar="REMOTE",
        help="병합 결과를 백업할 대상 remote (rclone 대상: 예: s3:bucket/path)",
    )
    parser.add_argument(
        "--backup-all",
        action="store_true",
        help="원본 파일까지 함께 백업 (템플릿 포함, 기본: 결과물만)",
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
