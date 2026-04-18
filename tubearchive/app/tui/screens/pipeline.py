"""Pipeline 탭 화면 (Phase 1 Placeholder).

Phase 2에서 파일 선택 + 옵션 패널 + 실행 + 진행률을 구현한다.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.widgets import Label, Static


class PipelinePane(Static):
    """파이프라인 실행 패널 (Phase 2 구현 예정)."""

    def __init__(self, initial_path: Path | None = None) -> None:
        super().__init__()
        self.initial_path = initial_path

    def compose(self) -> ComposeResult:
        if self.initial_path:
            yield Label(f"[bold]대상 경로:[/] {self.initial_path}", classes="section-title")
        yield Label(
            "[dim italic]Pipeline 탭은 Phase 2에서 구현됩니다.\n\n"
            "  · 파일 선택기 (DirectoryTree)\n"
            "  · 옵션 패널 (카테고리별 Collapsible)\n"
            "  · 실행 버튼 + 실시간 진행률[/]",
            classes="placeholder",
        )
