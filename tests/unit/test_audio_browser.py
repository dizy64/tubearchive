"""TUI 외부 오디오 브라우저 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from tubearchive.app.tui.widgets.audio_browser import AudioBrowserPane, AudioDirectoryTree


def test_audio_directory_tree_filters_audio_files_and_directories(tmp_path: Path) -> None:
    """오디오 브라우저 트리는 오디오 파일과 디렉토리만 표시한다."""
    wav = tmp_path / "take.wav"
    mp3 = tmp_path / "guide.mp3"
    video = tmp_path / "clip.mp4"
    hidden = tmp_path / ".hidden.wav"
    nested = tmp_path / "takes"
    wav.touch()
    mp3.touch()
    video.touch()
    hidden.touch()
    nested.mkdir()

    tree = object.__new__(AudioDirectoryTree)
    result = list(tree.filter_paths([wav, mp3, video, hidden, nested]))

    assert result == [wav, mp3, nested]


class _AudioBrowserTestApp(App[None]):
    """AudioBrowserPane 메시지 캡처용 테스트 앱."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.selected: list[tuple[Path, str]] = []

    def compose(self) -> ComposeResult:
        yield AudioBrowserPane(initial_path=self.root)

    def on_audio_browser_pane_audio_selected(
        self,
        event: AudioBrowserPane.AudioSelected,
    ) -> None:
        self.selected.append((event.path, event.target))


@pytest.mark.asyncio
async def test_audio_browser_posts_single_file_selection(tmp_path: Path) -> None:
    """단일 파일 버튼은 external_audio_path에 넣을 파일 선택 메시지를 보낸다."""
    wav = tmp_path / "recorder.wav"
    wav.touch()
    app = _AudioBrowserTestApp(tmp_path)

    async with app.run_test(headless=True, size=(100, 24)) as pilot:
        pane = app.query_one(AudioBrowserPane)
        app.query_one("#audio-path-input", Input).value = str(wav)

        pane._post_selected("single")
        await pilot.pause()

    assert app.selected == [(wav.resolve(), "single")]


@pytest.mark.asyncio
async def test_audio_browser_posts_long_recording_selection(tmp_path: Path) -> None:
    """긴 녹음 버튼은 external_audio_scope=long에 대응하는 메시지를 보낸다."""
    wav = tmp_path / "long.wav"
    wav.touch()
    app = _AudioBrowserTestApp(tmp_path)

    async with app.run_test(headless=True, size=(100, 24)) as pilot:
        pane = app.query_one(AudioBrowserPane)
        app.query_one("#audio-path-input", Input).value = str(wav)

        pane._post_selected("long")
        await pilot.pause()

    assert app.selected == [(wav.resolve(), "long")]


@pytest.mark.asyncio
async def test_audio_browser_posts_directory_selection(tmp_path: Path) -> None:
    """후보 폴더 버튼은 external_audio_dir에 넣을 디렉토리 선택 메시지를 보낸다."""
    app = _AudioBrowserTestApp(tmp_path)

    async with app.run_test(headless=True, size=(100, 24)) as pilot:
        pane = app.query_one(AudioBrowserPane)
        app.query_one("#audio-path-input", Input).value = str(tmp_path)

        pane._post_selected("dir")
        await pilot.pause()

    assert app.selected == [(tmp_path.resolve(), "dir")]
