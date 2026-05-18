"""외부 오디오 파일/디렉토리 선택 위젯."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, DirectoryTree, Input, Label

from tubearchive.domain.media.audio_sync import SUPPORTED_EXTERNAL_AUDIO_EXTENSIONS


class AudioDirectoryTree(DirectoryTree):
    """오디오 파일과 디렉토리만 표시하는 DirectoryTree."""

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [
            path
            for path in paths
            if not path.name.startswith(".")
            and (path.is_dir() or path.suffix.lower() in SUPPORTED_EXTERNAL_AUDIO_EXTENSIONS)
        ]


class AudioBrowserPane(Widget):
    """외부 오디오 파일/폴더를 찾아 TUI 옵션에 적용하는 패널."""

    class AudioSelected(Message):
        """오디오 경로 적용 버튼 클릭 시 부모로 전달하는 메시지."""

        def __init__(self, path: Path, target: str) -> None:
            super().__init__()
            self.path = path
            self.target = target

    DEFAULT_CSS = """
    AudioBrowserPane {
        height: auto;
        max-height: 14;
        border-bottom: solid $accent;
        padding: 0 1 1 1;
    }
    #audio-browser-title {
        color: $accent;
        text-style: bold;
    }
    #audio-browser-tree {
        height: 6;
        min-height: 4;
    }
    #audio-path-row {
        height: auto;
    }
    #audio-path-input {
        width: 1fr;
    }
    #audio-action-row {
        height: auto;
    }
    #audio-action-row Button {
        margin-right: 1;
    }
    #audio-browser-hint {
        color: $text-muted;
    }
    """

    def __init__(self, initial_path: Path | None = None) -> None:
        super().__init__()
        if initial_path is not None:
            root = initial_path if initial_path.is_dir() else initial_path.parent
        else:
            root = Path.home()
        self._tree_root = root

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("외부 오디오 선택", id="audio-browser-title")
            yield AudioDirectoryTree(str(self._tree_root), id="audio-browser-tree")
            with Horizontal(id="audio-path-row"):
                yield Input(
                    value=str(self._tree_root),
                    placeholder="~/Audio/recorder.wav 또는 ~/Audio/Takes",
                    id="audio-path-input",
                )
            with Horizontal(id="audio-action-row"):
                yield Button("단일 파일", id="audio-use-single", variant="primary")
                yield Button("긴 녹음", id="audio-use-long", variant="default")
                yield Button("후보 폴더", id="audio-use-dir", variant="default")
            yield Label(
                "단일 파일=영상 1개, 긴 녹음=여러 클립 자동 구간 매칭, 후보 폴더=자동 선택",
                id="audio-browser-hint",
            )

    def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.query_one("#audio-path-input", Input).value = str(event.path)

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.query_one("#audio-path-input", Input).value = str(event.path)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "audio-path-input":
            event.stop()
            self._post_selected("single")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        target_by_id = {
            "audio-use-single": "single",
            "audio-use-long": "long",
            "audio-use-dir": "dir",
        }
        target = target_by_id.get(event.button.id or "")
        if target is None:
            return
        event.stop()
        self._post_selected(target)

    def _post_selected(self, target: str) -> None:
        raw = self.query_one("#audio-path-input", Input).value.strip()
        if not raw:
            self.app.notify("오디오 경로를 입력하거나 선택하세요.", severity="warning", timeout=2)
            return
        path = Path(raw).expanduser()
        if target in {"single", "long"}:
            if not path.is_file():
                self.app.notify("오디오 파일을 선택하세요.", severity="warning", timeout=2)
                return
            if path.suffix.lower() not in SUPPORTED_EXTERNAL_AUDIO_EXTENSIONS:
                self.app.notify("지원되는 오디오 파일을 선택하세요.", severity="warning", timeout=2)
                return
        elif target == "dir" and not path.is_dir():
            self.app.notify("오디오 후보 디렉토리를 선택하세요.", severity="warning", timeout=2)
            return
        self.post_message(self.AudioSelected(path.resolve(), target))
