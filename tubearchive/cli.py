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
from tubearchive.utils.progress import MultiProgressBar

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

    # 2. 트랜스코딩 (임시 파일은 /tmp에 저장)
    temp_dir = get_temp_dir()
    logger.info(f"Using temp directory: {temp_dir}")
    logger.info("Starting transcoding...")
    transcoded_paths: list[Path] = []
    # (파일명, duration, device_model, creation_time_str)
    video_clips: list[tuple[str, float, str | None, str | None]] = []
    progress = MultiProgressBar(total_files=len(video_files))

    with Transcoder(temp_dir=temp_dir) as transcoder:
        for vf in video_files:
            progress.start_file(vf.path.name)

            def on_progress(percent: int) -> None:
                progress.update_file_progress(percent)

            output_path = transcoder.transcode_video(vf)
            transcoded_paths.append(output_path)

            # 메타데이터 수집 (Summary용)
            try:
                metadata = detect_metadata(vf.path)
                creation_time_str = vf.creation_time.strftime("%H:%M:%S")
                video_clips.append((
                    vf.path.name,
                    metadata.duration_seconds,
                    metadata.device_model,
                    creation_time_str,
                ))
            except Exception as e:
                logger.warning(f"Failed to get metadata for {vf.path}: {e}")
                video_clips.append((vf.path.name, 0.0, None, None))

            progress.finish_file()

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
        final_path, video_clips, validated_args.targets
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
) -> str | None:
    """
    병합 작업 정보를 DB에 저장 (타임라인 및 Summary 포함).

    Args:
        output_path: 출력 파일 경로
        video_clips: (파일명, 재생시간, 기종, 촬영시간) 튜플 리스트
        targets: 입력 타겟 목록 (제목 추출용)

    Returns:
        생성된 Summary 마크다운 (실패 시 None)
    """
    from tubearchive.utils.summary_generator import generate_clip_summary

    try:
        conn = init_database()
        repo = MergeJobRepository(conn)

        # 타임라인 정보 생성 (각 클립의 메타데이터 포함)
        timeline: list[dict[str, str | float | None]] = []
        current_time = 0.0
        for name, duration, device, shot_time in video_clips:
            timeline.append({
                "name": name,
                "duration": duration,
                "start": current_time,
                "end": current_time + duration,
                "device": device,
                "shot_time": shot_time,
            })
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
        from datetime import date
        today = date.today().isoformat()

        # 총 재생시간 및 파일 크기
        total_duration = sum(d for _, d, _, _ in video_clips)
        total_size = output_path.stat().st_size if output_path.exists() else 0

        # Summary 마크다운 생성 (기종, 촬영시간, 타임스탬프)
        summary_markdown = generate_clip_summary(video_clips)

        repo.create(
            output_path=output_path,
            video_ids=[],
            title=title,
            date=today,
            total_duration_seconds=total_duration,
            total_size_bytes=total_size,
            clips_info_json=clips_json,
            summary_markdown=summary_markdown,
        )
        conn.close()
        logger.debug("Merge job saved to database with summary")
        return summary_markdown

    except Exception as e:
        logger.warning(f"Failed to save merge job to DB: {e}")
        return None


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
            print("=" * 30)
            return

        output_path = run_pipeline(validated_args)
        print("\n✅ 완료!")
        print(f"📹 출력 파일: {output_path}")

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
