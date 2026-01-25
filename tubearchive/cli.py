"""CLI 인터페이스."""

import argparse
import json
import logging
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from tubearchive import __version__
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
ENV_YOUTUBE_PLAYLIST = "TUBEARCHIVE_YOUTUBE_PLAYLIST"

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
        "-V", "--version",
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
        help=(
            "업로드 후 플레이리스트에 추가 "
            f"(환경변수: {ENV_YOUTUBE_PLAYLIST}, 쉼표로 구분)"
        ),
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

    return ValidatedArgs(
        targets=targets,
        output=output,
        output_dir=output_dir,
        no_resume=args.no_resume,
        keep_temp=args.keep_temp,
        dry_run=args.dry_run,
        upload=upload,
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

        # 콘솔 출력용 요약 (마크다운 형식)
        console_summary = generate_clip_summary(video_clips)

        # YouTube 설명용 (타임스탬프 + 촬영기기)
        youtube_description = generate_youtube_description(video_clips)

        repo.create(
            output_path=output_path,
            video_ids=[],
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
    """
    from tubearchive.youtube.auth import YouTubeAuthError, get_authenticated_service
    from tubearchive.youtube.playlist import PlaylistError, add_to_playlist
    from tubearchive.youtube.uploader import YouTubeUploader, YouTubeUploadError

    if not file_path.exists():
        raise FileNotFoundError(f"Video file not found: {file_path}")

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
        uploader = YouTubeUploader(service)

        # 프로그레스 바 설정
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        bar_width = 30

        def on_progress(percent: int) -> None:
            filled = int(bar_width * percent / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            msg = f"\r📤 업로드: [{bar}] {percent:3d}% ({file_size_mb:.1f}MB)"
            print(msg, end="", flush=True)
            if percent >= 100:
                print()  # 완료 시 줄바꿈

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
                    add_to_playlist(service, pid, result.video_id)
                    print(f"📋 플레이리스트에 추가됨: {pid}")
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
    )


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
            description = ""
            try:
                conn = init_database()
                repo = MergeJobRepository(conn)
                job = repo.get_latest()
                if job:
                    merge_job_id = job.id
                    description = job.summary_markdown or ""
                conn.close()
            except Exception as e:
                logger.warning(f"Failed to get merge job: {e}")

            # 플레이리스트 처리
            playlist_ids = resolve_playlist_ids(args.playlist)

            upload_to_youtube(
                file_path=output_path,
                description=description,
                merge_job_id=merge_job_id,
                playlist_ids=playlist_ids,
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
