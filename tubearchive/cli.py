"""CLI 인터페이스."""

import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from tubearchive.core.merger import Merger
from tubearchive.core.scanner import scan_videos
from tubearchive.core.transcoder import Transcoder

logger = logging.getLogger(__name__)


@dataclass
class ValidatedArgs:
    """검증된 CLI 인자."""

    targets: list[Path]
    output: Path | None
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

    return ValidatedArgs(
        targets=targets,
        output=output,
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

    # 2. 트랜스코딩
    logger.info("Starting transcoding...")
    transcoded_paths: list[Path] = []

    with Transcoder() as transcoder:
        for vf in video_files:
            logger.info(f"Transcoding: {vf.path.name}")
            output_path = transcoder.transcode_video(vf)
            transcoded_paths.append(output_path)
            logger.info(f"  -> {output_path.name}")

    # 3. 병합
    logger.info("Merging videos...")
    output_path = validated_args.output or Path.cwd() / "merged_output.mp4"

    merger = Merger()
    final_path = merger.merge(transcoded_paths, output_path)

    logger.info(f"Final output: {final_path}")

    # 4. 임시 파일 정리
    if not validated_args.keep_temp:
        logger.info("Cleaning up temporary files...")
        for temp_path in transcoded_paths:
            if temp_path.exists() and temp_path != final_path:
                temp_path.unlink()
                logger.debug(f"  Removed: {temp_path}")

    return final_path


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
            print("\n=== Dry Run Execution Plan ===")
            print(f"Input targets: {[str(t) for t in validated_args.targets]}")
            print(f"Video files found: {len(video_files)}")
            for vf in video_files:
                print(f"  - {vf.path}")
            print(f"Output: {validated_args.output or 'merged_output.mp4'}")
            print(f"Resume enabled: {not validated_args.no_resume}")
            print(f"Keep temp files: {validated_args.keep_temp}")
            print("=" * 30)
            return

        output_path = run_pipeline(validated_args)
        print(f"\nSuccess! Output: {output_path}")

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
