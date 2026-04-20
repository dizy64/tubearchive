"""TUI 대시보드 모듈.

``tubearchive tui [path]`` 서브커맨드로 진입하는 Textual 기반 인터랙티브 대시보드.
"""

from __future__ import annotations

from pathlib import Path

from tubearchive.config import AppConfig


def launch_tui(initial_path: str | None = None, config: AppConfig | None = None) -> None:
    """TUI 애플리케이션을 실행한다.

    Args:
        initial_path: Pipeline 탭에 미리 로드할 대상 경로 문자열. None이면 빈 상태.
        config: 로드된 설정. None이면 기본 설정 사용.
    """
    from tubearchive.app.tui.app import TubeArchiveApp

    path = Path(initial_path).expanduser().resolve() if initial_path else None
    app = TubeArchiveApp(initial_path=path, config=config)
    app.run()


__all__ = ["launch_tui"]
