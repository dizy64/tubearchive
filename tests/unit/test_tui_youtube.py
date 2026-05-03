"""YouTubePane 단위 테스트.

인증 상태별 UI 표시, Apply/Save 공유 상태 반영, 플레이리스트 로드,
브릿지 YouTube 옵션 전달을 검증한다.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, RadioSet, SelectionList, Static

from tubearchive.app.tui.screens.youtube import YouTubePane

# ------------------------------------------------------------------ #
# 테스트 헬퍼                                                          #
# ------------------------------------------------------------------ #


def _auth_status(*, valid: bool, has_secrets: bool = True) -> object:
    """AuthStatus 인스턴스를 생성한다."""
    from tubearchive.infra.youtube.auth import AuthStatus

    return AuthStatus(
        has_valid_token=valid,
        has_client_secrets=has_secrets,
        needs_browser_auth=not valid,
        client_secrets_path=Path("/tmp/client_secrets.json"),
        token_path=Path("/tmp/youtube_token.json"),
    )


def _playlist(id_: str, title: str, count: int = 0) -> object:
    """SelectionList 표시용 플레이리스트 stub 객체를 생성한다."""
    return types.SimpleNamespace(id=id_, title=title, item_count=count)


def _static_content(app: App[None], widget_id: str) -> str:
    """Static 위젯의 텍스트 내용을 반환한다 (Textual 8.x: .content 공개 속성)."""
    return str(app.query_one(f"#{widget_id}", Static).content)


def _press_button(app: App[None], button_id: str) -> None:
    """Button을 프로그래밍 방식으로 누른다.

    Textual 8.x에서 pilot.click()이 ScrollableContainer 내부 위젯에
    대해 불안정하므로 Button.press()를 직접 호출한다.
    """
    app.query_one(f"#{button_id}", Button).press()


def _select_radio(app: App[None], radio_id: str) -> None:
    """RadioButton을 선택한다 (value = True 설정)."""
    from textual.widgets import RadioButton

    app.query_one(f"#{radio_id}", RadioButton).value = True


class _App(App[None]):
    """YouTubePane 테스트용 최소 앱."""

    def compose(self) -> ComposeResult:
        yield YouTubePane()


# ------------------------------------------------------------------ #
# 인증 상태 표시                                                       #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_pane_shows_authenticated_state() -> None:
    """인증 완료 상태에서 상태 라벨이 '인증 완료'이고 RadioSet/SelectionList가 활성화된다."""
    status = _auth_status(valid=True)
    with (
        patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=status),
        patch("tubearchive.infra.youtube.auth.get_authenticated_service", return_value=MagicMock()),
        patch("tubearchive.infra.youtube.playlist.list_playlists", return_value=[]),
    ):
        app = _App()
        async with app.run_test(headless=True, size=(120, 40)):
            await app.workers.wait_for_complete()
            assert "인증 완료" in _static_content(app, "yt-status-text")
            assert not app.query_one("#yt-privacy-set", RadioSet).disabled
            assert not app.query_one("#yt-playlist-list", SelectionList).disabled


@pytest.mark.asyncio
async def test_pane_shows_unauthenticated_no_secrets() -> None:
    """클라이언트 시크릿이 없을 때 '설정 필요' 라벨, 저장 경로, 설정 단계가 표시된다."""
    status = _auth_status(valid=False, has_secrets=False)
    with patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=status):
        app = _App()
        async with app.run_test(headless=True, size=(120, 40)):
            await app.workers.wait_for_complete()
            assert "설정 필요" in _static_content(app, "yt-status-text")
            assert "client_secrets.json" in _static_content(app, "yt-guide-text")
            assert "Google Cloud Console" in _static_content(app, "yt-setup-steps")
            assert app.query_one("#yt-console-btn", Button).display
            assert app.query_one("#yt-privacy-set", RadioSet).disabled
            assert app.query_one("#yt-playlist-list", SelectionList).disabled


@pytest.mark.asyncio
async def test_pane_shows_unauthenticated_needs_auth() -> None:
    """시크릿은 있지만 토큰이 없을 때 '인증 필요' 라벨이 표시된다."""
    status = _auth_status(valid=False, has_secrets=True)
    with patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=status):
        app = _App()
        async with app.run_test(headless=True, size=(120, 40)):
            await app.workers.wait_for_complete()
            assert "인증 필요" in _static_content(app, "yt-status-text")
            assert app.query_one("#yt-privacy-set", RadioSet).disabled
            assert app.query_one("#yt-playlist-list", SelectionList).disabled


# ------------------------------------------------------------------ #
# Apply / 공유 상태                                                    #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_apply_updates_shared_state() -> None:
    """Apply 클릭 시 app._youtube_applied에 privacy와 playlists가 반영된다."""
    status = _auth_status(valid=True)
    playlists = [_playlist("pl-1", "브이로그"), _playlist("pl-2", "여행")]
    with (
        patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=status),
        patch("tubearchive.infra.youtube.auth.get_authenticated_service", return_value=MagicMock()),
        patch("tubearchive.infra.youtube.playlist.list_playlists", return_value=playlists),
    ):
        app = _App()
        async with app.run_test(headless=True, size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()

            # public 라디오버튼 선택
            _select_radio(app, "rb-public")
            await pilot.pause()

            # 첫 번째 플레이리스트 선택
            app.query_one("#yt-playlist-list", SelectionList).select(0)

            # Apply
            _press_button(app, "yt-apply-btn")
            await pilot.pause()

            applied = getattr(app, "_youtube_applied", {})
            assert applied.get("upload_privacy") == "public"
            assert "pl-1" in applied.get("upload_playlists", [])


@pytest.mark.asyncio
async def test_apply_without_pressing_does_not_mutate_state() -> None:
    """Apply를 누르지 않으면 app._youtube_applied가 설정되지 않는다."""
    status = _auth_status(valid=True)
    with (
        patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=status),
        patch("tubearchive.infra.youtube.auth.get_authenticated_service", return_value=MagicMock()),
        patch("tubearchive.infra.youtube.playlist.list_playlists", return_value=[]),
    ):
        app = _App()
        async with app.run_test(headless=True, size=(120, 40)):
            await app.workers.wait_for_complete()
            applied = getattr(app, "_youtube_applied", {})
            assert "upload_privacy" not in applied
            assert "upload_playlists" not in applied


# ------------------------------------------------------------------ #
# Save                                                                #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_save_calls_save_config() -> None:
    """Save 클릭 시 tubearchive.config.save_config가 호출된다."""
    status = _auth_status(valid=True)
    mock_save = MagicMock(return_value=Path("/tmp/config.toml"))
    with (
        patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=status),
        patch("tubearchive.infra.youtube.auth.get_authenticated_service", return_value=MagicMock()),
        patch("tubearchive.infra.youtube.playlist.list_playlists", return_value=[]),
        patch("tubearchive.config.save_config", mock_save),
    ):
        app = _App()
        async with app.run_test(headless=True, size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            _press_button(app, "yt-save-btn")
            await pilot.pause()
            mock_save.assert_called_once()


@pytest.mark.asyncio
async def test_save_passes_correct_privacy() -> None:
    """Save 클릭 시 선택된 privacy 값이 save_config에 전달된다."""
    status = _auth_status(valid=True)
    saved_configs: list[object] = []

    def _capture(config: object, **_: object) -> Path:
        saved_configs.append(config)
        return Path("/tmp/config.toml")

    with (
        patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=status),
        patch("tubearchive.infra.youtube.auth.get_authenticated_service", return_value=MagicMock()),
        patch("tubearchive.infra.youtube.playlist.list_playlists", return_value=[]),
        patch("tubearchive.config.save_config", side_effect=_capture),
    ):
        app = _App()
        async with app.run_test(headless=True, size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            _select_radio(app, "rb-private")
            await pilot.pause()
            _press_button(app, "yt-save-btn")
            await pilot.pause()

    assert saved_configs, "save_config가 호출되지 않음"
    from tubearchive.config import AppConfig

    assert isinstance(saved_configs[0], AppConfig)
    assert saved_configs[0].youtube.upload_privacy == "private"  # type: ignore[union-attr]


# ------------------------------------------------------------------ #
# 플레이리스트 새로고침                                                  #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_refresh_playlists_button_calls_list_playlists() -> None:
    """플레이리스트 새로고침 버튼 클릭 시 list_playlists가 추가로 호출된다."""
    status = _auth_status(valid=True)
    mock_list = MagicMock(return_value=[_playlist("pl-1", "브이로그", 10)])
    with (
        patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=status),
        patch("tubearchive.infra.youtube.auth.get_authenticated_service", return_value=MagicMock()),
        patch("tubearchive.infra.youtube.playlist.list_playlists", mock_list),
    ):
        app = _App()
        async with app.run_test(headless=True, size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            count_before = mock_list.call_count
            _press_button(app, "yt-refresh-playlists-btn")
            await pilot.pause()  # 버튼 이벤트 처리 후 worker 시작 대기
            await app.workers.wait_for_complete()
            assert mock_list.call_count > count_before


# ------------------------------------------------------------------ #
# 인증 버튼                                                            #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_auth_button_invokes_flow() -> None:
    """인증 버튼 클릭 시 run_auth_flow가 1회 호출된다."""
    initial_status = _auth_status(valid=False, has_secrets=True)
    post_auth_status = _auth_status(valid=True)
    mock_flow = MagicMock(return_value=MagicMock())
    mock_check = MagicMock(side_effect=[initial_status, post_auth_status])

    with (
        patch("tubearchive.infra.youtube.auth.check_auth_status", mock_check),
        patch(
            "tubearchive.infra.youtube.auth.get_client_secrets_path",
            return_value=Path("/tmp/cs.json"),
        ),
        patch("tubearchive.infra.youtube.auth.get_token_path", return_value=Path("/tmp/tok.json")),
        patch("tubearchive.infra.youtube.auth.run_auth_flow", mock_flow),
        patch("tubearchive.infra.youtube.auth.save_credentials"),
        patch("tubearchive.infra.youtube.auth.get_authenticated_service", return_value=MagicMock()),
        patch("tubearchive.infra.youtube.playlist.list_playlists", return_value=[]),
    ):
        app = _App()
        async with app.run_test(headless=True, size=(120, 40)) as pilot:
            await app.workers.wait_for_complete()
            _press_button(app, "yt-auth-btn")
            await pilot.pause()  # 버튼 이벤트 처리 후 worker 시작 대기
            await app.workers.wait_for_complete()
            mock_flow.assert_called_once()


# ------------------------------------------------------------------ #
# Bridge                                                              #
# ------------------------------------------------------------------ #


def test_bridge_passes_youtube_options() -> None:
    """TuiOptionState의 YouTube 옵션이 build_validated_args()를 통해 ValidatedArgs에 전달된다."""
    from tubearchive.app.cli.main import ValidatedArgs
    from tubearchive.app.tui.bridge import build_validated_args
    from tubearchive.app.tui.models import TuiOptionState

    state = TuiOptionState(upload_privacy="public", upload_playlists=["pl-1", "pl-2"])
    result = build_validated_args([Path("/tmp/test.mp4")], state)

    assert isinstance(result, ValidatedArgs)
    assert result.upload_privacy == "public"
    assert result.playlist == ["pl-1", "pl-2"]


def test_bridge_empty_playlists_becomes_none() -> None:
    """빈 upload_playlists는 playlist=None으로 변환된다."""
    from tubearchive.app.tui.bridge import build_validated_args
    from tubearchive.app.tui.models import TuiOptionState

    state = TuiOptionState(upload_privacy="private", upload_playlists=[])
    result = build_validated_args([Path("/tmp/test.mp4")], state)

    assert result.upload_privacy == "private"
    assert result.playlist is None


def test_bridge_default_youtube_options() -> None:
    """기본 TuiOptionState의 YouTube 필드가 ValidatedArgs 기본값과 일치한다."""
    from tubearchive.app.tui.bridge import build_validated_args
    from tubearchive.app.tui.models import TuiOptionState

    state = TuiOptionState()
    result = build_validated_args([Path("/tmp/test.mp4")], state)

    assert result.upload_privacy == "unlisted"
    assert result.playlist is None
