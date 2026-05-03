"""YouTube 설정 탭 화면.

인증 상태 확인, OAuth 트리거, 영상 공개 설정, 플레이리스트 다중 선택을
제공하는 독립 탭 패널.

Apply: 현재 위젯 값을 앱 공유 상태(app._youtube_applied)에 반영.
Save:  Apply 후 ~/.tubearchive/config.toml에도 영구 저장.
"""

from __future__ import annotations

import contextlib
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import Button, Label, RadioButton, RadioSet, SelectionList, Static

_CHECKING = "인증 상태 확인 중..."
_AUTHING = "🔐 브라우저에서 Google 계정으로 인증해 주세요..."

_PRIVACY_ID_MAP: dict[str, str] = {
    "public": "rb-public",
    "unlisted": "rb-unlisted",
    "private": "rb-private",
}
_ID_PRIVACY_MAP: dict[str, str] = {v: k for k, v in _PRIVACY_ID_MAP.items()}


class YouTubePane(Widget):
    """YouTube 설정 탭: 인증 상태·공개 설정·플레이리스트."""

    DEFAULT_CSS = """
    YouTubePane {
        height: 1fr;
    }
    #yt-scroll {
        height: 1fr;
        overflow-y: auto;
    }
    .yt-section {
        padding: 1;
        border: solid $surface-lighten-1;
        margin: 0 0 1 0;
        height: auto;
    }
    .section-label {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }
    #yt-status-bar {
        height: auto;
        align: left middle;
        margin-bottom: 1;
    }
    #yt-status-text {
        text-style: bold;
        width: auto;
        margin-right: 2;
    }
    #yt-guide-text {
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }
    #yt-setup-steps {
        height: auto;
        color: $text-muted;
        padding: 0 1;
        margin-bottom: 1;
        border: dashed $surface-lighten-2;
        display: none;
    }
    #yt-auth-btns {
        height: auto;
        align: left middle;
    }
    #yt-auth-btn {
        margin-right: 1;
    }
    #yt-console-btn {
        margin-right: 1;
        display: none;
    }
    #yt-playlist-list {
        height: 10;
        margin-bottom: 1;
    }
    #yt-pl-bar {
        height: 3;
        align: left middle;
    }
    #yt-pl-count {
        margin-left: 2;
    }
    #yt-action-bar {
        height: 3;
        align: left middle;
        border-top: solid $accent;
        padding: 0 1;
    }
    #yt-action-bar Button {
        margin-right: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="yt-scroll"):
            # 인증 섹션
            with Vertical(classes="yt-section"):
                yield Label("인증 상태", classes="section-label")
                with Horizontal(id="yt-status-bar"):
                    yield Static(_CHECKING, id="yt-status-text")
                yield Static("", id="yt-guide-text")
                yield Static("", id="yt-setup-steps")
                with Horizontal(id="yt-auth-btns"):
                    yield Button("인증", id="yt-auth-btn", variant="primary")
                    yield Button(
                        "Google Cloud Console 열기",
                        id="yt-console-btn",
                        variant="warning",
                    )
                    yield Button("새로고침", id="yt-refresh-status-btn", variant="default")

            # 공개 설정 섹션
            with Vertical(classes="yt-section"):
                yield Label("영상 공개 설정", classes="section-label")
                with RadioSet(id="yt-privacy-set", disabled=True):
                    yield RadioButton("Public (공개)", id="rb-public")
                    yield RadioButton("Unlisted (링크 공유)", id="rb-unlisted")
                    yield RadioButton("Private (비공개)", id="rb-private")

            # 플레이리스트 섹션
            with Vertical(classes="yt-section"):
                yield Label("추가할 플레이리스트", classes="section-label")
                yield SelectionList[str](id="yt-playlist-list", disabled=True)
                with Horizontal(id="yt-pl-bar"):
                    yield Button("새로고침", id="yt-refresh-playlists-btn", variant="default")
                    yield Static("선택됨: 0개", id="yt-pl-count")

        # 하단 액션 바
        with Horizontal(id="yt-action-bar"):
            yield Button("Apply", id="yt-apply-btn", variant="success")
            yield Button("Save", id="yt-save-btn", variant="primary")

    # ------------------------------------------------------------------
    # 마운트 / 인증
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._load_auth_status()
        self._restore_privacy_from_app_state()

    def _restore_privacy_from_app_state(self) -> None:
        """앱 공유 상태의 upload_privacy로 라디오 버튼 초기화."""
        privacy = self._app_youtube_applied.get("upload_privacy", "unlisted")
        self._pending_privacy = str(privacy)

    @property
    def _app_youtube_applied(self) -> dict[str, Any]:
        return getattr(self.app, "_youtube_applied", {})

    @work(thread=True)
    def _load_auth_status(self) -> None:
        from tubearchive.infra.youtube.auth import check_auth_status

        try:
            status = check_auth_status()
        except Exception as exc:
            self.app.call_from_thread(
                self.query_one("#yt-status-text", Static).update,
                f"⚠️ 상태 확인 실패: {exc}",
            )
            return
        self.app.call_from_thread(self._update_auth_ui, status)

    def _update_auth_ui(self, status: object) -> None:
        """인증 상태에 맞게 UI 업데이트 (메인 스레드)."""
        from tubearchive.infra.youtube.auth import AuthStatus

        if not isinstance(status, AuthStatus):
            return

        status_widget = self.query_one("#yt-status-text", Static)
        guide_widget = self.query_one("#yt-guide-text", Static)
        setup_steps = self.query_one("#yt-setup-steps", Static)
        privacy_set = self.query_one("#yt-privacy-set", RadioSet)
        playlist_list = self.query_one("#yt-playlist-list", SelectionList)
        auth_btn = self.query_one("#yt-auth-btn", Button)
        console_btn = self.query_one("#yt-console-btn", Button)

        if status.has_valid_token:
            status_widget.update("✅ 인증 완료")
            guide_widget.update(f"토큰: {status.token_path}")
            setup_steps.display = False
            auth_btn.label = "재인증"
            auth_btn.disabled = False
            console_btn.display = False
            privacy_set.disabled = False
            playlist_list.disabled = False
            self._apply_pending_privacy()
            self._load_playlists()
        elif not status.has_client_secrets:
            status_widget.update("❌ 설정 필요")
            guide_widget.update(
                f"client_secrets.json이 없습니다. 다음 경로에 저장하세요:\n"
                f"  {status.client_secrets_path}"
            )
            setup_steps.update(
                "1. Google Cloud Console 접속 (오른쪽 버튼)\n"
                "2. 프로젝트 생성 → YouTube Data API v3 활성화\n"
                "3. APIs & Services → Credentials\n"
                "4. + CREATE CREDENTIALS → OAuth client ID (Desktop app)\n"
                "5. JSON 다운로드 후 위 경로에 저장\n"
                "6. '새로고침' 버튼 클릭"
            )
            setup_steps.display = True
            auth_btn.label = "인증"
            auth_btn.disabled = True
            console_btn.display = True
            privacy_set.disabled = True
            playlist_list.disabled = True
        else:
            status_widget.update("🔐 인증 필요")
            guide_widget.update(
                f"✅ {status.client_secrets_path.name} 확인됨\n"
                "'인증' 버튼을 클릭하면 브라우저가 열립니다."
            )
            setup_steps.display = False
            auth_btn.label = "인증"
            auth_btn.disabled = False
            console_btn.display = False
            privacy_set.disabled = True
            playlist_list.disabled = True

    def _apply_pending_privacy(self) -> None:
        """_pending_privacy 값에 맞는 RadioButton을 선택."""
        privacy = getattr(self, "_pending_privacy", "unlisted")
        btn_id = _PRIVACY_ID_MAP.get(privacy, "rb-unlisted")
        with contextlib.suppress(Exception):
            self.query_one(f"#{btn_id}", RadioButton).value = True

    @work(thread=True)
    def _do_auth(self) -> None:
        """브라우저 OAuth 인증 플로우 (백그라운드)."""
        from tubearchive.infra.youtube.auth import (
            check_auth_status,
            get_client_secrets_path,
            get_token_path,
            run_auth_flow,
            save_credentials,
        )

        self.app.call_from_thread(self.query_one("#yt-status-text", Static).update, _AUTHING)
        try:
            secrets_path = get_client_secrets_path()
            credentials = run_auth_flow(secrets_path)
            save_credentials(credentials, get_token_path())
            status = check_auth_status()
            self.app.call_from_thread(self._update_auth_ui, status)
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify, f"인증 실패: {exc}", severity="error", timeout=5
            )
            self.app.call_from_thread(
                self.query_one("#yt-status-text", Static).update, "❌ 인증 실패"
            )

    # ------------------------------------------------------------------
    # 플레이리스트
    # ------------------------------------------------------------------

    @work(thread=True)
    def _load_playlists(self) -> None:
        from tubearchive.infra.youtube.auth import get_authenticated_service
        from tubearchive.infra.youtube.playlist import list_playlists

        try:
            service = get_authenticated_service()
            playlists = list_playlists(service)
        except Exception as exc:
            self.app.call_from_thread(
                self.app.notify,
                f"플레이리스트 로드 실패: {exc}",
                severity="warning",
                timeout=4,
            )
            return
        self.app.call_from_thread(self._update_playlists, playlists)

    def _update_playlists(self, playlists: list[object]) -> None:
        """플레이리스트 목록 UI 업데이트 (메인 스레드)."""
        sl = self.query_one("#yt-playlist-list", SelectionList)

        # Textual 8.x: selected는 인덱스 목록을 반환하므로 값으로 변환
        prev_selected: set[str] = set()
        with contextlib.suppress(Exception):
            prev_selected = {str(sl.get_option_at_index(i).value) for i in sl.selected}
        prev_selected |= set(self._app_youtube_applied.get("upload_playlists", []))

        sl.clear_options()
        for pl in playlists:
            sl.add_option(
                (
                    f"{getattr(pl, 'title', str(pl))} ({getattr(pl, 'item_count', 0)}개)",
                    getattr(pl, "id", str(pl)),
                )
            )

        for i, pl in enumerate(playlists):
            if getattr(pl, "id", None) in prev_selected:
                sl.select(i)

        self._refresh_count()

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged[str]) -> None:
        self._refresh_count()

    def _refresh_count(self) -> None:
        try:
            count = len(self.query_one("#yt-playlist-list", SelectionList).selected)
        except Exception:
            count = 0
        self.query_one("#yt-pl-count", Static).update(f"선택됨: {count}개")

    # ------------------------------------------------------------------
    # Apply / Save
    # ------------------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "yt-auth-btn":
            self._do_auth()
        elif btn_id == "yt-console-btn":
            self._open_console()
        elif btn_id == "yt-refresh-status-btn":
            self.query_one("#yt-status-text", Static).update(_CHECKING)
            self._load_auth_status()
        elif btn_id == "yt-refresh-playlists-btn":
            self._load_playlists()
        elif btn_id == "yt-apply-btn":
            self._apply_state()
            self.app.notify("적용됨", timeout=2)
        elif btn_id == "yt-save-btn":
            self._apply_state()
            self._save_to_config()

    def _open_console(self) -> None:
        """브라우저에서 Google Cloud Console을 연다."""
        import webbrowser

        from tubearchive.infra.youtube.auth import GOOGLE_CLOUD_CONSOLE_URL

        webbrowser.open(GOOGLE_CLOUD_CONSOLE_URL)
        self.app.notify("브라우저에서 Google Cloud Console을 열었습니다.", timeout=3)

    def _apply_state(self) -> None:
        """위젯 값을 앱 공유 상태에 반영."""
        applied: dict[str, Any] = getattr(self.app, "_youtube_applied", {})
        applied["upload_privacy"] = self._get_selected_privacy()
        applied["upload_playlists"] = self._get_selected_playlists()
        self.app._youtube_applied = applied  # type: ignore[attr-defined]

    def _get_selected_privacy(self) -> str:
        try:
            pressed = self.query_one("#yt-privacy-set", RadioSet).pressed_button
            if pressed is None:
                return "unlisted"
            return _ID_PRIVACY_MAP.get(str(pressed.id), "unlisted")
        except Exception:
            return "unlisted"

    def _get_selected_playlists(self) -> list[str]:
        try:
            sl = self.query_one("#yt-playlist-list", SelectionList)
            # Textual 8.x: selected는 인덱스 목록을 반환하므로 실제 값으로 변환
            return [str(sl.get_option_at_index(i).value) for i in sl.selected]
        except Exception:
            return []

    def _save_to_config(self) -> None:
        from tubearchive.config import AppConfig, YouTubeConfig, save_config

        privacy = self._get_selected_privacy()
        playlists = self._get_selected_playlists()
        try:
            save_config(
                AppConfig(youtube=YouTubeConfig(upload_privacy=privacy, playlist=playlists))
            )
            self.app.notify("config.toml에 저장됨", timeout=3)
        except Exception as exc:
            self.app.notify(f"저장 실패: {exc}", severity="error", timeout=5)
