"""CLI 인터페이스."""

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tubearchive.core.detector import detect_metadata
from tubearchive.core.merger import Merger
from tubearchive.core.scanner import scan_videos
from tubearchive.core.transcoder import Transcoder
from tubearchive.database.repository import MergeJobRepository
from tubearchive.database.schema import init_database
from tubearchive.models.video import VideoFile
from tubearchive.utils.progress import MultiProgressBar
from tubearchive.utils.summary_generator import OutputInfo, save_summary

logger = logging.getLogger(__name__)

# 환경 변수
ENV_OUTPUT_DIR = "TUBEARCHIVE_OUTPUT_DIR"


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


@dataclass
class ValidatedArgs:
    """검증된 CLI 인자."""

    targets: list[Path]
    output: Path | None
    output_dir: Path | None
    no_resume: bool
    keep_temp: bool
    dry_run: bool


def create_parser() -> argparse.ArgumentParser:
    """
    CLI 파서 생성.

    Returns:
        argparse.ArgumentParser 인스턴스
    """
    parser = argparse.ArgumentParser(
        prog="tubearchive",
        description="다양한 기기의 4K 영상을 표준화하여 병합합니다.",
        epilog="예시: tubearchive video1.mp4 video2.mov -o merged.mp4",
    )

    parser.add_argument(
        "targets",
        nargs="*",
        default=[],
        help="영상 파일 또는 디렉토리 (기본: 현재 디렉토리)",
    )

    parser.add_argument(
        "-o", "--output",
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
        "-v", "--verbose",
        action="store_true",
        help="상세 로그 출력",
    )

    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help=f"출력 파일 저장 디렉토리 (환경변수: {ENV_OUTPUT_DIR})",
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

    return ValidatedArgs(
        targets=targets,
        output=output,
        output_dir=output_dir,
        no_resume=args.no_resume,
        keep_temp=args.keep_temp,
        dry_run=args.dry_run,
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


def run_pipeline(validated_args: ValidatedArgs) -> tuple[Path, Path | None]:
    """
    전체 파이프라인 실행.

    Args:
        validated_args: 검증된 인자

    Returns:
        (최종 출력 파일 경로, 요약 파일 경로) 튜플
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

    # 2. 트랜스코딩 (임시 파일은 /tmp에 저장)
    temp_dir = get_temp_dir()
    logger.info(f"Using temp directory: {temp_dir}")
    logger.info("Starting transcoding...")
    transcoded_paths: list[Path] = []
    progress = MultiProgressBar(total_files=len(video_files))

    with Transcoder(temp_dir=temp_dir) as transcoder:
        for vf in video_files:
            progress.start_file(vf.path.name)

            def on_progress(percent: int) -> None:
                progress.update_file_progress(percent)

            output_path = transcoder.transcode_video(vf)
            transcoded_paths.append(output_path)
            progress.finish_file()

    # 3. 병합
    logger.info("Merging videos...")
    output_path = validated_args.output or Path.cwd() / "merged_output.mp4"

    merger = Merger(temp_dir=temp_dir)
    final_path = merger.merge(transcoded_paths, output_path)

    logger.info(f"Final output: {final_path}")

    # 4. 요약 정보 생성
    summary_path = generate_output_summary(
        video_files, final_path, validated_args.output_dir
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

    return final_path, summary_path


def generate_output_summary(
    video_files: list[VideoFile],
    output_path: Path,
    output_dir: Path | None = None,
) -> Path | None:
    """
    출력 영상 요약 정보 생성 및 DB 저장.

    Args:
        video_files: 원본 영상 파일 목록
        output_path: 출력 파일 경로
        output_dir: 요약 파일 저장 디렉토리 (None이면 출력 파일과 같은 디렉토리)

    Returns:
        요약 파일 경로 또는 None
    """
    try:
        logger.info("Generating output summary...")

        # 출력 디렉토리 결정
        summary_dir = output_dir or output_path.parent

        # 디스크 공간 확인 (최소 10MB 여유 확인)
        if not check_output_disk_space(summary_dir, 10 * 1024 * 1024):
            logger.warning("Skipping summary generation due to insufficient disk space")
            return None

        # 각 영상의 duration 수집
        video_durations: list[tuple[Path, float]] = []
        for vf in video_files:
            try:
                metadata = detect_metadata(vf.path)
                video_durations.append((vf.path, metadata.duration_seconds))
            except Exception as e:
                logger.warning(f"Failed to get duration for {vf.path}: {e}")
                video_durations.append((vf.path, 0.0))

        # OutputInfo 생성
        output_info = OutputInfo.from_video_files(video_durations, output_path)

        # 요약 마크다운 저장
        summary_path = save_summary(output_info, summary_dir)
        logger.info(f"Summary saved: {summary_path}")

        # DB에 저장
        save_merge_job_to_db(output_info, video_files)

        return summary_path

    except Exception as e:
        logger.warning(f"Failed to generate summary: {e}")
        return None


def save_merge_job_to_db(
    output_info: OutputInfo,
    video_files: list[VideoFile],
) -> None:
    """
    병합 작업 정보를 DB에 저장.

    Args:
        output_info: 출력 정보
        video_files: 원본 영상 파일 목록
    """
    try:
        conn = init_database()
        repo = MergeJobRepository(conn)

        # 클립 정보 JSON
        clips_json = json.dumps(
            [{"name": name, "duration": dur} for name, dur in output_info.clips],
            ensure_ascii=False,
        )

        # summary_path 계산
        actual_summary_path = (
            output_info.output_path.parent / f"{output_info.output_path.stem}_summary.md"
        )

        repo.create(
            output_path=output_info.output_path,
            video_ids=[],  # 현재 video_ids 추적 안 함 (단순화)
            title=output_info.title,
            date=output_info.date,
            total_duration_seconds=output_info.total_duration,
            total_size_bytes=output_info.total_size,
            clips_info_json=clips_json,
            summary_path=actual_summary_path,
        )
        conn.close()
        logger.debug("Merge job saved to database")

    except Exception as e:
        logger.warning(f"Failed to save merge job to DB: {e}")


def main() -> None:
    """CLI 진입점."""
    parser = create_parser()
    args = parser.parse_args()

    setup_logging(args.verbose)

    try:
        validated_args = validate_args(args)

        if validated_args.dry_run:
            # Dry run: 실행 계획만 출력
            logger.info("Dry run mode - showing execution plan only")

            video_files = scan_videos(validated_args.targets)
            temp_dir = get_temp_dir()
            output_dir_str = (
                str(validated_args.output_dir) if validated_args.output_dir else "(출력 파일 위치)"
            )

            print("\n=== Dry Run Execution Plan ===")
            print(f"Input targets: {[str(t) for t in validated_args.targets]}")
            print(f"Video files found: {len(video_files)}")
            for vf in video_files:
                print(f"  - {vf.path}")
            print(f"Output: {validated_args.output or 'merged_output.mp4'}")
            print(f"Output dir: {output_dir_str}")
            print(f"Temp dir: {temp_dir}")
            print(f"Resume enabled: {not validated_args.no_resume}")
            print(f"Keep temp files: {validated_args.keep_temp}")
            print("=" * 30)
            return

        output_path, summary_path = run_pipeline(validated_args)
        print("\n✅ 완료!")
        print(f"📹 출력 파일: {output_path}")
        if summary_path:
            print(f"📝 요약 파일: {summary_path}")

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
