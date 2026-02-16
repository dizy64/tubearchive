"""Whisper 기반 자막 유틸리티 테스트."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.core.subtitle import (
    SubtitleGenerationError,
    SubtitleGenerationResult,
    _build_timestamp,
    _build_vtt,
    build_subtitle_filter,
    generate_subtitles,
)


def test_build_timestamp_keeps_milliseconds() -> None:
    """타임스탬프 계산 시 밀리초를 유지한다."""
    assert _build_timestamp(1.234) == "00:00:01,234"
    assert _build_timestamp(61.005, ".") == "00:01:01.005"


def test_build_vtt_content() -> None:
    """VTT 필터 포맷 생성이 정확해야 한다."""
    lines = _build_vtt([{"start": 0.0, "end": 1.2, "text": "Hello"}])

    assert lines == "WEBVTT\n\n00:00:00.000 --> 00:00:01.200\nHello\n"


def test_build_subtitle_filter_escapes_path() -> None:
    """`subtitles` 필터 문자열은 특수문자 이스케이프."""
    path = Path("C:\\tmp\\my video's subtitles.srt")
    assert build_subtitle_filter(path) == "subtitles='C:\\\\tmp\\\\my video\\'s subtitles.srt'"


class FakeWhisper:
    """모의 Whisper 모델."""

    def transcribe(self, _: str, **_kwargs: object) -> dict[str, object]:
        return {
            "language": "en",
            "segments": [
                {"start": 0.0, "end": 1.0, "text": "안녕하세요"},
            ],
        }


def test_generate_subtitles_writes_srt_file(tmp_path: Path) -> None:
    """자막 생성 결과가 SRT 파일로 저장된다."""
    video = tmp_path / "video.mp4"
    video.touch()
    caption = tmp_path / "output.mp4"

    fake_module = MagicMock()
    fake_module.load_model.return_value = FakeWhisper()

    with patch("tubearchive.core.subtitle._load_whisper_module", return_value=fake_module):
        result = generate_subtitles(
            video,
            model="tiny",
            output_format="srt",
            output_path=caption.with_suffix(".srt"),
        )

    assert isinstance(result, SubtitleGenerationResult)
    assert result.output_format == "srt"
    assert result.detected_language == "en"
    assert result.subtitle_path.exists()
    assert "안녕하세요" in result.subtitle_path.read_text(encoding="utf-8")


def test_generate_subtitles_writes_vtt_file(tmp_path: Path) -> None:
    """VTT 포맷 자막 파일 저장을 검증한다."""
    video = tmp_path / "video.mp4"
    video.touch()
    fake_module = MagicMock()
    fake_module.load_model.return_value = FakeWhisper()

    with patch("tubearchive.core.subtitle._load_whisper_module", return_value=fake_module):
        result = generate_subtitles(
            video,
            model="tiny",
            output_format="vtt",
            output_path=tmp_path / "subtitle.vtt",
        )

    assert result.output_format == "vtt"
    assert result.subtitle_path.suffix == ".vtt"
    text = result.subtitle_path.read_text(encoding="utf-8")
    assert text.startswith("WEBVTT")
    assert "00:00:00.000 --> 00:00:01.000" in text


def test_generate_subtitles_rejects_invalid_model(tmp_path: Path) -> None:
    """지원하지 않는 모델은 ValueError."""
    video = tmp_path / "video.mp4"
    video.touch()

    with pytest.raises(ValueError, match="Unsupported subtitle model"):
        generate_subtitles(video, model="invalid")


def test_generate_subtitles_raises_for_missing_video(tmp_path: Path) -> None:
    """없는 동영상 경로는 FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        generate_subtitles(tmp_path / "missing.mp4")


def test_generate_subtitles_wraps_transcription_error(tmp_path: Path) -> None:
    """트랜스크립트 실패 시 SubtitleGenerationError."""
    video = tmp_path / "video.mp4"
    video.touch()

    fake_module = MagicMock()
    fake_module.load_model.return_value.transcribe.side_effect = RuntimeError("boom")

    with (
        patch("tubearchive.core.subtitle._load_whisper_module", return_value=fake_module),
        pytest.raises(SubtitleGenerationError),
    ):
        generate_subtitles(video)
