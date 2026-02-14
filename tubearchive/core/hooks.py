"""후처리 훅 실행 유틸.

설정 기반 이벤트 훅(``on_transcode``, ``on_merge``, ``on_upload``, ``on_error``)
을 subprocess로 실행한다.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tubearchive.config import HooksConfig

logger = logging.getLogger(__name__)

HookEvent = Literal["on_transcode", "on_merge", "on_upload", "on_error"]


@dataclass(frozen=True)
class HookContext:
    """훅 실행 시 전달할 실행 컨텍스트."""

    output_path: Path | None = None
    youtube_id: str | None = None
    input_paths: tuple[Path, ...] = ()
    error_message: str | None = None


def _build_hook_env(context: HookContext) -> dict[str, str]:
    """훅 실행용 환경변수 사전을 구성한다."""
    env = os.environ.copy()
    env["TUBEARCHIVE_OUTPUT_PATH"] = str(context.output_path) if context.output_path else ""
    env["TUBEARCHIVE_YOUTUBE_ID"] = context.youtube_id or ""
    env["TUBEARCHIVE_INPUT_PATHS"] = ";".join(str(p) for p in context.input_paths)
    env["TUBEARCHIVE_INPUT_COUNT"] = str(len(context.input_paths))
    if context.error_message is not None:
        env["TUBEARCHIVE_ERROR_MESSAGE"] = context.error_message
    return env


def run_hooks(
    hooks: HooksConfig,
    event: HookEvent,
    *,
    context: HookContext,
    timeout_sec: int | None = None,
) -> None:
    """지정 이벤트 훅을 실행한다.

    Args:
        hooks: 설정에서 파싱한 훅 목록.
        event: 실행 이벤트(`on_transcode`, `on_merge`, `on_upload`, `on_error`).
        context: 훅 실행 컨텍스트.
        timeout_sec: 훅 기본 타임아웃(초). None이면 설정값 사용.
    """
    if event == "on_transcode":
        commands = hooks.on_transcode
    elif event == "on_merge":
        commands = hooks.on_merge
    elif event == "on_upload":
        commands = hooks.on_upload
    elif event == "on_error":
        commands = hooks.on_error
    else:
        logger.warning("알 수 없는 훅 이벤트: %s", event)
        return

    if not commands:
        return

    effective_timeout = timeout_sec if timeout_sec is not None else hooks.timeout_sec
    env = _build_hook_env(context)

    for command in commands:
        logger.info("훅 실행: event=%s command=%s", event, command)
        try:
            cmd = shlex.split(command)
            if not cmd:
                continue

            subprocess.run(
                cmd,
                env=env,
                timeout=effective_timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("훅 타임아웃(event=%s, timeout=%ss): %s", event, effective_timeout, exc)
        except Exception:
            logger.warning("훅 실행 실패(event=%s): %s", event, command, exc_info=True)
