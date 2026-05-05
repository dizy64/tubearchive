"""YouTube 설정 탭 화면.

인증 상태 확인, OAuth 트리거, 영상 공개 설정, 플레이리스트 다중 선택,
파일 직접 업로드를 제공하는 독립 탭 패널.

Apply: 현재 위젯 값을 앱 공유 상태(app._youtube_applied)에 반영.
Save:  Apply 후 ~/.tubearchive/config.toml에도 영구 저장.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.widget import Widget
from textual.widgets import (
    Button,
    DirectoryTree,
    Label,
    RadioButton,
    RadioSet,
    RichLog,
    SelectionList,
    Static,
)

_CHECKING = "인증 상태 확인 중..."
_AUTHING = "🔐 브라우저에서 Google 계정으로 인증해 주세요..."

_PRIVACY_ID_MAP: dict[str, str] = {
    "public": "rb-public",
    "unlisted": "rb-unlisted",
    "private": "rb-private",
}
_ID_PRIVACY_MAP: dict[str, str] = {v: k for k, v in _PRIVACY_ID_MAP.items()}

_VIDEO_EXTS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm", ".ts", ".mts"})


def _fmt_size(path: Path) -> str:
    size: float = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


class YouTubePane(Widget):
    """YouTube 설정 탭: 인증 상태·공개 설정·플레이리스트·직접 업로드."""

    DEFAULT_CSS = """
    YouTubePane {
        height: 1fr;
    }
    #yt-body {
        height: 1fr;
    }
    #yt-fixed {
        height: auto;
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
        height: auto;
    }
    #yt-pl-count {
        margin-left: 2;
    }
    /* 직접 업로드 */
    #yt-upload-tree {
        height: 8;
        border: solid $surface-lighten-2;
        margin-bottom: 1;
    }
    #yt-upload-file {
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }
    #yt-upload-log {
        height: 6;
        border: solid $surface-lighten-2;
        margin-bottom: 1;
        display: none;
    }
    #yt-upload-bar {
        height: auto;
    }
    #yt-upload-btn {
        margin-right: 1;
    }
    /* 하단 액션 바 */
    #yt-action-bar {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }
    #yt-action-bar Button {
        margin-right: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="yt-body"):
            # 스크롤 영향 없는 고정 섹션 (RadioSet 포함) — 클릭 좌표 항상 정확
            with Vertical(id="yt-fixed"):
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

                # 공개 설정 섹션 — RadioSet은 스크롤 컨테이너 밖에 위치해야 클릭이 정확함
                with Vertical(classes="yt-section"):
                    yield Label("영상 공개 설정", classes="section-label")
                    with RadioSet(id="yt-privacy-set"):
                        yield RadioButton("Public (공개)", id="rb-public")
                        yield RadioButton("Unlisted (링크 공유)", id="rb-unlisted")
                        yield RadioButton("Private (비공개)", id="rb-private")

            # 스크롤 필요 섹션 (플레이리스트 + 직접 업로드)
            with ScrollableContainer(id="yt-scroll"):
                # 플레이리스트 섹션
                with Vertical(classes="yt-section"):
                    yield Label("추가할 플레이리스트", classes="section-label")
                    yield SelectionList[str](id="yt-playlist-list", disabled=True)
                    with Horizontal(id="yt-pl-bar"):
                        yield Button("새로고침", id="yt-refresh-playlists-btn", variant="default")
                        yield Static("선택됨: 0개", id="yt-pl-count")

                # 직접 업로드 섹션
                with Vertical(classes="yt-section"):
                    yield Label("직접 업로드", classes="section-label")
                    yield DirectoryTree(
                        str(self._upload_start_path()),
                        id="yt-upload-tree",
                    )
                    yield Static("선택된 파일: (없음)", id="yt-upload-file")
                    yield RichLog(id="yt-upload-log", max_lines=20, highlight=False, markup=True)
                    with Horizontal(id="yt-upload-bar"):
                        yield Button(
                            "업로드 시작",
                            id="yt-upload-btn",
                            variant="success",
                            disabled=True,
                        )

            # 하단 액션 바
            with Horizontal(id="yt-action-bar"):
                yield Button("Apply", id="yt-apply-btn", variant="success")
                yield Button("Save", id="yt-save-btn", variant="primary")

    # ------------------------------------------------------------------
    # 마운트 / 인증
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        self._is_authenticated: bool = False
        self._selected_upload_file: Path | None = None
        self._load_auth_status()
        self._restore_privacy_from_app_state()
        self._apply_pending_privacy()

    @staticmethod
    def _upload_start_path() -> Path:
        """DirectoryTree 시작 경로: CWD → ~/Movies → ~/Videos → ~/ 순으로 폴백."""
        cwd = Path.cwd()
        if cwd.is_dir():
            return cwd
        for candidate in (Path.home() / "Movies", Path.home() / "Videos", Path.home()):
            if candidate.exists():
                return candidate
        return Path.home()

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
        playlist_list = self.query_one("#yt-playlist-list", SelectionList)
        auth_btn = self.query_one("#yt-auth-btn", Button)
        console_btn = self.query_one("#yt-console-btn", Button)

        if status.has_valid_token:
            self._is_authenticated = True
            status_widget.update("✅ 인증 완료")
            guide_widget.update(f"토큰: {status.token_path}")
            setup_steps.display = False
            auth_btn.label = "재인증"
            auth_btn.disabled = False
            console_btn.display = False
            playlist_list.disabled = False
            self._apply_pending_privacy()
            self._load_playlists()
        elif not status.has_client_secrets:
            self._is_authenticated = False
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
            playlist_list.disabled = True
        else:
            self._is_authenticated = False
            status_widget.update("🔐 인증 필요")
            guide_widget.update(
                f"✅ {status.client_secrets_path.name} 확인됨\n"
                "'인증' 버튼을 클릭하면 브라우저가 열립니다."
            )
            setup_steps.display = False
            auth_btn.label = "인증"
            auth_btn.disabled = False
            console_btn.display = False
            playlist_list.disabled = True

        self._refresh_upload_btn()

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
    # 직접 업로드
    # ------------------------------------------------------------------

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        """DirectoryTree에서 파일 선택 시 처리."""
        path = event.path
        file_widget = self.query_one("#yt-upload-file", Static)

        if path.suffix.lower() in _VIDEO_EXTS:
            self._selected_upload_file = path
            try:
                size_str = _fmt_size(path)
            except OSError:
                size_str = "?"
            file_widget.update(f"선택됨: {path.name}  [{size_str}]")
        else:
            self._selected_upload_file = None
            file_widget.update(f"[yellow]{path.name} — 지원하지 않는 형식[/]")

        self._refresh_upload_btn()

    def _refresh_upload_btn(self) -> None:
        """업로드 버튼 활성화 조건: 인증 완료 + 비디오 파일 선택."""
        with contextlib.suppress(Exception):
            enabled = self._is_authenticated and self._selected_upload_file is not None
            self.query_one("#yt-upload-btn", Button).disabled = not enabled

    @work(thread=True, exclusive=True)
    def _do_upload(self, file_path: Path) -> None:
        """YouTubeUploader를 사용해 직접 업로드 (백그라운드)."""
        from tubearchive.infra.youtube.auth import get_authenticated_service
        from tubearchive.infra.youtube.playlist import PlaylistError, add_to_playlist
        from tubearchive.infra.youtube.uploader import YouTubeUploader, YouTubeUploadError

        log = self.query_one("#yt-upload-log", RichLog)

        def append(msg: str) -> None:
            self.app.call_from_thread(log.write, msg)

        privacy = self._get_selected_privacy()
        playlist_ids = self._get_selected_playlists() or None

        self.app.call_from_thread(self._set_upload_btn, True)

        try:
            append(f"[bold]파일:[/] {file_path.name}")
            append(f"[bold]공개 설정:[/] {privacy}")
            if playlist_ids:
                append(f"[bold]플레이리스트:[/] {len(playlist_ids)}개")

            service = get_authenticated_service()
            uploader = YouTubeUploader(service)

            last_pct: list[int] = [-1]

            def on_progress(percent: int) -> None:
                # 10% 단위로만 로그 출력
                bucket = (percent // 10) * 10
                if bucket != last_pct[0]:
                    last_pct[0] = bucket
                    filled = bucket // 5
                    bar = "█" * filled + "░" * (20 - filled)
                    self.app.call_from_thread(log.write, f"[{bar}] {bucket:3d}%")

            result = uploader.upload(
                file_path=file_path,
                title=file_path.stem,
                privacy=privacy,
                on_progress=on_progress,
            )

            append("[green bold]✅ 업로드 완료![/]")
            append(f"[link={result.url}]{result.url}[/link]")

            if playlist_ids:
                for pl_id in playlist_ids:
                    with contextlib.suppress(PlaylistError):
                        add_to_playlist(service, pl_id, result.video_id)
                append(f"[green]📋 플레이리스트({len(playlist_ids)}개) 추가 완료[/]")

            self.app.call_from_thread(self.app.notify, f"업로드 완료: {result.url}", timeout=5)

        except YouTubeUploadError as exc:
            append(f"[red bold]❌ 업로드 실패:[/] {exc}")
            self.app.call_from_thread(
                self.app.notify, f"업로드 실패: {exc}", severity="error", timeout=5
            )
        except Exception as exc:
            append(f"[red bold]❌ 오류:[/] {exc}")
            self.app.call_from_thread(self.app.notify, f"오류: {exc}", severity="error", timeout=5)
        finally:
            self.app.call_from_thread(self._set_upload_btn, False)

    def _set_upload_btn(self, disabled: bool) -> None:
        """업로드 버튼 활성/비활성 (메인 스레드에서 호출)."""
        with contextlib.suppress(Exception):
            self.query_one("#yt-upload-btn", Button).disabled = disabled

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
        elif btn_id == "yt-upload-btn":
            if self._selected_upload_file is not None:
                self.query_one("#yt-upload-log").display = True
                self._do_upload(self._selected_upload_file)
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
