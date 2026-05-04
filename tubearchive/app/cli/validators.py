"""CLI 검증 모듈 — ValidatedArgs dataclass 및 CLI 인자 검증."""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path

from tubearchive.app.cli.parser import parse_schedule_datetime
from tubearchive.config import (
    HooksConfig,
    get_default_auto_lut,
    get_default_backup_include_originals,
    get_default_backup_remote,
    get_default_bgm_loop,
    get_default_bgm_path,
    get_default_bgm_volume,
    get_default_denoise,
    get_default_denoise_level,
    get_default_fade_duration,
    get_default_group_sequences,
    get_default_normalize_audio,
    get_default_notify,
    get_default_output_dir,
    get_default_parallel,
    get_default_stabilize,
    get_default_stabilize_crop,
    get_default_stabilize_strength,
    get_default_subtitle_burn,
    get_default_subtitle_format,
    get_default_subtitle_lang,
    get_default_subtitle_model,
    get_default_template_intro,
    get_default_template_outro,
    get_default_watch_log_path,
    get_default_watch_paths,
    get_default_watch_poll_interval,
    get_default_watch_stability_checks,
)
from tubearchive.domain.media.subtitle import (
    SUPPORTED_SUBTITLE_FORMATS,
    SUPPORTED_SUBTITLE_MODELS,
    SubtitleFormat,
    SubtitleModel,
)
from tubearchive.infra.ffmpeg.effects import LUT_SUPPORTED_EXTENSIONS
from tubearchive.shared.validators import ValidationError

logger = logging.getLogger(__name__)

SUPPORTED_THUMBNAIL_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})


@dataclass
class ValidatedArgs:
    """검증된 CLI 인자.

    ``argparse.Namespace`` 를 타입 안전하게 변환한 데이터클래스.
    :func:`validate_args` 에서 생성된다.
    """

    targets: list[Path]
    output: Path | None
    output_dir: Path | None
    no_resume: bool
    keep_temp: bool
    dry_run: bool
    denoise: bool = False
    denoise_level: str = "medium"
    normalize_audio: bool = False
    group_sequences: bool = True
    fade_duration: float = 0.5
    upload: bool = False
    parallel: int = 1
    thumbnail: bool = False
    thumbnail_timestamps: list[str] | None = None
    thumbnail_quality: int = 2
    set_thumbnail: Path | None = None
    generated_thumbnail_paths: list[Path] | None = None
    detect_silence: bool = False
    trim_silence: bool = False
    silence_threshold: str = "-30dB"
    silence_min_duration: float = 2.0
    subtitle: bool = False
    subtitle_model: str = "tiny"
    subtitle_format: str = "srt"
    subtitle_lang: str | None = None
    subtitle_burn: bool = False
    bgm_path: Path | None = None
    bgm_volume: float = 0.2
    bgm_loop: bool = False
    exclude_patterns: list[str] | None = None
    include_only_patterns: list[str] | None = None
    sort_key: str = "time"
    reorder: bool = False
    split_duration: str | None = None
    split_size: str | None = None
    archive_originals: Path | None = None
    archive_force: bool = False
    backup_remote: str | None = None
    backup_all: bool = False
    timelapse_speed: int | None = None
    timelapse_audio: bool = False
    timelapse_resolution: str | None = None
    stabilize: bool = False
    stabilize_strength: str = "medium"
    stabilize_crop: str = "crop"
    project: str | None = None
    lut_path: Path | None = None
    auto_lut: bool = False
    lut_before_hdr: bool = False
    device_luts: dict[str, str] | None = None
    template_intro: Path | None = None
    template_outro: Path | None = None
    watermark: bool = False
    watermark_text: str | None = None
    watermark_pos: str = "bottom-right"
    watermark_size: int = 48
    watermark_color: str = "white"
    watermark_alpha: float = 0.85
    watch: bool = False
    watch_paths: list[Path] = field(default_factory=list)
    watch_poll_interval: float = 1.0
    watch_stability_checks: int = 2
    watch_log: Path | None = None
    notify: bool = False
    schedule: str | None = None
    quality_report: bool = False
    hooks: HooksConfig = field(default_factory=HooksConfig)
    upload_privacy: str = "unlisted"
    playlist: list[str] | None = None


def _resolve_set_thumbnail_path(
    set_thumbnail_arg: str | Path | None,
) -> Path | None:
    """`--set-thumbnail` 입력값을 정규화하고 검증한다.

    - 경로 확장: `~` 전개 + `resolve()`
    - 존재 여부 검증
    - 포맷 검증 (`.jpg`, `.jpeg`, `.png`)

    Args:
        set_thumbnail_arg: CLI 입력값(`--set-thumbnail`)

    Returns:
        검증된 Path 또는 미지정 시 None
    """
    if not set_thumbnail_arg:
        return None

    set_thumbnail = Path(set_thumbnail_arg).expanduser().resolve()
    if not set_thumbnail.is_file():
        raise FileNotFoundError(f"Thumbnail file not found: {set_thumbnail_arg}")

    if set_thumbnail.suffix.lower() not in SUPPORTED_THUMBNAIL_EXTENSIONS:
        raise ValueError(
            f"Unsupported thumbnail format: {set_thumbnail.suffix} (supported: .jpg, .jpeg, .png)"
        )

    return set_thumbnail


def _resolve_template_path(template_arg: str | Path | None) -> Path | None:
    """`--template-*` 경로를 검증하고 Path로 변환한다.

    - `~` 확장
    - 존재 여부 검증

    Args:
        template_arg: 템플릿 파일 경로

    Returns:
        검증된 Path 또는 미지정 시 None
    """
    if not template_arg:
        return None

    template = Path(template_arg).expanduser().resolve()
    if not template.is_file():
        raise FileNotFoundError(f"Template file not found: {template_arg}")
    return template


def validate_args(
    args: argparse.Namespace,
    device_luts: dict[str, str] | None = None,
    hooks: HooksConfig | None = None,
) -> ValidatedArgs:
    """CLI 인자를 검증하고 :class:`ValidatedArgs` 로 변환한다.

    각 설정의 우선순위: **CLI 옵션 > 환경변수 > config.toml > 기본값**.
    ``get_default_*()`` 헬퍼가 환경변수·config.toml을 이미 반영하므로,
    여기서는 CLI 인자가 명시되었는지만 확인한다.

    Args:
        args: ``argparse`` 파싱 결과

    Returns:
        타입-안전하게 검증된 인자 데이터클래스

    Raises:
        FileNotFoundError: 대상 파일/디렉토리가 없을 때
        ValueError: fade_duration < 0 또는 thumbnail_quality 범위 초과
    """
    # targets 검증
    targets: list[Path] = []
    if not args.targets:
        targets = [Path.cwd()]
    else:
        for target in args.targets:
            path = Path(target).expanduser()
            if not path.exists():
                raise FileNotFoundError(f"Target not found: {target}")
            targets.append(path)

    # output 검증
    output: Path | None = None
    if args.output:
        output = Path(args.output).expanduser()
        if not output.parent.exists():
            raise FileNotFoundError(f"Output directory not found: {output.parent}")

    # output_dir 검증 (CLI 인자 > 환경 변수 > None)
    output_dir: Path | None = None
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
        if not output_dir.is_dir():
            raise FileNotFoundError(f"Output directory not found: {args.output_dir}")
    else:
        output_dir = get_default_output_dir()

    # watch 모드 경로 결정 (CLI > config)
    watch_arg = getattr(args, "watch", None)
    watch_paths_raw = watch_arg if watch_arg is not None else list(get_default_watch_paths())
    watch_mode = bool(watch_paths_raw)
    watch_paths: list[Path] = []
    for watch_path in watch_paths_raw:
        watch_target = Path(watch_path).expanduser()
        if not watch_target.is_dir():
            raise FileNotFoundError(f"Watch path not found or not a directory: {watch_path}")
        watch_paths.append(watch_target)

    watch_poll_interval = get_default_watch_poll_interval()
    watch_stability_checks = get_default_watch_stability_checks()
    watch_log_arg = getattr(args, "watch_log", None)
    watch_log: Path | None = (
        Path(watch_log_arg).expanduser() if watch_log_arg else get_default_watch_log_path()
    )

    # upload 플래그 확인
    upload = getattr(args, "upload", False)

    # parallel 값 결정 (CLI 인자 > 환경 변수 > 기본값)
    parallel = args.parallel if args.parallel is not None else get_default_parallel()
    if parallel < 1:
        parallel = 1

    # denoise 설정 (CLI 인자 > 환경 변수 > 기본값)
    denoise_flag = bool(getattr(args, "denoise", False))
    denoise_level = getattr(args, "denoise_level", None)
    env_denoise = get_default_denoise()
    env_denoise_level = get_default_denoise_level()
    if denoise_level is not None:
        denoise_flag = True
    resolved_denoise_level = denoise_level or env_denoise_level or "medium"
    if env_denoise_level is not None or env_denoise:
        denoise_flag = True

    # normalize_audio 설정 (CLI 인자 > 환경 변수 > 기본값)
    normalize_audio = bool(getattr(args, "normalize_audio", False)) or get_default_normalize_audio()

    # 그룹핑 설정 (CLI 인자 > 환경 변수 > 기본값)
    group_flag = bool(getattr(args, "group", False))
    no_group_flag = bool(getattr(args, "no_group", False))
    if group_flag:
        group_sequences = True
    elif no_group_flag:
        group_sequences = False
    else:
        group_sequences = get_default_group_sequences()

    # fade_duration 설정 (--no-fade > CLI 인자 > 환경 변수 > 기본값)
    no_fade = bool(getattr(args, "no_fade", False))
    if no_fade:
        fade_duration = 0.0
    else:
        fade_duration_arg = getattr(args, "fade_duration", None)
        fade_duration = (
            fade_duration_arg if fade_duration_arg is not None else get_default_fade_duration()
        )
    if fade_duration < 0:
        raise ValueError(f"Fade duration must be >= 0, got: {fade_duration}")

    # 썸네일 옵션 검증
    thumbnail = getattr(args, "thumbnail", False)
    thumbnail_at: list[str] | None = getattr(args, "thumbnail_at", None)
    thumbnail_quality: int = getattr(args, "thumbnail_quality", 2)
    set_thumbnail_arg = getattr(args, "set_thumbnail", None)
    set_thumbnail = _resolve_set_thumbnail_path(set_thumbnail_arg)

    # --thumbnail-at만 지정해도 암묵적 활성화
    if thumbnail_at and not thumbnail:
        thumbnail = True

    # quality 범위 검증
    if not 1 <= thumbnail_quality <= 31:
        raise ValueError(f"Thumbnail quality must be 1-31, got: {thumbnail_quality}")

    # 화질 리포트 옵션
    quality_report = bool(getattr(args, "quality_report", False))

    # 무음 관련 옵션
    detect_silence = getattr(args, "detect_silence", False)
    trim_silence = getattr(args, "trim_silence", False)
    silence_threshold = getattr(args, "silence_threshold", "-30dB")
    silence_min_duration = getattr(args, "silence_duration", 2.0)

    # silence_min_duration 범위 검증
    if silence_min_duration <= 0:
        raise ValueError(f"Silence duration must be > 0, got: {silence_min_duration}")

    # 자막 옵션
    subtitle = bool(getattr(args, "subtitle", False))
    subtitle_model = getattr(args, "subtitle_model", None)
    if isinstance(subtitle_model, SubtitleModel):
        subtitle_model = subtitle_model.value
    if subtitle_model is None:
        subtitle_model = get_default_subtitle_model() or "tiny"
    if subtitle_model not in SUPPORTED_SUBTITLE_MODELS:
        raise ValidationError(
            f"Invalid subtitle model: {subtitle_model!r} (supported: "
            f"{[m.value for m in SUPPORTED_SUBTITLE_MODELS]})"
        )

    subtitle_format = getattr(args, "subtitle_format", None)
    if isinstance(subtitle_format, SubtitleFormat):
        subtitle_format = subtitle_format.value
    if subtitle_format is None:
        subtitle_format = get_default_subtitle_format() or "srt"
    if subtitle_format not in SUPPORTED_SUBTITLE_FORMATS:
        raise ValidationError(
            f"Invalid subtitle format: {subtitle_format!r} (supported: "
            f"{[f.value for f in SUPPORTED_SUBTITLE_FORMATS]})"
        )

    subtitle_lang = getattr(args, "subtitle_lang", None)
    if subtitle_lang is None:
        subtitle_lang = get_default_subtitle_lang()
    if subtitle_lang is not None:
        subtitle_lang = subtitle_lang.strip().lower() or None

    subtitle_burn = bool(getattr(args, "subtitle_burn", False))
    if not subtitle_burn:
        subtitle_burn = get_default_subtitle_burn()

    # BGM 옵션 검증 (CLI 인자 > 환경 변수 > 기본값)
    bgm_path_arg = getattr(args, "bgm", None)
    bgm_path: Path | None = None
    if bgm_path_arg:
        bgm_path = Path(bgm_path_arg).expanduser()
        if not bgm_path.is_file():
            raise FileNotFoundError(f"BGM file not found: {bgm_path_arg}")
    else:
        # 환경변수/설정파일에서 기본값
        bgm_path = get_default_bgm_path()

    bgm_volume_arg = getattr(args, "bgm_volume", None)
    if bgm_volume_arg is not None:
        if not (0.0 <= bgm_volume_arg <= 1.0):
            raise ValueError(f"BGM volume must be in range [0.0, 1.0], got: {bgm_volume_arg}")
        bgm_volume = bgm_volume_arg
    else:
        # 환경변수/설정파일에서 기본값, 없으면 0.2
        env_bgm_volume = get_default_bgm_volume()
        bgm_volume = env_bgm_volume if env_bgm_volume is not None else 0.2

    bgm_loop_arg = getattr(args, "bgm_loop", False)
    bgm_loop = bgm_loop_arg or get_default_bgm_loop()

    # stabilize 설정 (CLI 인자 > 환경 변수 > 기본값)
    # --stabilize-strength 또는 --stabilize-crop 지정 시 암묵적으로 활성화
    stabilize_strength_arg: str | None = getattr(args, "stabilize_strength", None)
    stabilize_crop_arg: str | None = getattr(args, "stabilize_crop", None)
    env_stabilize = get_default_stabilize()
    env_stabilize_strength = get_default_stabilize_strength()
    env_stabilize_crop = get_default_stabilize_crop()
    stabilize_flag = (
        bool(getattr(args, "stabilize", False))
        or stabilize_strength_arg is not None
        or stabilize_crop_arg is not None
        or env_stabilize
    )
    resolved_stabilize_strength = stabilize_strength_arg or env_stabilize_strength or "medium"
    resolved_stabilize_crop = stabilize_crop_arg or env_stabilize_crop or "crop"

    exclude_patterns: list[str] | None = getattr(args, "exclude", None)
    include_only_patterns: list[str] | None = getattr(args, "include_only", None)
    sort_key_str: str = getattr(args, "sort", None) or "time"
    reorder_flag: bool = getattr(args, "reorder", False)

    # 영상 분할 옵션
    split_duration: str | None = getattr(args, "split_duration", None)
    split_size: str | None = getattr(args, "split_size", None)

    # 아카이브 옵션
    archive_originals_arg = getattr(args, "archive_originals", None)
    archive_originals: Path | None = None
    if archive_originals_arg:
        archive_originals = Path(archive_originals_arg).expanduser().resolve()
        # 디렉토리가 존재하지 않으면 생성 예정이므로 검증 생략

    archive_force_flag: bool = getattr(args, "archive_force", False)

    # 클라우드 백업 옵션 (CLI > 환경변수 > 기본값)
    backup_remote_arg = getattr(args, "backup", None)
    backup_remote: str | None = (
        backup_remote_arg.strip() if backup_remote_arg else get_default_backup_remote()
    )
    backup_all: bool = bool(getattr(args, "backup_all", False)) or (
        get_default_backup_include_originals()
    )

    # 타임랩스 옵션 검증
    timelapse_speed: int | None = None
    if hasattr(args, "timelapse") and args.timelapse:
        timelapse_str = args.timelapse.lower().rstrip("x")
        try:
            timelapse_speed = int(timelapse_str)
        except ValueError:
            raise ValueError(f"Invalid timelapse speed format: {args.timelapse}") from None

        from tubearchive.infra.ffmpeg.effects import TIMELAPSE_MAX_SPEED, TIMELAPSE_MIN_SPEED

        if timelapse_speed < TIMELAPSE_MIN_SPEED or timelapse_speed > TIMELAPSE_MAX_SPEED:
            raise ValueError(
                f"Timelapse speed must be between {TIMELAPSE_MIN_SPEED}x and "
                f"{TIMELAPSE_MAX_SPEED}x, got {timelapse_speed}x"
            )

    timelapse_audio: bool = getattr(args, "timelapse_audio", False)
    timelapse_resolution: str | None = getattr(args, "timelapse_resolution", None)

    # LUT 옵션 검증 (CLI 인자 > 환경변수 > config > 기본값)
    lut_path_arg = getattr(args, "lut", None)
    lut_path: Path | None = None
    if lut_path_arg:
        lut_path = Path(lut_path_arg).expanduser().resolve()
        if not lut_path.is_file():
            raise FileNotFoundError(f"LUT file not found: {lut_path_arg}")
        ext = lut_path.suffix.lower()
        if ext not in LUT_SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"Unsupported LUT format: {ext} "
                f"(supported: {', '.join(sorted(LUT_SUPPORTED_EXTENSIONS))})"
            )

    auto_lut_flag = getattr(args, "auto_lut", None)
    no_auto_lut_flag = getattr(args, "no_auto_lut", False)
    # --no-auto-lut이 --auto-lut보다 우선 (명시적 비활성화)
    if no_auto_lut_flag:
        if auto_lut_flag:
            logger.warning("--auto-lut and --no-auto-lut both set; --no-auto-lut wins")
        auto_lut = False
    elif auto_lut_flag:
        auto_lut = True
    else:
        auto_lut = get_default_auto_lut()

    lut_before_hdr: bool = getattr(args, "lut_before_hdr", False)
    # 템플릿 옵션 (CLI > env/config)
    template_intro_env = get_default_template_intro()
    template_outro_env = get_default_template_outro()
    template_intro_arg = getattr(args, "template_intro", None)
    template_outro_arg = getattr(args, "template_outro", None)
    template_intro = (
        _resolve_template_path(template_intro_arg)
        if template_intro_arg is not None
        else _resolve_template_path(template_intro_env)
    )
    template_outro = (
        _resolve_template_path(template_outro_arg)
        if template_outro_arg is not None
        else _resolve_template_path(template_outro_env)
    )

    # 워터마크 옵션
    watermark_enabled: bool = bool(getattr(args, "watermark", False))
    watermark_pos: str = getattr(args, "watermark_pos", "bottom-right")
    watermark_size: int = getattr(args, "watermark_size", 48)
    watermark_color: str = getattr(args, "watermark_color", "white")
    watermark_alpha: float = float(getattr(args, "watermark_alpha", 0.85))
    if watermark_size <= 0:
        raise ValueError(f"Watermark size must be > 0, got: {watermark_size}")
    if not (0.0 <= watermark_alpha <= 1.0):
        raise ValueError(f"Watermark alpha must be in [0.0, 1.0], got: {watermark_alpha}")

    # 스케줄 옵션 검증
    schedule_arg: str | None = getattr(args, "schedule", None)
    schedule: str | None = None
    if schedule_arg:
        schedule = parse_schedule_datetime(schedule_arg)
        logger.info(f"Parsed schedule time: {schedule}")

    hooks_config = hooks if hooks is not None else HooksConfig()

    return ValidatedArgs(
        targets=targets,
        output=output,
        output_dir=output_dir,
        no_resume=args.no_resume,
        keep_temp=args.keep_temp,
        dry_run=args.dry_run,
        denoise=denoise_flag,
        denoise_level=resolved_denoise_level,
        normalize_audio=normalize_audio,
        group_sequences=group_sequences,
        fade_duration=fade_duration,
        upload=upload,
        parallel=parallel,
        thumbnail=thumbnail,
        thumbnail_timestamps=thumbnail_at,
        thumbnail_quality=thumbnail_quality,
        set_thumbnail=set_thumbnail,
        generated_thumbnail_paths=None,
        detect_silence=detect_silence,
        trim_silence=trim_silence,
        silence_threshold=silence_threshold,
        silence_min_duration=silence_min_duration,
        subtitle=subtitle,
        subtitle_model=subtitle_model,
        subtitle_format=subtitle_format,
        subtitle_lang=subtitle_lang,
        subtitle_burn=subtitle_burn,
        bgm_path=bgm_path,
        bgm_volume=bgm_volume,
        bgm_loop=bgm_loop,
        exclude_patterns=exclude_patterns,
        include_only_patterns=include_only_patterns,
        sort_key=sort_key_str,
        reorder=reorder_flag,
        split_duration=split_duration,
        split_size=split_size,
        archive_originals=archive_originals,
        archive_force=archive_force_flag,
        backup_remote=backup_remote,
        backup_all=backup_all,
        timelapse_speed=timelapse_speed,
        timelapse_audio=timelapse_audio,
        timelapse_resolution=timelapse_resolution,
        stabilize=stabilize_flag,
        stabilize_strength=resolved_stabilize_strength,
        stabilize_crop=resolved_stabilize_crop,
        project=getattr(args, "project", None),
        lut_path=lut_path,
        auto_lut=auto_lut,
        lut_before_hdr=lut_before_hdr,
        watermark=watermark_enabled,
        watermark_pos=watermark_pos,
        watermark_size=watermark_size,
        watermark_color=watermark_color,
        watermark_alpha=watermark_alpha,
        device_luts=device_luts if device_luts else None,
        template_intro=template_intro,
        template_outro=template_outro,
        watch=watch_mode,
        watch_paths=watch_paths,
        watch_poll_interval=watch_poll_interval,
        watch_stability_checks=watch_stability_checks,
        watch_log=watch_log,
        quality_report=quality_report,
        notify=bool(getattr(args, "notify", False)) or get_default_notify(),
        schedule=schedule,
        hooks=hooks_config,
        upload_privacy=getattr(args, "upload_privacy", None) or "unlisted",
        playlist=getattr(args, "playlist", None),
    )
