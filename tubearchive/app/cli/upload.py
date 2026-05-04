"""CLI upload 모듈 — YouTube 업로드 흐름."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tubearchive.infra.notification.notifier import Notifier

from tubearchive.config import ENV_YOUTUBE_PLAYLIST, HooksConfig
from tubearchive.domain.media.hooks import HookContext, run_hooks
from tubearchive.domain.media.splitter import probe_duration
from tubearchive.domain.models.clip import ClipInfo
from tubearchive.infra.db.repository import MergeJobRepository, SplitJobRepository

logger = logging.getLogger(__name__)

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
    subtitle_path: Path | None = None,
    subtitle_language: str | None = None,
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
        subtitle_path: 자막 파일 경로
        subtitle_language: 자막 언어 코드

    Returns:
        업로드된 YouTube 영상 ID. 실패 시 None.
    """
    from tubearchive.app.cli.main import database_session, safe_input
    from tubearchive.infra.youtube.auth import YouTubeAuthError, get_authenticated_service
    from tubearchive.infra.youtube.playlist import PlaylistError, add_to_playlist
    from tubearchive.infra.youtube.uploader import (
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
    from tubearchive.infra.youtube.auth import check_auth_status

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

        if subtitle_path is not None:
            try:
                uploader.set_captions(
                    video_id=result.video_id,
                    caption_path=subtitle_path,
                    language=subtitle_language,
                )
                logger.info(
                    "Subtitles uploaded for video_id=%s from %s",
                    result.video_id,
                    subtitle_path,
                )
            except Exception as e:
                logger.warning(
                    "Failed to set captions for %s: %s",
                    result.video_id,
                    e,
                )

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
    from tubearchive.app.cli.main import _interactive_select

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
        from tubearchive.infra.youtube.auth import get_authenticated_service
        from tubearchive.infra.youtube.playlist import list_playlists, select_playlist_interactive

        print("\n📋 플레이리스트 목록을 가져오는 중...")
        service = get_authenticated_service()
        playlists = list_playlists(service)

        selected = select_playlist_interactive(playlists)
        if selected:
            for pl in selected:
                print(f"   선택됨: {pl.title}")
            direct_ids.extend([pl.id for pl in selected])

    return direct_ids


def cmd_upload_only(args: argparse.Namespace, hooks: HooksConfig | None = None) -> str | None:
    """
    --upload-only 옵션 처리.

    Args:
        args: 파싱된 인자
    """
    from tubearchive.app.cli.main import database_session
    from tubearchive.app.cli.parser import parse_schedule_datetime
    from tubearchive.app.cli.validators import _resolve_set_thumbnail_path

    file_path = Path(args.upload_only).expanduser()

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

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
    video_id = upload_to_youtube(
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

    if hooks is not None and video_id is not None:
        run_hooks(
            hooks,
            "on_upload",
            context=HookContext(
                output_path=file_path,
                youtube_id=video_id,
                input_paths=(file_path,),
            ),
        )

    return video_id


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
    subtitle_path: Path | None = None,
    subtitle_language: str | None = None,
) -> list[str]:
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
        subtitle_path: 자막 파일 경로
        subtitle_language: 자막 언어 코드
    """
    from tubearchive.app.cli.main import database_session
    from tubearchive.shared.summary_generator import (
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
    uploaded_ids: list[str] = []
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
                subtitle_path=subtitle_path,
                subtitle_language=subtitle_language,
            )
            # 파트별 youtube_id를 split_job에 저장
            if video_id and split_job_id is not None:
                try:
                    with database_session() as conn:
                        split_repo = SplitJobRepository(conn)
                        split_repo.append_youtube_id(split_job_id, video_id)
                except Exception as e:
                    logger.warning(f"Failed to save youtube_id for part {i + 1}: {e}")
            if video_id:
                uploaded_ids.append(video_id)
        except Exception as e:
            logger.error(f"Part {i + 1}/{total} upload failed: {e}")
            print(f"  ⚠️  Part {i + 1} 업로드 실패: {e}")
            continue

    return uploaded_ids


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
    from tubearchive.app.cli.main import database_session
    from tubearchive.infra.db.repository import ProjectRepository

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
        from tubearchive.infra.youtube.auth import get_authenticated_service
        from tubearchive.infra.youtube.playlist import create_playlist

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
    subtitle_path: Path | None = None,
    subtitle_language: str | None = None,
    explicit_thumbnail: Path | None = None,
    hooks: HooksConfig | None = None,
) -> list[str]:
    """파이프라인 완료 후 YouTube 업로드를 수행한다.

    DB에서 최신 merge_job을 조회하여 제목·설명을 가져온 뒤,
    분할 파일이 있으면 순차 업로드, 없으면 단일 업로드한다.

    Args:
        output_path: 업로드할 병합 영상 파일 경로
        args: 원본 CLI 인자 (playlist, upload_privacy, upload_chunk 등)
        notifier: 알림 오케스트레이터 (None이면 알림 비활성화)
        publish_at: 예약 공개 시간 (이미 검증된 값, 재파싱하지 않음)
        generated_thumbnail_paths: 썸네일 후보 경로 목록 (생성된 썸네일)
        subtitle_path: 자막 파일 경로
        subtitle_language: 자막 언어 코드
        explicit_thumbnail: --set-thumbnail에서 지정한 썸네일 경로
    """
    from tubearchive.app.cli.main import database_session

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
            job = repo.get_by_output_path(output_path)
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
    uploaded_ids: list[str] = []
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
        uploaded_ids = _upload_split_files(
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
            subtitle_path=subtitle_path,
            subtitle_language=subtitle_language,
        )
    else:
        video_id = upload_to_youtube(
            file_path=output_path,
            title=title,
            description=description,
            privacy=args.upload_privacy,
            publish_at=publish_at,
            merge_job_id=merge_job_id,
            playlist_ids=playlist_ids,
            chunk_mb=args.upload_chunk,
            thumbnail=thumbnail,
            subtitle_path=subtitle_path,
            subtitle_language=subtitle_language,
        )
        if video_id:
            uploaded_ids = [video_id]

    # 알림: 업로드 완료
    if notifier:
        from tubearchive.infra.notification import upload_complete_event

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

    if hooks is not None and uploaded_ids:
        run_hooks(
            hooks,
            "on_upload",
            context=HookContext(
                output_path=output_path,
                youtube_id=";".join(uploaded_ids),
                input_paths=(output_path,),
            ),
        )

    return uploaded_ids
