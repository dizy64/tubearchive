"""TUI 앱 단위 테스트.

Textual의 비동기 테스트 러너를 사용하여 앱 부팅, 탭 전환, 화면 구성을 검증한다.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.app.tui.app import TubeArchiveApp
from tubearchive.app.tui.screens.history import HistoryPane
from tubearchive.app.tui.screens.pipeline import PipelinePane
from tubearchive.app.tui.screens.projects import ProjectsPane
from tubearchive.app.tui.screens.stats import StatsPane


@pytest.mark.asyncio
async def test_app_mounts_all_tabs() -> None:
    """앱이 4개 탭 패널을 모두 마운트하는지 확인한다."""
    app = TubeArchiveApp()
    async with app.run_test(headless=True, size=(120, 40)):
        # 4개 Pane 모두 DOM에 존재해야 함
        assert app.query_one(PipelinePane)
        assert app.query_one(StatsPane)
        assert app.query_one(ProjectsPane)
        assert app.query_one(HistoryPane)


@pytest.mark.asyncio
async def test_app_title() -> None:
    """앱 타이틀이 올바르게 설정되는지 확인한다."""
    app = TubeArchiveApp()
    async with app.run_test(headless=True, size=(120, 40)):
        assert app.TITLE == "TubeArchive"


@pytest.mark.asyncio
async def test_app_with_initial_path() -> None:
    """initial_path가 PipelinePane에 전달되는지 확인한다."""
    test_path = Path("/tmp/test_videos")
    app = TubeArchiveApp(initial_path=test_path)
    async with app.run_test(headless=True, size=(120, 40)):
        pane = app.query_one(PipelinePane)
        assert pane.initial_path == test_path


@pytest.mark.asyncio
async def test_tab_switch_via_action() -> None:
    """탭 전환 액션이 올바르게 동작하는지 확인한다."""
    from textual.widgets import TabbedContent

    app = TubeArchiveApp()
    async with app.run_test(headless=True, size=(120, 40)):
        await app.run_action("switch_tab('stats')")
        tc = app.query_one(TabbedContent)
        assert tc.active == "stats"

        await app.run_action("switch_tab('history')")
        assert tc.active == "history"


@pytest.mark.asyncio
async def test_stats_pane_loads_without_db_error() -> None:
    """Stats Pane이 빈 DB에서도 에러 없이 로드되는지 확인한다."""
    from tubearchive.app.queries.stats import (
        ArchiveStats,
        MergeStats,
        StatsData,
        TranscodingStats,
    )

    mock_data = StatsData(
        period=None,
        total_videos=0,
        total_duration=0.0,
        devices=[],
        transcoding=TranscodingStats(
            completed=0, failed=0, pending=0, processing=0, total=0, avg_encoding_speed=None
        ),
        merge=MergeStats(
            total=0, completed=0, failed=0, uploaded=0, total_size_bytes=0, total_duration=0.0
        ),
        archive=ArchiveStats(moved=0, deleted=0),
    )

    with patch("tubearchive.app.queries.stats.fetch_stats", return_value=mock_data):
        app = TubeArchiveApp()
        async with app.run_test(headless=True, size=(120, 40)):
            await app.run_action("switch_tab('stats')")
            pane = app.query_one(StatsPane)
            assert pane is not None


@pytest.mark.asyncio
async def test_projects_pane_empty_db() -> None:
    """Projects Pane이 빈 DB에서 오류 없이 표시되는지 확인한다."""
    from textual.widgets import DataTable

    mock_repo = MagicMock()
    mock_repo.get_all_with_stats.return_value = []

    with patch("tubearchive.infra.db.repository.ProjectRepository", return_value=mock_repo):
        app = TubeArchiveApp()
        async with app.run_test(headless=True, size=(120, 40)):
            await app.run_action("switch_tab('projects')")
            pane = app.query_one(ProjectsPane)
            table = pane.query_one("#projects-table", DataTable)
            assert table is not None


@pytest.mark.asyncio
async def test_history_pane_empty_db() -> None:
    """History Pane이 빈 DB에서 오류 없이 표시되는지 확인한다."""
    from textual.widgets import DataTable

    with patch("tubearchive.app.queries.catalog.fetch_catalog_items", return_value=[]):
        app = TubeArchiveApp()
        async with app.run_test(headless=True, size=(120, 40)):
            await app.run_action("switch_tab('history')")
            pane = app.query_one(HistoryPane)
            table = pane.query_one("#history-table", DataTable)
            assert table is not None
