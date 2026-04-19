"""TubeArchive TUI 메인 앱.

Textual 기반 4탭 대시보드: Pipeline / Projects / Stats / History.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from tubearchive.config import AppConfig


class TubeArchiveApp(App[None]):
    """TubeArchive 인터랙티브 대시보드 앱."""

    TITLE = "TubeArchive"
    SUB_TITLE = "4K 영상 표준화 · 병합 대시보드"

    CSS = """
    TabbedContent {
        height: 1fr;
    }
    TabPane {
        padding: 0 1;
    }
    .section-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 0;
    }
    Collapsible {
        margin: 0;
        padding: 0;
        border: none;
    }
    CollapsibleTitle {
        padding: 0 1;
        background: $surface;
    }
    .stat-label {
        color: $text-muted;
        width: 24;
    }
    .stat-value {
        color: $text;
        text-style: bold;
    }
    .placeholder {
        color: $text-muted;
        text-style: italic;
        margin: 2 4;
    }
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("q", "quit", "종료"),
        Binding("1", "switch_tab('pipeline')", "Pipeline"),
        Binding("2", "switch_tab('projects')", "Projects"),
        Binding("3", "switch_tab('stats')", "Stats"),
        Binding("4", "switch_tab('history')", "History"),
        Binding("r", "refresh_data", "새로고침"),
    ]

    def __init__(
        self,
        initial_path: Path | None = None,
        config: AppConfig | None = None,
    ) -> None:
        super().__init__()
        self.initial_path = initial_path
        self.config = config or AppConfig()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="pipeline"):
            with TabPane("Pipeline", id="pipeline"):
                from tubearchive.app.tui.screens.pipeline import PipelinePane

                yield PipelinePane(initial_path=self.initial_path)
            with TabPane("Projects", id="projects"):
                from tubearchive.app.tui.screens.projects import ProjectsPane

                yield ProjectsPane()
            with TabPane("Stats", id="stats"):
                from tubearchive.app.tui.screens.stats import StatsPane

                yield StatsPane()
            with TabPane("History", id="history"):
                from tubearchive.app.tui.screens.history import HistoryPane

                yield HistoryPane()
        yield Footer()

    def action_switch_tab(self, tab_id: str) -> None:
        """탭 전환 액션."""
        self.query_one(TabbedContent).active = tab_id

    def action_refresh_data(self) -> None:
        """현재 탭 데이터 새로고침."""
        tc = self.query_one(TabbedContent)
        active = tc.active
        if active == "stats":
            from tubearchive.app.tui.screens.stats import StatsPane

            self.query_one(StatsPane).load_data()
        elif active == "projects":
            from tubearchive.app.tui.screens.projects import ProjectsPane

            self.query_one(ProjectsPane).load_data()
        elif active == "history":
            from tubearchive.app.tui.screens.history import HistoryPane

            self.query_one(HistoryPane).load_data()
