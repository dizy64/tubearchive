"""Whisper 기반 자막 생성 유틸리티."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_SUBTITLE_FORMATS = frozenset({"srt", "vtt"})
SUPPORTED_SUBTITLE_MODELS = ("tiny", "base", "small", "medium", "large")


class SubtitleGenerationError(RuntimeError):
    """Whisper 자막 생성 실패 시 발생."""


@dataclass(frozen=True)
class SubtitleGenerationResult:
    """Whisper 자막 생성 결과."""

    subtitle_path: Path
    detected_language: str | None
    output_format: str


def _load_whisper_module() -> Any:
    """Whisper 모듈을 동적으로 로드한다."""

    try:
        return importlib.import_module("whisper")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "openai-whisper is not installed. Install with: pip install openai-whisper"
        ) from exc


def _coerce_float(value: object, default: float = 0.0) -> float:
    """세그먼트 타임스탬프를 float로 변환."""

    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _build_timestamp(seconds: float, separator: str = ",") -> str:
    """초 단위 시간을 SRT/VTT 타임스탬프 문자열로 변환."""

    total_ms = max(int(seconds * 1000), 0)
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    ms = total_ms % 1000
    return f"{hours:02}:{minutes:02}:{secs:02}{separator}{ms:03}"


def _build_srt(segments: list[dict[str, object]]) -> str:
    """Whisper 세그먼트를 SRT 문자열로 변환."""

    lines: list[str] = []
    sequence = 1
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue

        start = _coerce_float(segment.get("start", 0.0))
        end = _coerce_float(segment.get("end", start))

        lines.append(str(sequence))
        lines.append(f"{_build_timestamp(start)} --> {_build_timestamp(end)}")
        lines.append(text)
        lines.append("")
        sequence += 1

    return "\n".join(lines).strip() + "\n"


def _build_vtt(segments: list[dict[str, object]]) -> str:
    """Whisper 세그먼트를 VTT 문자열로 변환."""

    lines: list[str] = ["WEBVTT", ""]
    for segment in segments:
        text = str(segment.get("text", "")).strip()
        if not text:
            continue

        start = _coerce_float(segment.get("start", 0.0))
        end = _coerce_float(segment.get("end", start))

        lines.append(f"{_build_timestamp(start, '.')} --> {_build_timestamp(end, '.')}")
        lines.append(text)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def build_subtitle_filter(subtitle_path: Path) -> str:
    """`subtitles` 필터에 사용할 경로 문자열을 반환."""

    escaped = str(subtitle_path).replace("\\", "\\\\").replace("'", "\\'")
    return f"subtitles='{escaped}'"


def generate_subtitles(
    video_path: Path,
    *,
    model: str = "tiny",
    language: str | None = None,
    output_format: str = "srt",
    output_path: Path | None = None,
) -> SubtitleGenerationResult:
    """Whisper로 자막을 생성하고 저장 경로를 반환."""

    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    if model not in SUPPORTED_SUBTITLE_MODELS:
        raise ValueError(f"Unsupported subtitle model: {model}")

    if output_format not in SUPPORTED_SUBTITLE_FORMATS:
        raise ValueError(f"Unsupported subtitle format: {output_format}")

    whisper = _load_whisper_module()
    options: dict[str, object] = {"fp16": False}
    try:
        model_instance = whisper.load_model(model)
        if language:
            options["language"] = language
        transcribe_result = model_instance.transcribe(str(video_path), **options)
    except Exception as exc:
        raise SubtitleGenerationError(f"Failed to generate subtitle: {exc}") from exc

    output = output_path or video_path.with_suffix(f".{output_format}")
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    raw_segments = transcribe_result.get("segments", [])
    if not isinstance(raw_segments, list):
        raw_segments = []

    segments = []
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue
        segments.append(
            {
                "start": _coerce_float(segment.get("start", 0.0)),
                "end": _coerce_float(segment.get("end", 0.0)),
                "text": str(segment.get("text", "")),
            }
        )

    detected_language = transcribe_result.get("language")
    if not isinstance(detected_language, str):
        detected_language = None

    content = _build_srt(segments) if output_format == "srt" else _build_vtt(segments)

    output.write_text(content, encoding="utf-8")

    return SubtitleGenerationResult(
        subtitle_path=output,
        detected_language=detected_language,
        output_format=output_format,
    )


__all__ = [
    "SUPPORTED_SUBTITLE_FORMATS",
    "SUPPORTED_SUBTITLE_MODELS",
    "SubtitleGenerationError",
    "SubtitleGenerationResult",
    "build_subtitle_filter",
    "generate_subtitles",
]
