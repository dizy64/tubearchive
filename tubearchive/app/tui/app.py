"""TubeArchive TUI 메인 앱.

Textual 기반 4탭 대시보드: Pipeline / Projects / Stats / History.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from tubearchive.config import AppConfig

if TYPE_CHECKING:
    from tubearchive.app.tui.models import TuiOptionState


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
    /* 프리셋 모달 */
    #preset-dialog {
        width: 60;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
        align: center middle;
    }
    #preset-dialog Label {
        margin-bottom: 1;
    }
    #preset-dialog Input {
        margin-bottom: 1;
    }
    #preset-dialog ListView {
        height: 8;
        margin-bottom: 1;
    }
    #dialog-buttons {
        height: 3;
        align: right middle;
    }
    #dialog-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("q", "quit", "종료"),
        Binding("1", "switch_tab('pipeline')", "Pipeline"),
        Binding("2", "switch_tab('projects')", "Projects"),
        Binding("3", "switch_tab('stats')", "Stats"),
        Binding("4", "switch_tab('history')", "History"),
        Binding("r", "refresh_data", "새로고침"),
        Binding("t", "toggle_dark", "테마 전환"),
    ]

    def __init__(
        self,
        initial_path: Path | None = None,
        config: AppConfig | None = None,
    ) -> None:
        super().__init__()
        self.initial_path = initial_path
        self.config = config or AppConfig()
        self._initial_state: TuiOptionState | None = self._make_initial_state()

    def _make_initial_state(self) -> TuiOptionState | None:
        """config에서 초기 TuiOptionState를 생성한다."""
        from tubearchive.app.tui.models import state_from_config

        return state_from_config(self.config)

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="pipeline"):
            with TabPane("Pipeline", id="pipeline"):
                from tubearchive.app.tui.screens.pipeline import PipelinePane

                yield PipelinePane(
                    initial_path=self.initial_path,
                    initial_state=self._initial_state,
                )
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

    # ------------------------------------------------------------------
    # 프리셋 저장/불러오기
    # ------------------------------------------------------------------

    def action_save_preset(self) -> None:
        """현재 옵션 패널 상태를 프리셋으로 저장한다."""
        from tubearchive.app.tui.screens.presets import SavePresetScreen
        from tubearchive.app.tui.widgets.option_panels import OptionsPane

        try:
            state = self.query_one(OptionsPane).collect_state()
        except Exception:
            self.notify("옵션 패널을 찾을 수 없습니다.", severity="warning", timeout=2)
            return

        def _on_name(name: str | None) -> None:
            if not name:
                return
            from tubearchive.app.tui.models import save_preset

            save_preset(name, state)
            self.notify(f"프리셋 '{name}' 저장됨", timeout=3)

        self.push_screen(SavePresetScreen(), _on_name)

    def action_load_preset(self) -> None:
        """저장된 프리셋을 선택해 옵션 패널에 적용한다."""
        from tubearchive.app.tui.models import list_presets
        from tubearchive.app.tui.screens.presets import LoadPresetScreen
        from tubearchive.app.tui.widgets.option_panels import OptionsPane

        presets = list_presets()
        if not presets:
            self.notify("저장된 프리셋이 없습니다.", severity="warning", timeout=3)
            return

        def _on_state(state: TuiOptionState | None) -> None:
            if state is None:
                return
            try:
                self.query_one(OptionsPane).apply_state(state)
                self.notify("프리셋 적용됨", timeout=3)
            except Exception:
                self.notify("프리셋 적용 실패", severity="error", timeout=3)

        self.push_screen(LoadPresetScreen(presets), _on_state)
