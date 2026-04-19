"""TUI 상태 → ValidatedArgs 브릿지.

``TuiOptionState`` (TUI 위젯 상태)를 ``ValidatedArgs`` (frozen dataclass)로 변환한다.
``run_pipeline()`` 은 변경 없이 그대로 사용된다.
"""

from __future__ import annotations

from pathlib import Path

from tubearchive.app.tui.models import TuiOptionState


def build_validated_args(
    targets: list[Path],
    state: TuiOptionState,
) -> object:
    """TUI 상태에서 ValidatedArgs를 생성한다.

    Args:
        targets: 파일 브라우저에서 선택된 대상 경로 목록.
        state: 옵션 패널에서 수집된 TUI 옵션 상태.

    Returns:
        실행 준비된 :class:`ValidatedArgs` 인스턴스.

    Raises:
        ValueError: targets가 비어있거나 유효하지 않은 값이 있을 경우.
    """
    from tubearchive.app.cli.main import ValidatedArgs

    if not targets:
        raise ValueError("처리할 파일이나 디렉토리를 선택하세요.")

    output_dir = _to_path_or_none(state.output_dir)
    bgm_path = _to_path_or_none(state.bgm_path)
    lut_path = _to_path_or_none(state.lut_path)
    template_intro = _to_path_or_none(state.template_intro)
    template_outro = _to_path_or_none(state.template_outro)
    archive_originals = _to_path_or_none(state.archive_originals)

    timelapse_speed: int | None = None
    if state.timelapse_speed.strip():
        try:
            timelapse_speed = int(state.timelapse_speed.strip())
        except ValueError as exc:
            raise ValueError(f"타임랩스 배속은 정수여야 합니다: {state.timelapse_speed!r}") from exc

    timelapse_resolution: str | None = state.timelapse_resolution or None

    exclude_patterns: list[str] | None = None
    if state.exclude_patterns.strip():
        exclude_patterns = [p.strip() for p in state.exclude_patterns.split(",") if p.strip()]

    include_only: list[str] | None = None
    if state.include_only_patterns.strip():
        include_only = [p.strip() for p in state.include_only_patterns.split(",") if p.strip()]

    return ValidatedArgs(
        targets=targets,
        output=None,
        output_dir=output_dir,
        no_resume=state.no_resume,
        keep_temp=state.keep_temp,
        dry_run=state.dry_run,
        # Audio
        normalize_audio=state.normalize_audio,
        denoise=state.denoise,
        denoise_level=state.denoise_level,
        # BGM
        bgm_path=bgm_path,
        bgm_volume=state.bgm_volume,
        bgm_loop=state.bgm_loop,
        # Silence
        trim_silence=state.trim_silence,
        detect_silence=state.detect_silence,
        silence_threshold=state.silence_threshold,
        silence_min_duration=state.silence_min_duration,
        # Video Effects
        stabilize=state.stabilize,
        stabilize_strength=state.stabilize_strength,
        stabilize_crop=state.stabilize_crop,
        fade_duration=state.fade_duration,
        # Color
        lut_path=lut_path,
        auto_lut=state.auto_lut,
        lut_before_hdr=state.lut_before_hdr,
        # Watermark
        watermark=state.watermark,
        watermark_text=state.watermark_text or None,
        watermark_pos=state.watermark_pos,
        watermark_size=state.watermark_size,
        watermark_color=state.watermark_color,
        watermark_alpha=state.watermark_alpha,
        # Sequence
        group_sequences=state.group_sequences,
        sort_key=state.sort_key,
        reorder=state.reorder,
        exclude_patterns=exclude_patterns,
        include_only_patterns=include_only,
        # Split
        split_duration=state.split_duration or None,
        split_size=state.split_size or None,
        # Timelapse
        timelapse_speed=timelapse_speed,
        timelapse_audio=state.timelapse_audio,
        timelapse_resolution=timelapse_resolution,
        # Thumbnail
        thumbnail=state.thumbnail,
        thumbnail_quality=state.thumbnail_quality,
        # Subtitle
        subtitle=state.subtitle,
        subtitle_model=state.subtitle_model,
        subtitle_format=state.subtitle_format,
        subtitle_burn=state.subtitle_burn,
        # Template
        template_intro=template_intro,
        template_outro=template_outro,
        # Archive
        archive_originals=archive_originals,
        archive_force=state.archive_force,
        # Upload / Project
        upload=state.upload,
        project=state.project or None,
        notify=state.notify,
        # 나머지 필드: TUI에서 미노출 → 기본값
        parallel=state.parallel,
    )


def _to_path_or_none(value: str) -> Path | None:
    """빈 문자열이면 None, 아니면 expanduser 적용 후 Path 반환."""
    stripped = value.strip()
    if not stripped:
        return None
    return Path(stripped).expanduser()
