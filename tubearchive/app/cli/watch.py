"""Watch 모드 CLI 커맨드."""

from __future__ import annotations

import argparse
import logging
import signal
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock
from typing import TYPE_CHECKING, Any

from tubearchive.app.cli.pipeline import _run_error_hook, run_pipeline
from tubearchive.app.cli.upload import _upload_after_pipeline
from tubearchive.app.cli.validators import ValidatedArgs, validate_args
from tubearchive.config import HooksConfig, apply_config_to_env, load_config
from tubearchive.shared.validators import VIDEO_EXTENSIONS

if TYPE_CHECKING:
    from tubearchive.infra.notification.notifier import Notifier

logger = logging.getLogger(__name__)


def _wait_for_stable_file(
    file_path: Path,
    checks: int,
    interval: float,
    stop_event: Event,
) -> bool:
    """파일 크기가 N회 연속 동일하면 안정된 것으로 간주."""
    if checks <= 0:
        return True

    last_size: int | None = None
    stable_count = 0
    while not stop_event.is_set():
        try:
            current_size = file_path.stat().st_size
        except OSError:
            return False

        if last_size is not None and current_size == last_size:
            stable_count += 1
        else:
            last_size = current_size
            stable_count = 1

        if stable_count >= checks:
            return True

        stop_event.wait(interval)

    return False


def _setup_file_observer(
    paths: list[Path],
    callback: Callable[[Path], None],
) -> tuple[Any, Any]:
    """watchdog observer를 시작하고 observer 객체를 반환."""
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "watchdog 패키지가 필요합니다. 'uv add watchdog'로 설치 후 다시 시도하세요."
        ) from exc

    class _Handler(FileSystemEventHandler):
        def on_created(self, event: Any) -> None:
            if not getattr(event, "is_directory", False):
                callback(Path(event.src_path))

        def on_modified(self, event: Any) -> None:
            if not getattr(event, "is_directory", False):
                callback(Path(event.src_path))

        def on_moved(self, event: Any) -> None:
            if not getattr(event, "is_directory", False):
                callback(Path(event.dest_path))

    observer: Any = Observer()
    handler = _Handler()
    for watch_path in paths:
        observer.schedule(handler, str(watch_path), recursive=True)
    observer.start()
    return observer, handler


def _run_watch_pipeline(
    file_path: Path,
    args_namespace: argparse.Namespace,
    validated_args: ValidatedArgs,
    notifier: Notifier | None = None,
    hooks: HooksConfig | None = None,
) -> None:
    """watch 이벤트가 들어온 단일 파일에 대해 파이프라인 1회 실행."""
    pipeline_args = replace(
        validated_args,
        targets=[file_path],
        watch=False,
        watch_paths=[],
    )
    pipeline_generated_thumbnail_paths: list[Path] = []
    output_path = run_pipeline(
        pipeline_args,
        notifier=notifier,
        generated_thumbnail_paths=pipeline_generated_thumbnail_paths,
    )
    print("\n✅ 완료!")
    print(f"📹 출력 파일: {output_path}")

    if pipeline_args.upload:
        _upload_after_pipeline(
            output_path=output_path,
            args=args_namespace,
            notifier=notifier,
            publish_at=pipeline_args.schedule,
            generated_thumbnail_paths=pipeline_generated_thumbnail_paths,
            explicit_thumbnail=pipeline_args.set_thumbnail,
            hooks=hooks,
        )


def _run_watch_mode(
    parsed_args: argparse.Namespace,
    validated_args: ValidatedArgs,
    *,
    config_path: Path | None,
    hooks: HooksConfig | None = None,
    notifier: Notifier | None = None,
    verbose: bool = False,
) -> None:
    """watch 모드 실행."""
    from collections import deque

    # Lazy import: setup_logging is defined in main.py and stays there
    from tubearchive.app.cli.main import setup_logging

    queue: deque[Path] = deque()
    lock = Lock()
    stop_event = Event()
    reload_event = Event()

    def _enqueue_file(path: Path) -> None:
        if stop_event.is_set():
            return
        if path.name.startswith(".") or path.suffix.lower() not in VIDEO_EXTENSIONS:
            return

        with lock:
            if path not in queue:
                queue.append(path)

    def _load_validated_args() -> ValidatedArgs:
        if config_path is None:
            return validate_args(parsed_args, hooks=hooks)
        updated_config = load_config(config_path)
        apply_config_to_env(updated_config, overwrite=True)
        return validate_args(
            parsed_args,
            device_luts=updated_config.color_grading.device_luts or None,
            hooks=updated_config.hooks,
        )

    def _signal_handler(signum: int, _frame: object | None) -> None:
        if signum == signal.SIGINT:
            stop_event.set()
        else:
            reload_event.set()

    previous_sigint: Any = signal.getsignal(signal.SIGINT)
    previous_sighup: Any | None = (
        signal.getsignal(signal.SIGHUP) if hasattr(signal, "SIGHUP") else None
    )
    signal.signal(signal.SIGINT, _signal_handler)
    if previous_sighup is not None:
        signal.signal(signal.SIGHUP, _signal_handler)

    try:
        current_args = validated_args
        while not stop_event.is_set():
            if not current_args.watch or not current_args.watch_paths:
                logger.info("No watch paths configured; exiting watch mode.")
                return

            setup_logging(
                verbose=verbose,
                log_path=current_args.watch_log,
            )

            observer, _handler = _setup_file_observer(current_args.watch_paths, _enqueue_file)
            logger.info("watch mode started (paths=%s)", current_args.watch_paths)

            try:
                while not stop_event.is_set():
                    if reload_event.is_set():
                        reload_event.clear()
                        break

                    pending: Path | None = None
                    with lock:
                        if queue:
                            pending = queue.popleft()

                    if pending is None:
                        stop_event.wait(current_args.watch_poll_interval)
                        continue

                    if not _wait_for_stable_file(
                        pending,
                        checks=current_args.watch_stability_checks,
                        interval=current_args.watch_poll_interval,
                        stop_event=stop_event,
                    ):
                        continue

                    if not pending.is_file():
                        continue

                    try:
                        _run_watch_pipeline(
                            pending,
                            parsed_args,
                            current_args,
                            notifier=notifier,
                            hooks=current_args.hooks if hooks is None else hooks,
                        )
                    except Exception as e:
                        logger.error("watch pipeline failed for %s: %s", pending, e)
                        _run_error_hook(
                            current_args.hooks,
                            e,
                            output_path=None,
                            validated_args=current_args,
                        )
            finally:
                observer.stop()
                observer.join()

            if stop_event.is_set():
                return

            try:
                current_args = _load_validated_args()
            except Exception as e:
                logger.error("watch config reload failed: %s", e)
                return
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        if previous_sighup is not None:
            signal.signal(signal.SIGHUP, previous_sighup)
        stop_event.set()


__all__ = [
    "_run_watch_mode",
    "_run_watch_pipeline",
    "_setup_file_observer",
    "_wait_for_stable_file",
]
