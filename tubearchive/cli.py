"""CLI 인터페이스."""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from threading import Lock

try:
    import termios

    HAS_TERMIOS = True
except ImportError:
    HAS_TERMIOS = False

from tubearchive import __version__
from tubearchive.core.detector import detect_metadata
from tubearchive.core.merger import Merger
from tubearchive.core.scanner import scan_videos
from tubearchive.core.transcoder import Transcoder
from tubearchive.database.repository import MergeJobRepository
from tubearchive.database.schema import init_database
from tubearchive.models.video import VideoFile
from tubearchive.utils.progress import MultiProgressBar, ProgressInfo
from tubearchive.utils.summary_generator import generate_single_file_description

logger = logging.getLogger(__name__)


def safe_input(prompt: str) -> str:
    """
    터미널 상태를 복원하고 안전하게 입력 받기.

    Args:
        prompt: 입력 프롬프트

    Returns:
        사용자 입력 (strip 적용)
    """
    # 터미널 상태 복원 시도
    if HAS_TERMIOS and sys.stdin.isatty():
        try:
            # 현재 터미널 설정 저장
            fd = sys.stdin.fileno()
            old_settings = termios.tcgetattr(fd)
            # cooked 모드로 복원 (일반 라인 입력 모드)
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except (termios.error, OSError):
            pass

    sys.stdout.write(prompt)
    sys.stdout.flush()

    try:
        line = sys.stdin.readline()
        return line.strip().replace("\r", "")
    except (EOFError, KeyboardInterrupt):
        return ""


# 환경 변수
ENV_OUTPUT_DIR = "TUBEARCHIVE_OUTPUT_DIR"
ENV_YOUTUBE_PLAYLIST = "TUBEARCHIVE_YOUTUBE_PLAYLIST"
ENV_PARALLEL = "TUBEARCHIVE_PARALLEL"

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


def get_default_output_dir() -> Path | None:
    """환경 변수에서 기본 출력 디렉토리 가져오기."""
    env_dir = os.environ.get(ENV_OUTPUT_DIR)
    if env_dir:
        path = Path(env_dir)
        if path.is_dir():
            return path
        logger.warning(f"{ENV_OUTPUT_DIR}={env_dir} is not a valid directory")
    return None


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


def get_default_parallel() -> int:
    """환경 변수에서 기본 병렬 처리 수 가져오기."""
    env_parallel = os.environ.get(ENV_PARALLEL)
    if env_parallel:
        try:
            val = int(env_parallel)
            if val >= 1:
                return val
            logger.warning(f"{ENV_PARALLEL}={env_parallel} must be >= 1, using 1")
        except ValueError:
            logger.warning(f"{ENV_PARALLEL}={env_parallel} is not a valid number")
    return 1  # 기본값: 순차 처리


@dataclass
class ValidatedArgs:
    """검증된 CLI 인자."""

    targets: list[Path]
    output: Path | None
    output_dir: Path | None
    no_resume: bool
    keep_temp: bool
    dry_run: bool
    upload: bool = False
    parallel: int = 1


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
        default="unlisted",
        choices=["public", "unlisted", "private"],
        help="YouTube 공개 설정 (기본: unlisted)",
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

    parser.add_argument(
        "--status",
        action="store_true",
        help="작업 현황 조회 (트랜스코딩, 병합, 업로드)",
    )

    parser.add_argument(
        "--status-detail",
        type=int,
        metavar="ID",
        default=None,
        help="특정 작업 상세 조회 (merge_job ID)",
    )

    return parser


def validate_args(args: argparse.Namespace) -> ValidatedArgs:
    """
    CLI 인자 검증.

    Args:
        args: 파싱된 인자

    Returns:
        검증된 인자

    Raises:
        FileNotFoundError: 파일/디렉토리가 존재하지 않는 경우
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

    return ValidatedArgs(
        targets=targets,
        output=output,
        output_dir=output_dir,
        no_resume=args.no_resume,
        keep_temp=args.keep_temp,
        dry_run=args.dry_run,
        upload=upload,
        parallel=parallel,
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
    if first_target.is_dir():
        # 디렉토리면 디렉토리명 사용
        name = first_target.name
    else:
        # 파일이면 부모 디렉토리명 사용
        name = first_target.parent.name

    # 빈 이름이거나 현재 디렉토리면 기본값
    if not name or name == ".":
        name = "output"

    return f"{name}.mp4"


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
    clip_info: dict[str, str | float | None] = {
        "name": video_file.path.name,
        "duration": metadata.duration_seconds,
        "start": 0.0,
        "end": metadata.duration_seconds,
        "device": metadata.device_model or "Unknown",
        "shot_time": creation_time_str,
    }

    # 5. YouTube 설명 생성 (단일 파일용)
    youtube_description = generate_single_file_description(clip_info)

    # 6. DB 저장
    conn = init_database()
    repo = MergeJobRepository(conn)
    today = date.today().isoformat()

    repo.create(
        output_path=video_file.path,
        video_ids=[],  # 트랜스코딩 안 함
        title=title,
        date=today,
        total_duration_seconds=metadata.duration_seconds,
        total_size_bytes=video_file.path.stat().st_size,
        clips_info_json=json.dumps([clip_info]),
        summary_markdown=youtube_description,
    )
    conn.close()

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


def _transcode_single(
    vf: VideoFile,
    temp_dir: Path,
    index: int,
) -> tuple[int, Path, int, tuple[str, float, str | None, str | None]]:
    """
    단일 파일 트랜스코딩 (병렬 처리용).

    Args:
        vf: VideoFile 객체
        temp_dir: 임시 디렉토리
        index: 파일 인덱스 (순서 유지용)

    Returns:
        (인덱스, 출력 경로, video_id, 클립 정보) 튜플
    """

    with Transcoder(temp_dir=temp_dir) as transcoder:
        output_path, video_id = transcoder.transcode_video(vf)

        # 메타데이터 수집 (Summary용)
        clip_info: tuple[str, float, str | None, str | None]
        try:
            metadata = detect_metadata(vf.path)
            creation_time_str = vf.creation_time.strftime("%H:%M:%S")
            clip_info = (
                vf.path.name,
                metadata.duration_seconds,
                metadata.device_model,
                creation_time_str,
            )
        except Exception as e:
            logger.warning(f"Failed to get metadata for {vf.path}: {e}")
            clip_info = (vf.path.name, 0.0, None, None)

        return index, output_path, video_id, clip_info


def run_pipeline(validated_args: ValidatedArgs) -> Path:
    """
    전체 파이프라인 실행.

    Args:
        validated_args: 검증된 인자

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
    for vf in video_files:
        logger.info(f"  - {vf.path.name}")

    # 단일 파일 + --upload 시 빠른 경로 (인코딩/병합 건너뛰기)
    if len(video_files) == 1 and validated_args.upload:
        return handle_single_file_upload(video_files[0], validated_args)

    # 2. 트랜스코딩 (임시 파일은 /tmp에 저장)
    temp_dir = get_temp_dir()
    logger.info(f"Using temp directory: {temp_dir}")

    parallel = validated_args.parallel
    if parallel > 1:
        logger.info(f"Starting parallel transcoding (workers: {parallel})...")
    else:
        logger.info("Starting transcoding...")

    # 결과 저장용 (인덱스로 순서 유지): (출력 경로, video_id, 클립 정보)
    results: dict[int, tuple[Path, int, tuple[str, float, str | None, str | None]]] = {}

    if parallel > 1:
        # 병렬 처리
        completed_count = 0
        total_count = len(video_files)
        print_lock = Lock()

        def print_progress(idx: int, filename: str, status: str) -> None:
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

        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {
                executor.submit(_transcode_single, vf, temp_dir, i): i
                for i, vf in enumerate(video_files)
            }

            for future in as_completed(futures):
                try:
                    idx, output_path, video_id, clip_info = future.result()
                    results[idx] = (output_path, video_id, clip_info)
                    print_progress(idx, video_files[idx].path.name, "완료")
                except Exception as e:
                    idx = futures[future]
                    logger.error(f"Failed to transcode {video_files[idx].path}: {e}")
                    print_progress(idx, video_files[idx].path.name, "실패")
                    raise

    else:
        # 순차 처리 (기존 방식)
        progress = MultiProgressBar(total_files=len(video_files))

        with Transcoder(temp_dir=temp_dir) as transcoder:
            for i, vf in enumerate(video_files):
                progress.start_file(vf.path.name)

                # 상세 진행률 콜백
                def on_progress_info(info: ProgressInfo) -> None:
                    progress.update_with_info(info)

                output_path, video_id = transcoder.transcode_video(
                    vf,
                    progress_info_callback=on_progress_info,
                )

                # 메타데이터 수집 (Summary용)
                try:
                    metadata = detect_metadata(vf.path)
                    creation_time_str = vf.creation_time.strftime("%H:%M:%S")
                    clip_info = (
                        vf.path.name,
                        metadata.duration_seconds,
                        metadata.device_model,
                        creation_time_str,
                    )
                except Exception as e:
                    logger.warning(f"Failed to get metadata for {vf.path}: {e}")
                    clip_info = (vf.path.name, 0.0, None, None)

                results[i] = (output_path, video_id, clip_info)
                progress.finish_file()

    # 인덱스 순서대로 결과 정렬
    transcoded_paths: list[Path] = []
    video_ids: list[int] = []
    video_clips: list[tuple[str, float, str | None, str | None]] = []
    for i in range(len(video_files)):
        output_path, video_id, clip_info = results[i]
        transcoded_paths.append(output_path)
        video_ids.append(video_id)
        video_clips.append(clip_info)

    # 3. 병합
    logger.info("Merging videos...")

    # 출력 파일 경로 결정
    if validated_args.output:
        output_path = validated_args.output
    else:
        output_filename = get_output_filename(validated_args.targets)
        output_dir = validated_args.output_dir or Path.cwd()
        output_path = output_dir / output_filename

    merger = Merger(temp_dir=temp_dir)
    final_path = merger.merge(transcoded_paths, output_path)

    logger.info(f"Final output: {final_path}")

    # 4. DB에 타임라인 정보 저장 및 Summary 생성
    summary_markdown = save_merge_job_to_db(
        final_path, video_clips, validated_args.targets, video_ids
    )

    # 5. 임시 파일 및 폴더 정리
    if not validated_args.keep_temp:
        logger.info("Cleaning up temporary files...")
        for temp_path in transcoded_paths:
            if temp_path.exists() and temp_path != final_path:
                temp_path.unlink()
                logger.debug(f"  Removed: {temp_path}")

        # 임시 폴더 삭제 (비어있거나 concat 파일만 남은 경우)
        if temp_dir.exists():
            try:
                shutil.rmtree(temp_dir)
                logger.info(f"Removed temp directory: {temp_dir}")
            except OSError as e:
                logger.warning(f"Failed to remove temp directory: {e}")

    # 6. Summary 출력 (복사해서 바로 사용 가능)
    if summary_markdown:
        print("\n" + "=" * 60)
        print("📋 SUMMARY (Copy & Paste)")
        print("=" * 60)
        print(summary_markdown)
        print("=" * 60 + "\n")

    return final_path


def save_merge_job_to_db(
    output_path: Path,
    video_clips: list[tuple[str, float, str | None, str | None]],
    targets: list[Path],
    video_ids: list[int],
) -> str | None:
    """
    병합 작업 정보를 DB에 저장 (타임라인 및 Summary 포함).

    Args:
        output_path: 출력 파일 경로
        video_clips: (파일명, 재생시간, 기종, 촬영시간) 튜플 리스트
        targets: 대상 경로 목록
        video_ids: 병합된 영상들의 DB ID 목록
        targets: 입력 타겟 목록 (제목 추출용)

    Returns:
        생성된 Summary 마크다운 (실패 시 None)
    """
    from tubearchive.utils.summary_generator import (
        generate_clip_summary,
        generate_youtube_description,
    )

    try:
        conn = init_database()
        repo = MergeJobRepository(conn)

        # 타임라인 정보 생성 (각 클립의 메타데이터 포함)
        timeline: list[dict[str, str | float | None]] = []
        current_time = 0.0
        for name, duration, device, shot_time in video_clips:
            timeline.append(
                {
                    "name": name,
                    "duration": duration,
                    "start": current_time,
                    "end": current_time + duration,
                    "device": device,
                    "shot_time": shot_time,
                }
            )
            current_time += duration

        clips_json = json.dumps(timeline, ensure_ascii=False)

        # 제목: 디렉토리명
        title = None
        if targets:
            first_target = targets[0]
            if first_target.is_dir():
                title = first_target.name
            else:
                title = first_target.parent.name
            if not title or title == ".":
                title = output_path.stem

        # 날짜: 오늘
        today = date.today().isoformat()

        # 총 재생시간 및 파일 크기
        total_duration = sum(d for _, d, _, _ in video_clips)
        total_size = output_path.stat().st_size if output_path.exists() else 0

        # 콘솔 출력용 요약 (마크다운 형식)
        console_summary = generate_clip_summary(video_clips)

        # YouTube 설명용 (타임스탬프 + 촬영기기)
        youtube_description = generate_youtube_description(video_clips)

        repo.create(
            output_path=output_path,
            video_ids=video_ids,
            title=title,
            date=today,
            total_duration_seconds=total_duration,
            total_size_bytes=total_size,
            clips_info_json=clips_json,
            summary_markdown=youtube_description,  # YouTube 설명용으로 저장
        )
        conn.close()
        logger.debug("Merge job saved to database with summary")
        return console_summary  # 콘솔에는 상세 요약 출력

    except Exception as e:
        logger.warning(f"Failed to save merge job to DB: {e}")
        return None


def upload_to_youtube(
    file_path: Path,
    title: str | None = None,
    description: str = "",
    privacy: str = "unlisted",
    merge_job_id: int | None = None,
    playlist_ids: list[str] | None = None,
    chunk_mb: int | None = None,
) -> None:
    """
    영상을 YouTube에 업로드.

    Args:
        file_path: 업로드할 영상 파일 경로
        title: 영상 제목 (None이면 파일명 사용)
        description: 영상 설명
        privacy: 공개 설정 (public, unlisted, private)
        merge_job_id: DB에 저장할 MergeJob ID
        playlist_ids: 추가할 플레이리스트 ID 리스트 (None이면 추가 안 함)
        chunk_mb: 업로드 청크 크기 MB (None이면 환경변수/기본값)
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
                return
        except KeyboardInterrupt:
            print("\n업로드가 취소되었습니다.")
            return

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
            on_progress=on_progress,
        )

        print("\n✅ YouTube 업로드 완료!")
        print(f"🎬 URL: {result.url}")

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
                conn = init_database()
                repo = MergeJobRepository(conn)
                repo.update_youtube_id(merge_job_id, result.video_id)
                conn.close()
                logger.debug(f"YouTube ID {result.video_id} saved to merge job {merge_job_id}")
            except Exception as e:
                logger.warning(f"Failed to save YouTube ID to DB: {e}")

    except YouTubeAuthError as e:
        logger.error(f"YouTube authentication failed: {e}")
        print(f"\n❌ YouTube 인증 실패: {e}")
        print("\n설정 가이드: tubearchive --setup-youtube")
        raise
    except YouTubeUploadError as e:
        logger.error(f"YouTube upload failed: {e}")
        print(f"\n❌ YouTube 업로드 실패: {e}")
        raise


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


def cmd_reset_build(path_arg: str) -> None:
    """
    --reset-build 옵션 처리.

    병합 기록을 삭제하여 다시 빌드할 수 있도록 합니다.

    Args:
        path_arg: 파일 경로 (빈 문자열이면 목록에서 선택)
    """
    conn = init_database()
    repo = MergeJobRepository(conn)

    if path_arg:
        # 경로가 지정된 경우 해당 경로의 레코드 삭제
        target_path = Path(path_arg).resolve()
        deleted = repo.delete_by_output_path(target_path)
        if deleted > 0:
            print(f"✅ 빌드 기록 삭제됨: {target_path}")
            print("   이제 다시 빌드할 수 있습니다.")
        else:
            print(f"⚠️ 해당 경로의 기록이 없습니다: {target_path}")
    else:
        # 목록에서 선택
        jobs = repo.get_all()
        if not jobs:
            print("📋 빌드 기록이 없습니다.")
            conn.close()
            return

        print("\n📋 빌드 기록 목록")
        print("=" * 80)
        print(f"{'번호':<4} {'제목':<30} {'날짜':<12} {'YouTube':<10} 경로")
        print("-" * 80)
        for i, job in enumerate(jobs, 1):
            title = (job.title or "-")[:28]
            date = job.date or "-"
            yt_status = "✅ 업로드됨" if job.youtube_id else "-"
            path = str(job.output_path)
            if len(path) > 40:
                path = "..." + path[-37:]
            print(f"{i:<4} {title:<30} {date:<12} {yt_status:<10} {path}")
        print("=" * 80)

        try:
            choice = safe_input("\n삭제할 번호 입력 (0: 취소): ")
            if not choice or choice == "0":
                print("취소됨")
                conn.close()
                return

            idx = int(choice) - 1
            if 0 <= idx < len(jobs):
                job = jobs[idx]
                if job.id is not None:
                    repo.delete(job.id)
                print(f"\n✅ 빌드 기록 삭제됨: {job.title or job.output_path}")
                print("   이제 다시 빌드할 수 있습니다.")
            else:
                print("잘못된 번호입니다.")
        except ValueError:
            print("숫자를 입력해주세요.")
        except KeyboardInterrupt:
            print("\n취소됨")

    conn.close()


def cmd_reset_upload(path_arg: str) -> None:
    """
    --reset-upload 옵션 처리.

    YouTube 업로드 기록을 초기화하여 다시 업로드할 수 있도록 합니다.

    Args:
        path_arg: 파일 경로 (빈 문자열이면 목록에서 선택)
    """
    conn = init_database()
    repo = MergeJobRepository(conn)

    if path_arg:
        # 경로가 지정된 경우 해당 경로의 레코드 초기화
        target_path = Path(path_arg).resolve()
        cursor = conn.execute(
            "SELECT id, youtube_id FROM merge_jobs WHERE output_path = ?",
            (str(target_path),),
        )
        row = cursor.fetchone()
        if row and row["youtube_id"]:
            repo.clear_youtube_id(row["id"])
            print(f"✅ 업로드 기록 초기화됨: {target_path}")
            print(f"   이전 YouTube ID: {row['youtube_id']}")
            print("   이제 다시 업로드할 수 있습니다.")
        elif row:
            print(f"⚠️ 이미 업로드 기록이 없습니다: {target_path}")
        else:
            print(f"⚠️ 해당 경로의 기록이 없습니다: {target_path}")
    else:
        # 업로드된 목록에서 선택
        jobs = repo.get_uploaded()
        if not jobs:
            print("📋 업로드된 영상이 없습니다.")
            conn.close()
            return

        print("\n📋 업로드된 영상 목록")
        print("=" * 90)
        print(f"{'번호':<4} {'제목':<30} {'날짜':<12} {'YouTube ID':<15} 경로")
        print("-" * 90)
        for i, job in enumerate(jobs, 1):
            title = (job.title or "-")[:28]
            date = job.date or "-"
            yt_id = job.youtube_id or "-"
            path = str(job.output_path)
            if len(path) > 30:
                path = "..." + path[-27:]
            print(f"{i:<4} {title:<30} {date:<12} {yt_id:<15} {path}")
        print("=" * 90)

        try:
            choice = safe_input("\n초기화할 번호 입력 (0: 취소): ")
            if not choice or choice == "0":
                print("취소됨")
                conn.close()
                return

            idx = int(choice) - 1
            if 0 <= idx < len(jobs):
                job = jobs[idx]
                if job.id is not None:
                    repo.clear_youtube_id(job.id)
                print(f"\n✅ 업로드 기록 초기화됨: {job.title or job.output_path}")
                print(f"   이전 YouTube ID: {job.youtube_id}")
                print("   이제 다시 업로드할 수 있습니다.")
            else:
                print("잘못된 번호입니다.")
        except ValueError:
            print("숫자를 입력해주세요.")
        except KeyboardInterrupt:
            print("\n취소됨")

    conn.close()


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
        conn = init_database()

        # 최신 MergeJob에서 일치하는 경로 찾기
        cursor = conn.execute(
            """SELECT id, summary_markdown FROM merge_jobs
            WHERE output_path = ? ORDER BY created_at DESC LIMIT 1""",
            (str(file_path),),
        )
        row = cursor.fetchone()
        if row:
            merge_job_id = row["id"]
            # description이 비어있으면 summary_markdown 사용
            if row["summary_markdown"]:
                description = row["summary_markdown"]
                logger.info("Using summary from database as description")

        conn.close()
    except Exception as e:
        logger.warning(f"Failed to load merge job from DB: {e}")

    # 플레이리스트 처리
    playlist_ids = resolve_playlist_ids(args.playlist)

    # 업로드 실행
    upload_to_youtube(
        file_path=file_path,
        title=args.upload_title,
        description=description,
        privacy=args.upload_privacy,
        merge_job_id=merge_job_id,
        playlist_ids=playlist_ids,
        chunk_mb=args.upload_chunk,
    )


def cmd_status() -> None:
    """
    --status 옵션 처리.

    작업 현황을 조회하여 출력합니다.
    """
    conn = init_database()

    print("\n📊 TubeArchive 작업 현황\n")

    # 1. 진행 중인 트랜스코딩 작업
    cursor = conn.execute("""
        SELECT tj.id, tj.status, tj.progress_percent, v.original_path
        FROM transcoding_jobs tj
        JOIN videos v ON tj.video_id = v.id
        WHERE tj.status IN ('pending', 'processing')
        ORDER BY tj.created_at DESC
        LIMIT 10
    """)
    processing_jobs = cursor.fetchall()

    if processing_jobs:
        print("🔄 진행 중인 트랜스코딩:")
        print("-" * 70)
        for job in processing_jobs:
            path = Path(job["original_path"]).name
            status = "⏳ 대기" if job["status"] == "pending" else "🔄 진행"
            progress = job["progress_percent"] or 0
            print(f"  {status} [{progress:3d}%] {path}")
        print()

    # 2. 최근 병합 작업
    cursor = conn.execute("""
        SELECT id, title, date, status, youtube_id, output_path,
               total_duration_seconds, total_size_bytes, created_at
        FROM merge_jobs
        ORDER BY created_at DESC
        LIMIT 10
    """)
    merge_jobs = cursor.fetchall()

    if merge_jobs:
        print("📁 최근 병합 작업:")
        print("-" * 90)
        print(f"{'ID':<4} {'상태':<10} {'제목':<25} {'날짜':<12} {'길이':<10} {'YouTube':<12}")
        print("-" * 90)
        for job in merge_jobs:
            job_id = job["id"]
            title = (job["title"] or "-")[:23]
            date = job["date"] or "-"
            status = job["status"]

            # 상태 아이콘
            status_icon = {
                "pending": "⏳ 대기",
                "processing": "🔄 진행",
                "completed": "✅ 완료",
                "failed": "❌ 실패",
            }.get(status, status)

            # 길이 포맷
            duration = job["total_duration_seconds"] or 0
            if duration >= 3600:
                duration_str = f"{int(duration // 3600)}h {int((duration % 3600) // 60)}m"
            elif duration >= 60:
                duration_str = f"{int(duration // 60)}m {int(duration % 60)}s"
            else:
                duration_str = f"{int(duration)}s"

            # YouTube 상태
            if job["youtube_id"]:
                yt_status = f"✅ {job['youtube_id'][:8]}..."
            else:
                yt_status = "- 미업로드"

            row = f"{job_id:<4} {status_icon:<10} {title:<25} {date:<12} {duration_str:<10}"
            print(f"{row} {yt_status}")

        print("-" * 90)
    else:
        print("📁 병합 작업 없음\n")

    # 3. 통계 요약
    cursor = conn.execute("SELECT COUNT(*) as cnt FROM videos")
    video_count = cursor.fetchone()["cnt"]

    cursor = conn.execute("SELECT COUNT(*) as cnt FROM merge_jobs WHERE youtube_id IS NOT NULL")
    uploaded_count = cursor.fetchone()["cnt"]

    cursor = conn.execute("SELECT COUNT(*) as cnt FROM merge_jobs")
    total_jobs = cursor.fetchone()["cnt"]

    print(f"\n📈 통계: 영상 {video_count}개 등록 | 병합 {total_jobs}건 | 업로드 {uploaded_count}건")

    conn.close()


def cmd_status_detail(job_id: int) -> None:
    """
    --status-detail 옵션 처리.

    특정 작업의 상세 정보를 출력합니다.

    Args:
        job_id: merge_job ID
    """
    import json

    conn = init_database()

    cursor = conn.execute(
        """
        SELECT * FROM merge_jobs WHERE id = ?
        """,
        (job_id,),
    )
    job = cursor.fetchone()

    if not job:
        print(f"❌ 작업 ID {job_id}를 찾을 수 없습니다.")
        conn.close()
        return

    print(f"\n📋 작업 상세 (ID: {job_id})\n")
    print("=" * 60)

    print(f"📌 제목: {job['title'] or '-'}")
    print(f"📅 날짜: {job['date'] or '-'}")
    print(f"📁 출력: {job['output_path']}")

    # 상태
    status = job["status"]
    status_icon = {
        "pending": "⏳ 대기",
        "processing": "🔄 진행 중",
        "completed": "✅ 완료",
        "failed": "❌ 실패",
    }.get(status, status)
    print(f"📊 상태: {status_icon}")

    # 길이/크기
    duration = job["total_duration_seconds"] or 0
    hours = int(duration // 3600)
    minutes = int((duration % 3600) // 60)
    seconds = int(duration % 60)
    if hours > 0:
        duration_str = f"{hours}시간 {minutes}분 {seconds}초"
    elif minutes > 0:
        duration_str = f"{minutes}분 {seconds}초"
    else:
        duration_str = f"{seconds}초"
    print(f"⏱️  길이: {duration_str}")

    size_bytes = job["total_size_bytes"] or 0
    if size_bytes >= 1024 * 1024 * 1024:
        size_str = f"{size_bytes / (1024**3):.2f} GB"
    else:
        size_str = f"{size_bytes / (1024**2):.1f} MB"
    print(f"💾 크기: {size_str}")

    # YouTube
    if job["youtube_id"]:
        print(f"🎬 YouTube: https://youtu.be/{job['youtube_id']}")
    else:
        print("🎬 YouTube: 미업로드")

    # 클립 정보
    clips_json = job["clips_info_json"]
    if clips_json:
        try:
            clips = json.loads(clips_json)
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
    conn.close()


def main() -> None:
    """CLI 진입점."""
    parser = create_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)

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

        # --status 옵션 처리 (작업 현황 조회)
        if args.status:
            cmd_status()
            return

        # --status-detail 옵션 처리 (작업 상세 조회)
        if args.status_detail is not None:
            cmd_status_detail(args.status_detail)
            return

        # --upload-only 옵션 처리 (업로드만)
        if args.upload_only:
            cmd_upload_only(args)
            return

        validated_args = validate_args(args)

        if validated_args.dry_run:
            # Dry run: 실행 계획만 출력
            logger.info("Dry run mode - showing execution plan only")

            video_files = scan_videos(validated_args.targets)
            temp_dir = get_temp_dir()

            # 출력 경로 계산
            if validated_args.output:
                output_str = str(validated_args.output)
            else:
                output_filename = get_output_filename(validated_args.targets)
                output_dir = validated_args.output_dir or Path.cwd()
                output_str = str(output_dir / output_filename)

            print("\n=== Dry Run Execution Plan ===")
            print(f"Input targets: {[str(t) for t in validated_args.targets]}")
            print(f"Video files found: {len(video_files)}")
            for vf in video_files:
                print(f"  - {vf.path}")
            print(f"Output: {output_str}")
            print(f"Temp dir: {temp_dir}")
            print(f"Resume enabled: {not validated_args.no_resume}")
            print(f"Keep temp files: {validated_args.keep_temp}")
            print(f"Parallel workers: {validated_args.parallel}")
            print("=" * 30)
            return

        output_path = run_pipeline(validated_args)
        print("\n✅ 완료!")
        print(f"📹 출력 파일: {output_path}")

        # --upload 플래그 처리
        if validated_args.upload:
            print("\n📤 YouTube 업로드 시작...")
            # DB에서 최신 MergeJob ID 조회
            merge_job_id = None
            title = None
            description = ""
            try:
                conn = init_database()
                repo = MergeJobRepository(conn)
                job = repo.get_latest()
                if job:
                    merge_job_id = job.id
                    title = job.title
                    description = job.summary_markdown or ""
                conn.close()
            except Exception as e:
                logger.warning(f"Failed to get merge job: {e}")

            # 플레이리스트 처리
            playlist_ids = resolve_playlist_ids(args.playlist)

            upload_to_youtube(
                file_path=output_path,
                title=title,
                description=description,
                merge_job_id=merge_job_id,
                playlist_ids=playlist_ids,
                chunk_mb=args.upload_chunk,
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
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
