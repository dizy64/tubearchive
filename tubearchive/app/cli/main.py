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

import logging
import sqlite3
import subprocess
import sys
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from tubearchive.infra.notification.notifier import Notifier
from tubearchive.app.queries.catalog import (
    CATALOG_STATUS_SENTINEL,
    cmd_catalog,
    normalize_status_filter,
)
from tubearchive.config import (
    apply_config_to_env,
    get_default_config_path,
    load_config,
)
from tubearchive.domain.media.hooks import HookContext, HookEvent, run_hooks
from tubearchive.infra.db.repository import (
    MergeJobRepository,  # noqa: F401  # mock surface: pipeline.save_merge_job_to_db patches main.MergeJobRepository
)
from tubearchive.infra.db.schema import init_database as _init_database
from tubearchive.shared.validators import ValidationError

logger = logging.getLogger(__name__)


# 호환성: 기존 테스트/모킹 코드에서 `tubearchive.app.cli.main.init_database`를 패치한다.
init_database = _init_database


@contextmanager
def database_session() -> Iterator[sqlite3.Connection]:
    """DB 연결을 열고 사용 후 반드시 close 한다."""
    conn = init_database()
    try:
        yield conn
    finally:
        conn.close()


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

    try:
        return input().strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def setup_logging(
    verbose: bool = False,
    *,
    log_path: Path | None = None,
) -> None:
    """
    로깅 설정.

    Args:
        verbose: 상세 로그 여부
    """
    level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [
        logging.StreamHandler(),
    ]
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    for handler in handlers:
        handler.setLevel(level)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    root_logger.setLevel(level)


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


# --- parser.py re-exports ---
from tubearchive.app.cli.parser import (  # noqa: E402, F401
    create_parser,
    parse_schedule_datetime,
)

# --- pipeline.py re-exports (after module-level code) ---
from tubearchive.app.cli.pipeline import (  # noqa: E402, F401
    TranscodeOptions,
    TranscodeResult,
    _apply_bgm_mixing,
    _apply_ordering,
    _apply_subtitle_burn,
    _archive_originals,
    _cleanup_temp,
    _cmd_dry_run,
    _collect_clip_info,
    _detect_silence_only,
    _generate_thumbnails,
    _generate_timelapse,
    _get_media_duration,
    _has_audio_stream,
    _is_file_in_use,
    _link_merge_job_to_project,
    _make_watermark_text,
    _mark_transcoding_jobs_merged,
    _print_quality_report,
    _print_summary,
    _prompt_archive_delete_confirmation,
    _resolve_output_path,
    _run_backup,
    _run_error_hook,
    _to_video_file,
    _transcode_parallel,
    _transcode_sequential,
    _transcode_single,
    check_output_disk_space,
    get_output_filename,
    get_temp_dir,
    handle_single_file_upload,
    run_pipeline,
    save_merge_job_to_db,
)

# --- status.py re-exports ---
from tubearchive.app.cli.status import (  # noqa: E402, F401
    _delete_build_records,
    cmd_fix_device_models,
    cmd_reset_build,
    cmd_reset_upload,
    cmd_status,
    cmd_status_detail,
)

# --- upload.py re-exports ---
from tubearchive.app.cli.upload import (  # noqa: E402, F401
    DATE_PATTERN,
    _get_or_create_project_playlist,
    _resolve_upload_thumbnail,
    _upload_after_pipeline,
    _upload_split_files,
    cmd_upload_only,
    format_youtube_title,
    resolve_playlist_ids,
    upload_to_youtube,
)

# --- validators.py re-exports ---
from tubearchive.app.cli.validators import (  # noqa: E402, F401
    SUPPORTED_THUMBNAIL_EXTENSIONS,
    ValidatedArgs,
    _resolve_set_thumbnail_path,
    _resolve_template_path,
    validate_args,
)

# --- watch.py re-exports ---
from tubearchive.app.cli.watch import (  # noqa: E402, F401
    _run_watch_mode,
    _run_watch_pipeline,
    _setup_file_observer,
    _wait_for_stable_file,
)

# --- youtube.py re-exports ---
from tubearchive.app.cli.youtube import (  # noqa: E402
    cmd_list_playlists,
    cmd_setup_youtube,
    cmd_youtube_auth,
)

# domain re-exports (외부 import 표면 보존)
from tubearchive.domain.models.clip import ClipInfo  # noqa: E402, F401


def main() -> None:
    """CLI 진입점.

    인자를 파싱하고 설정 파일을 로드한 뒤, 요청된 서브커맨드를
    적절한 핸들러 함수로 라우팅한다. 서브커맨드가 지정되지 않은
    기본 동작은 :func:`run_pipeline` (트랜스코딩 + 병합).
    """
    # argparse보다 먼저 처리: nargs="*" targets와 충돌 방지
    if len(sys.argv) > 1 and sys.argv[1] == "tui":
        from tubearchive.app.tui import launch_tui

        path_arg = sys.argv[2] if len(sys.argv) > 2 else None
        has_config_flag = len(sys.argv) > 3 and sys.argv[3] == "--config"
        tui_config_path = Path(sys.argv[4]) if has_config_flag else get_default_config_path()
        tui_config = load_config(tui_config_path)
        apply_config_to_env(tui_config)
        launch_tui(initial_path=path_arg, config=tui_config)
        return

    parser = create_parser()
    args = parser.parse_args()

    # --init-config 처리 (가장 먼저, 로깅/설정 로드 전)
    if args.init_config:
        cmd_init_config()
        return

    # 설정 파일 로드 및 환경변수 적용
    config_path = Path(args.config) if args.config else get_default_config_path()
    config = load_config(config_path)
    apply_config_to_env(config)
    setup_logging(args.verbose)

    # --notify-test 처리 (서브커맨드 전)
    if getattr(args, "notify_test", False):
        from tubearchive.infra.notification import Notifier as _Notifier

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

    if args.run_hook:
        hook_event = cast(HookEvent, args.run_hook)
        run_hooks(
            config.hooks,
            hook_event,
            context=HookContext(),
        )
        return

    notifier: Notifier | None = None
    validated_args: ValidatedArgs | None = None
    output_path: Path | None = None

    try:
        if args.setup_youtube:
            cmd_setup_youtube()
            return

        if args.youtube_auth:
            cmd_youtube_auth()
            return

        if args.list_playlists:
            cmd_list_playlists()
            return

        if args.reset_build is not None:
            cmd_reset_build(args.reset_build)
            return

        if args.reset_upload is not None:
            cmd_reset_upload(args.reset_upload)
            return

        if args.fix_device_models:
            cmd_fix_device_models()
            return

        if args.export_db:
            from tubearchive.app.queries.migrate import cmd_export_db

            cmd_export_db(Path(args.export_db))
            return

        if args.import_db:
            from tubearchive.app.queries.migrate import cmd_import_db

            cmd_import_db(
                Path(args.import_db),
                src_prefix=args.src_prefix,
                dst_prefix=args.dst_prefix,
                overwrite=args.overwrite,
            )
            return

        if args.project_list:
            from tubearchive.app.queries.project import cmd_project_list

            cmd_project_list(output_json=args.json)
            return

        if args.project_detail is not None:
            from tubearchive.app.queries.project import cmd_project_detail

            cmd_project_detail(args.project_detail, output_json=args.json)
            return

        if args.status_detail is not None:
            cmd_status_detail(args.status_detail)
            return

        if args.status == CATALOG_STATUS_SENTINEL:
            cmd_status()
            return

        # --period 단독 사용 경고
        if args.period and not args.stats:
            logger.warning("--period 옵션은 --stats와 함께 사용해야 합니다.")

        if args.stats:
            from tubearchive.app.queries.stats import cmd_stats as _cmd_stats

            with database_session() as conn:
                _cmd_stats(conn, period=args.period)
            return

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

        if args.upload_only:
            cmd_upload_only(args, hooks=config.hooks)
            return

        # config의 device_luts를 validate_args에 전달하여 초기화 시 주입
        cfg_device_luts = config.color_grading.device_luts or None
        validated_args = validate_args(
            args,
            device_luts=cfg_device_luts,
            hooks=config.hooks,
        )

        if validated_args.watch:
            _run_watch_mode(
                args,
                validated_args,
                config_path=config_path,
                hooks=config.hooks,
                notifier=notifier,
                verbose=args.verbose,
            )
            return

        if validated_args.dry_run:
            _cmd_dry_run(validated_args)
            return

        # Notifier 초기화
        if validated_args.notify:
            from tubearchive.infra.notification import Notifier as _Notifier

            notifier = _Notifier(config.notification)
            if notifier.has_providers:
                logger.info("알림 시스템 활성화 (%d개 채널)", notifier.provider_count)

        pipeline_generated_thumbnail_paths: list[Path] = []
        pipeline_generated_subtitle_paths: list[Path] = []
        output_path = run_pipeline(
            validated_args,
            notifier=notifier,
            generated_thumbnail_paths=pipeline_generated_thumbnail_paths,
            generated_subtitle_paths=pipeline_generated_subtitle_paths,
        )
        subtitle_path = (
            pipeline_generated_subtitle_paths[0] if pipeline_generated_subtitle_paths else None
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
                subtitle_path=subtitle_path,
                subtitle_language=validated_args.subtitle_lang,
                explicit_thumbnail=validated_args.set_thumbnail,
                hooks=config.hooks,
            )

    except FileNotFoundError as e:
        _run_error_hook(config.hooks, e, output_path=output_path, validated_args=validated_args)
        logger.error(str(e))
        sys.exit(1)
    except ValueError as e:
        _run_error_hook(config.hooks, e, output_path=output_path, validated_args=validated_args)
        logger.error(str(e))
        sys.exit(1)
    except ValidationError as e:
        _run_error_hook(config.hooks, e, output_path=output_path, validated_args=validated_args)
        logger.error(str(e))
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        sys.exit(130)
    except Exception as e:
        # 에러 알림
        if notifier is not None:
            from tubearchive.infra.notification import error_event

            notifier.notify(error_event(error_message=str(e), stage="pipeline"))
        _run_error_hook(config.hooks, e, output_path=output_path, validated_args=validated_args)
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
