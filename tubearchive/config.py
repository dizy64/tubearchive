"""TOML 설정 파일 및 환경변수 기본값 관리.

``~/.tubearchive/config.toml`` 에서 사용자 설정을 로드하고,
환경변수 Shim 패턴으로 기존 모듈의 ``os.environ.get()`` 코드를
변경하지 않고 설정값을 주입한다.

우선순위::

    CLI 옵션 > 환경변수 > config.toml > 기본값

또한 환경변수에서 개별 기본값을 읽어오는 헬퍼 함수
(``get_default_parallel``, ``get_default_denoise`` 등)도 이 모듈에서 제공한다.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# 환경변수 매핑
ENV_OUTPUT_DIR = "TUBEARCHIVE_OUTPUT_DIR"
ENV_PARALLEL = "TUBEARCHIVE_PARALLEL"
ENV_DB_PATH = "TUBEARCHIVE_DB_PATH"
ENV_YOUTUBE_CLIENT_SECRETS = "TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS"
ENV_YOUTUBE_TOKEN = "TUBEARCHIVE_YOUTUBE_TOKEN"
ENV_YOUTUBE_PLAYLIST = "TUBEARCHIVE_YOUTUBE_PLAYLIST"
ENV_UPLOAD_CHUNK_MB = "TUBEARCHIVE_UPLOAD_CHUNK_MB"
ENV_DENOISE = "TUBEARCHIVE_DENOISE"
ENV_DENOISE_LEVEL = "TUBEARCHIVE_DENOISE_LEVEL"
ENV_NORMALIZE_AUDIO = "TUBEARCHIVE_NORMALIZE_AUDIO"
ENV_GROUP_SEQUENCES = "TUBEARCHIVE_GROUP_SEQUENCES"
ENV_FADE_DURATION = "TUBEARCHIVE_FADE_DURATION"
ENV_BACKUP_REMOTE = "TUBEARCHIVE_BACKUP_REMOTE"
ENV_BACKUP_INCLUDE_ORIGINALS = "TUBEARCHIVE_BACKUP_INCLUDE_ORIGINALS"
ENV_TEMPLATE_INTRO = "TUBEARCHIVE_TEMPLATE_INTRO"
ENV_TEMPLATE_OUTRO = "TUBEARCHIVE_TEMPLATE_OUTRO"
ENV_TRIM_SILENCE = "TUBEARCHIVE_TRIM_SILENCE"
ENV_SILENCE_THRESHOLD = "TUBEARCHIVE_SILENCE_THRESHOLD"
ENV_SILENCE_MIN_DURATION = "TUBEARCHIVE_SILENCE_MIN_DURATION"
ENV_BGM_PATH = "TUBEARCHIVE_BGM_PATH"
ENV_BGM_VOLUME = "TUBEARCHIVE_BGM_VOLUME"
ENV_BGM_LOOP = "TUBEARCHIVE_BGM_LOOP"
ENV_ARCHIVE_POLICY = "TUBEARCHIVE_ARCHIVE_POLICY"
ENV_ARCHIVE_DESTINATION = "TUBEARCHIVE_ARCHIVE_DESTINATION"
ENV_STABILIZE = "TUBEARCHIVE_STABILIZE"
ENV_STABILIZE_STRENGTH = "TUBEARCHIVE_STABILIZE_STRENGTH"
ENV_STABILIZE_CROP = "TUBEARCHIVE_STABILIZE_CROP"
ENV_AUTO_LUT = "TUBEARCHIVE_AUTO_LUT"
ENV_WATCH_PATHS = "TUBEARCHIVE_WATCH_PATHS"
ENV_WATCH_POLL_INTERVAL = "TUBEARCHIVE_WATCH_POLL_INTERVAL"
ENV_WATCH_STABILITY_CHECKS = "TUBEARCHIVE_WATCH_STABILITY_CHECKS"
ENV_WATCH_LOG = "TUBEARCHIVE_WATCH_LOG_PATH"
ENV_TEMPLATE_INTRO = "TUBEARCHIVE_TEMPLATE_INTRO"
ENV_TEMPLATE_OUTRO = "TUBEARCHIVE_TEMPLATE_OUTRO"
ENV_SUBTITLE_LANG = "TUBEARCHIVE_SUBTITLE_LANG"
ENV_SUBTITLE_MODEL = "TUBEARCHIVE_SUBTITLE_MODEL"
ENV_SUBTITLE_FORMAT = "TUBEARCHIVE_SUBTITLE_FORMAT"
ENV_SUBTITLE_BURN = "TUBEARCHIVE_SUBTITLE_BURN"

# 알림 설정
ENV_NOTIFY = "TUBEARCHIVE_NOTIFY"
ENV_NOTIFY_MACOS = "TUBEARCHIVE_NOTIFY_MACOS"
ENV_NOTIFY_MACOS_SOUND = "TUBEARCHIVE_NOTIFY_MACOS_SOUND"
ENV_NOTIFY_TELEGRAM = "TUBEARCHIVE_NOTIFY_TELEGRAM"
ENV_TELEGRAM_BOT_TOKEN = "TUBEARCHIVE_TELEGRAM_BOT_TOKEN"
ENV_TELEGRAM_CHAT_ID = "TUBEARCHIVE_TELEGRAM_CHAT_ID"
ENV_NOTIFY_DISCORD = "TUBEARCHIVE_NOTIFY_DISCORD"
ENV_DISCORD_WEBHOOK_URL = "TUBEARCHIVE_DISCORD_WEBHOOK_URL"
ENV_NOTIFY_SLACK = "TUBEARCHIVE_NOTIFY_SLACK"
ENV_SLACK_WEBHOOK_URL = "TUBEARCHIVE_SLACK_WEBHOOK_URL"


@dataclass(frozen=True)
class GeneralConfig:
    """``config.toml`` 의 ``[general]`` 섹션.

    모든 필드가 ``None`` 이면 해당 옵션은 환경변수 또는 기본값을 사용한다.
    """

    output_dir: str | None = None
    parallel: int | None = None
    db_path: str | None = None
    denoise: bool | None = None
    denoise_level: str | None = None
    normalize_audio: bool | None = None
    group_sequences: bool | None = None
    fade_duration: float | None = None
    trim_silence: bool | None = None
    silence_threshold: str | None = None
    silence_min_duration: float | None = None
    stabilize: bool | None = None
    stabilize_strength: str | None = None
    stabilize_crop: str | None = None
    subtitle_lang: str | None = None
    subtitle_model: str | None = None
    subtitle_format: str | None = None
    subtitle_burn: bool | None = None


@dataclass(frozen=True)
class BGMConfig:
    """``config.toml`` 의 ``[bgm]`` 섹션.

    배경음악 믹싱 설정을 관리한다.
    """

    bgm_path: str | None = None
    """배경 음악 파일 경로."""
    bgm_volume: float | None = None
    """배경 음악 상대 볼륨 (0.0~1.0)."""
    bgm_loop: bool | None = None
    """BGM 루프 재생 여부."""


@dataclass(frozen=True)
class YouTubeConfig:
    """``config.toml`` 의 ``[youtube]`` 섹션.

    OAuth 인증 경로, 플레이리스트, 업로드 옵션을 관리한다.
    """

    client_secrets: str | None = None
    token: str | None = None
    playlist: list[str] = field(default_factory=list)
    upload_chunk_mb: int | None = None
    upload_privacy: str | None = None


@dataclass(frozen=True)
class TemplateConfig:
    """``[template]`` 섹션 데이터.

    인트로/아웃트로 템플릿 파일 경로를 보관한다.
    ``None`` 이면 해당 템플릿 미적용이다.
    """

    intro: str | None = None
    """인트로 영상 경로 (문자열)."""
    outro: str | None = None
    """아웃트로 영상 경로 (문자열)."""


@dataclass(frozen=True)
class ArchiveConfig:
    """``config.toml`` 의 ``[archive]`` 섹션.

    트랜스코딩 완료 후 원본 파일 관리 정책을 정의한다.
    """

    policy: str | None = None  # keep/move/delete
    destination: str | None = None  # move 시 이동 경로


@dataclass(frozen=True)
class BackupConfig:
    """``[backup]`` 섹션 데이터.

    처리 완료 후 백업 대상과 범위를 관리한다.
    """

    remote: str | None = None
    include_originals: bool = False


@dataclass(frozen=True)
class ColorGradingConfig:
    """``config.toml`` 의 ``[color_grading]`` 섹션.

    LUT(Look-Up Table) 기반 컬러 그레이딩 설정을 관리한다.

    ``auto_lut``만 환경변수(``TUBEARCHIVE_AUTO_LUT``)로 오버라이드 가능.
    ``device_luts``는 TOML 중첩 테이블 구조이므로 환경변수 매핑이 없으며,
    config.toml에서만 설정한다.
    """

    auto_lut: bool | None = None
    """기기 모델명 기반 자동 LUT 적용 여부."""
    device_luts: dict[str, str] = field(default_factory=dict)
    """기기 키워드 → LUT 파일 경로 매핑 (config.toml 전용, env var 없음)."""


@dataclass(frozen=True)
class WatchConfig:
    """``config.toml`` 의 ``[watch]`` 섹션."""

    paths: tuple[str, ...] = ()
    """워치 대상 디렉토리 목록."""
    poll_interval: float | None = None
    """폴링 대기 시간(초)."""
    stability_checks: int | None = None
    """파일 안정성 검사 반복 횟수."""
    log_path: str | None = None
    """watch 데몬 로그 파일 경로."""


@dataclass(frozen=True)
class MacOSNotifyConfig:
    """``[notification.macos]`` 하위 설정."""

    enabled: bool | None = None  # None이면 기본 True
    sound: bool | None = None  # None이면 기본 True


@dataclass(frozen=True)
class TelegramConfig:
    """``[notification.telegram]`` 하위 설정."""

    enabled: bool | None = None
    bot_token: str | None = None
    chat_id: str | None = None


@dataclass(frozen=True)
class DiscordConfig:
    """``[notification.discord]`` 하위 설정."""

    enabled: bool | None = None
    webhook_url: str | None = None


@dataclass(frozen=True)
class SlackConfig:
    """``[notification.slack]`` 하위 설정."""

    enabled: bool | None = None
    webhook_url: str | None = None


@dataclass(frozen=True)
class HooksConfig:
    """``[hooks]`` 섹션 파서 결과.

    이벤트별 실행할 훅 명령어를 보관한다.
    """

    on_transcode: tuple[str, ...] = field(default_factory=tuple)
    on_merge: tuple[str, ...] = field(default_factory=tuple)
    on_upload: tuple[str, ...] = field(default_factory=tuple)
    on_error: tuple[str, ...] = field(default_factory=tuple)
    timeout_sec: int = 60


@dataclass(frozen=True)
class NotificationConfig:
    """``config.toml`` 의 ``[notification]`` 섹션.

    macOS 알림센터 및 외부 웹훅(Telegram, Discord, Slack) 설정을 관리한다.
    """

    enabled: bool | None = None
    on_transcode_complete: bool | None = None
    on_merge_complete: bool | None = None
    on_upload_complete: bool | None = None
    on_error: bool | None = None
    macos: MacOSNotifyConfig = field(default_factory=MacOSNotifyConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    discord: DiscordConfig = field(default_factory=DiscordConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)


@dataclass(frozen=True)
class AppConfig:
    """애플리케이션 전체 설정.

    [general] + [bgm] + [youtube] + [archive] + [color_grading]
    + [template] + [hooks] + [notification] 통합.
    """

    general: GeneralConfig = field(default_factory=GeneralConfig)
    bgm: BGMConfig = field(default_factory=BGMConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    color_grading: ColorGradingConfig = field(default_factory=ColorGradingConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    template: TemplateConfig = field(default_factory=TemplateConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    notification: NotificationConfig = field(default_factory=NotificationConfig)


def _parse_hook_commands(data: dict[str, object], key: str, section: str) -> tuple[str, ...]:
    """훅 명령 목록을 안전하게 파싱한다.

    지원 형식:
    - string: 단일 훅 명령 1개
    - array<string>: 다중 훅 명령 목록
    """
    raw = data.get(key)
    if raw is None:
        return ()

    if isinstance(raw, str):
        return (raw,)

    if not isinstance(raw, list):
        _warn_type(f"{section}.{key}", "str|list[str]", raw)
        return ()

    commands: list[str] = []
    skipped = 0
    for item in raw:
        if isinstance(item, str):
            commands.append(item)
        else:
            skipped += 1
    if skipped > 0:
        logger.warning(
            "config: %s.%s 에 비문자열 항목 %d개 무시됨",
            section,
            key,
            skipped,
        )
    return tuple(commands)


def _parse_hook_timeout(data: dict[str, object]) -> int:
    """훅 타임아웃을 파싱한다."""
    raw_timeout = data.get("timeout_sec")
    if raw_timeout is None:
        return 60
    if isinstance(raw_timeout, int) and not isinstance(raw_timeout, bool):
        if raw_timeout > 0:
            return raw_timeout
        logger.warning(
            "config: hooks.timeout_sec 값 오류: %r (양수여야 함)",
            raw_timeout,
        )
        return 60
    _warn_type("hooks.timeout_sec", "int", raw_timeout)
    return 60


def _warn_type(field_name: str, expected: str, value: object) -> None:
    """타입 불일치 경고 출력."""
    logger.warning(
        "config: %s 타입 오류 (expected %s, got %s)",
        field_name,
        expected,
        type(value).__name__,
    )


def _parse_str(data: dict[str, object], key: str, section: str) -> str | None:
    """TOML dict에서 문자열 필드를 안전하게 파싱한다."""
    raw = data.get(key)
    if isinstance(raw, str):
        return raw
    if raw is not None:
        _warn_type(f"{section}.{key}", "str", raw)
    return None


def _parse_bool(data: dict[str, object], key: str, section: str) -> bool | None:
    """TOML dict에서 bool 필드를 안전하게 파싱한다."""
    raw = data.get(key)
    if isinstance(raw, bool):
        return raw
    if raw is not None:
        _warn_type(f"{section}.{key}", "bool", raw)
    return None


def _parse_int(data: dict[str, object], key: str, section: str) -> int | None:
    """TOML dict에서 정수 필드를 안전하게 파싱한다 (bool 제외)."""
    raw = data.get(key)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if raw is not None:
        _warn_type(f"{section}.{key}", "int", raw)
    return None


def _parse_enum_str(
    data: dict[str, object],
    key: str,
    section: str,
    allowed: tuple[str, ...],
) -> str | None:
    """허용된 문자열 값만 통과시키는 파서. 유효하지 않으면 경고 후 None."""
    value = _parse_str(data, key, section)
    if value is not None and value not in allowed:
        logger.warning("config: %s.%s 값 오류: %r", section, key, value)
        return None
    return value


def get_default_config_path() -> Path:
    """기본 설정 파일 경로 반환."""
    return Path.home() / ".tubearchive" / "config.toml"


def _parse_general(data: dict[str, object]) -> GeneralConfig:
    """[general] 섹션 파싱. 타입 오류 시 해당 필드 무시."""
    section = "general"

    # denoise_level: 허용값 검증이 필요한 문자열
    denoise_level = _parse_enum_str(data, "denoise_level", section, ("light", "medium", "heavy"))

    # fade_duration: 음수가 아닌 실수 (int도 허용)
    fade_duration: float | None = None
    raw_fade = data.get("fade_duration")
    if isinstance(raw_fade, (int, float)) and not isinstance(raw_fade, bool):
        if raw_fade >= 0:
            fade_duration = float(raw_fade)
        else:
            logger.warning("config: general.fade_duration 값 오류: %r", raw_fade)
    elif raw_fade is not None:
        _warn_type(f"{section}.fade_duration", "float", raw_fade)

    # silence_min_duration: 양수 실수 (int도 허용)
    silence_min_duration: float | None = None
    raw_silence_dur = data.get("silence_min_duration")
    if isinstance(raw_silence_dur, (int, float)) and not isinstance(raw_silence_dur, bool):
        if raw_silence_dur > 0:
            silence_min_duration = float(raw_silence_dur)
        else:
            logger.warning("config: general.silence_min_duration 값 오류: %r", raw_silence_dur)
    elif raw_silence_dur is not None:
        _warn_type(f"{section}.silence_min_duration", "float", raw_silence_dur)

    # stabilize_strength / stabilize_crop: 허용값 검증
    stabilize_strength = _parse_enum_str(
        data,
        "stabilize_strength",
        section,
        ("light", "medium", "heavy"),
    )
    stabilize_crop = _parse_enum_str(
        data,
        "stabilize_crop",
        section,
        ("crop", "expand"),
    )
    subtitle_lang = _parse_str(data, "subtitle_lang", section)
    if subtitle_lang is not None:
        subtitle_lang = subtitle_lang.strip().lower() or None
    subtitle_model = _parse_enum_str(
        data,
        "subtitle_model",
        section,
        ("tiny", "base", "small", "medium", "large"),
    )
    subtitle_format = _parse_enum_str(
        data,
        "subtitle_format",
        section,
        ("srt", "vtt"),
    )

    return GeneralConfig(
        output_dir=_parse_str(data, "output_dir", section),
        parallel=_parse_int(data, "parallel", section),
        db_path=_parse_str(data, "db_path", section),
        denoise=_parse_bool(data, "denoise", section),
        denoise_level=denoise_level,
        normalize_audio=_parse_bool(data, "normalize_audio", section),
        group_sequences=_parse_bool(data, "group_sequences", section),
        fade_duration=fade_duration,
        trim_silence=_parse_bool(data, "trim_silence", section),
        silence_threshold=_parse_str(data, "silence_threshold", section),
        silence_min_duration=silence_min_duration,
        stabilize=_parse_bool(data, "stabilize", section),
        stabilize_strength=stabilize_strength,
        stabilize_crop=stabilize_crop,
        subtitle_lang=subtitle_lang,
        subtitle_model=subtitle_model,
        subtitle_format=subtitle_format,
        subtitle_burn=_parse_bool(data, "subtitle_burn", section),
    )


def _parse_bgm(data: dict[str, object]) -> BGMConfig:
    """[bgm] 섹션 파싱. 타입 오류 시 해당 필드 무시."""
    section = "bgm"

    # bgm_volume: 범위 검증 (0.0~1.0)
    bgm_volume: float | None = None
    raw_volume = data.get("bgm_volume")
    if isinstance(raw_volume, (int, float)) and not isinstance(raw_volume, bool):
        if 0.0 <= raw_volume <= 1.0:
            bgm_volume = float(raw_volume)
        else:
            logger.warning("config: bgm.bgm_volume 범위 초과: %r (0.0~1.0)", raw_volume)
    elif raw_volume is not None:
        _warn_type(f"{section}.bgm_volume", "float", raw_volume)

    return BGMConfig(
        bgm_path=_parse_str(data, "bgm_path", section),
        bgm_volume=bgm_volume,
        bgm_loop=_parse_bool(data, "bgm_loop", section),
    )


def _parse_youtube(data: dict[str, object]) -> YouTubeConfig:
    """[youtube] 섹션 파싱. 타입 오류 시 해당 필드 무시."""
    section = "youtube"

    # playlist: list[str] 또는 단일 str 허용
    playlist: list[str] = []
    raw_playlist = data.get("playlist")
    if isinstance(raw_playlist, list):
        playlist = [item for item in raw_playlist if isinstance(item, str)]
        skipped = len(raw_playlist) - len(playlist)
        if skipped > 0:
            logger.warning("config: youtube.playlist에 비문자열 항목 %d개 무시됨", skipped)
    elif isinstance(raw_playlist, str):
        playlist = [raw_playlist]
    elif raw_playlist is not None:
        _warn_type(f"{section}.playlist", "list|str", raw_playlist)

    # upload_chunk_mb: 범위 검증
    upload_chunk_mb = _parse_int(data, "upload_chunk_mb", section)
    if upload_chunk_mb is not None and not (1 <= upload_chunk_mb <= 256):
        logger.warning("config: youtube.upload_chunk_mb 범위 초과: %d (1-256)", upload_chunk_mb)
        upload_chunk_mb = None

    # upload_privacy: 허용값 검증
    upload_privacy = _parse_str(data, "upload_privacy", section)
    if upload_privacy is not None and upload_privacy not in ("public", "unlisted", "private"):
        logger.warning("config: youtube.upload_privacy 값 오류: %r", upload_privacy)
        upload_privacy = None

    return YouTubeConfig(
        client_secrets=_parse_str(data, "client_secrets", section),
        token=_parse_str(data, "token", section),
        playlist=playlist,
        upload_chunk_mb=upload_chunk_mb,
        upload_privacy=upload_privacy,
    )


def _parse_archive(data: dict[str, object]) -> ArchiveConfig:
    """[archive] 섹션 파싱. 타입 오류 시 해당 필드 무시."""
    section = "archive"

    # policy: 허용값 검증
    policy = _parse_str(data, "policy", section)
    if policy is not None and policy not in ("keep", "move", "delete"):
        logger.warning("config: archive.policy 값 오류: %r", policy)
        policy = None

    # destination: 문자열 (move 정책 시 필수)
    destination = _parse_str(data, "destination", section)

    return ArchiveConfig(
        policy=policy,
        destination=destination,
    )


def _parse_backup(data: dict[str, object]) -> BackupConfig:
    """``[backup]`` 섹션 파싱. 타입 오류 시 해당 필드만 무시."""
    section = "backup"

    remote = _parse_str(data, "remote", section)
    include_originals = _parse_bool(data, "include_originals", section)

    return BackupConfig(
        remote=remote,
        include_originals=include_originals if include_originals is not None else False,
    )


def _parse_color_grading(data: dict[str, object]) -> ColorGradingConfig:
    """[color_grading] 섹션 파싱. 타입 오류 시 해당 필드 무시."""
    section = "color_grading"

    auto_lut = _parse_bool(data, "auto_lut", section)

    # device_luts: 중첩 테이블 {키워드: LUT 경로}
    device_luts: dict[str, str] = {}
    raw_device_luts = data.get("device_luts")
    if isinstance(raw_device_luts, dict):
        for key, value in raw_device_luts.items():
            if isinstance(value, str):
                device_luts[key] = value
            else:
                _warn_type(f"{section}.device_luts.{key}", "str", value)
    elif raw_device_luts is not None:
        _warn_type(f"{section}.device_luts", "table", raw_device_luts)

    return ColorGradingConfig(
        auto_lut=auto_lut,
        device_luts=device_luts,
    )


def _parse_watch(data: dict[str, object]) -> WatchConfig:
    """``[watch]`` 섹션 파싱. 타입 오류 시 해당 필드만 기본값 유지."""
    section = "watch"

    raw_paths = data.get("paths")
    paths: list[str] = []
    if isinstance(raw_paths, list):
        for raw_path in raw_paths:
            if isinstance(raw_path, str):
                paths.append(raw_path.strip())
            else:
                _warn_type(f"{section}.paths", "list[str]", raw_path)
    elif raw_paths is not None:
        _warn_type(f"{section}.paths", "list[str]", raw_paths)

    raw_poll_interval = data.get("poll_interval")
    poll_interval = None
    if raw_poll_interval is not None:
        if isinstance(raw_poll_interval, (int, float)):
            if raw_poll_interval > 0:
                poll_interval = float(raw_poll_interval)
            else:
                logger.warning("%s.%s must be > 0, using None", section, "poll_interval")
        else:
            _warn_type(f"{section}.poll_interval", "float", raw_poll_interval)

    raw_stability_checks = data.get("stability_checks")
    stability_checks = None
    if raw_stability_checks is not None:
        if isinstance(raw_stability_checks, int):
            if raw_stability_checks > 0:
                stability_checks = raw_stability_checks
            else:
                logger.warning("%s.%s must be > 0, using None", section, "stability_checks")
        else:
            _warn_type(f"{section}.stability_checks", "int", raw_stability_checks)

    log_path = _parse_str(data, "log_path", section)

    return WatchConfig(
        paths=tuple(p for p in paths if p),
        poll_interval=poll_interval,
        stability_checks=stability_checks,
        log_path=log_path,
    )


def _parse_template(data: dict[str, object]) -> TemplateConfig:
    """``[template]`` 섹션 파싱."""
    section = "template"
    return TemplateConfig(
        intro=_parse_str(data, "intro", section),
        outro=_parse_str(data, "outro", section),
    )


def _parse_notification(data: dict[str, object]) -> NotificationConfig:
    """[notification] 섹션 파싱. 타입 오류 시 해당 필드 무시."""
    section = "notification"

    enabled = _parse_bool(data, "enabled", section)
    on_transcode_complete = _parse_bool(data, "on_transcode_complete", section)
    on_merge_complete = _parse_bool(data, "on_merge_complete", section)
    on_upload_complete = _parse_bool(data, "on_upload_complete", section)
    on_error = _parse_bool(data, "on_error", section)

    # macos 하위 테이블
    raw_macos = data.get("macos", {})
    if isinstance(raw_macos, dict):
        macos = MacOSNotifyConfig(
            enabled=_parse_bool(raw_macos, "enabled", f"{section}.macos"),
            sound=_parse_bool(raw_macos, "sound", f"{section}.macos"),
        )
    else:
        macos = MacOSNotifyConfig()

    # telegram 하위 테이블
    raw_telegram = data.get("telegram", {})
    if isinstance(raw_telegram, dict):
        telegram = TelegramConfig(
            enabled=_parse_bool(raw_telegram, "enabled", f"{section}.telegram"),
            bot_token=_parse_str(raw_telegram, "bot_token", f"{section}.telegram"),
            chat_id=_parse_str(raw_telegram, "chat_id", f"{section}.telegram"),
        )
    else:
        telegram = TelegramConfig()

    # discord 하위 테이블
    raw_discord = data.get("discord", {})
    if isinstance(raw_discord, dict):
        discord = DiscordConfig(
            enabled=_parse_bool(raw_discord, "enabled", f"{section}.discord"),
            webhook_url=_parse_str(raw_discord, "webhook_url", f"{section}.discord"),
        )
    else:
        discord = DiscordConfig()

    # slack 하위 테이블
    raw_slack = data.get("slack", {})
    if isinstance(raw_slack, dict):
        slack = SlackConfig(
            enabled=_parse_bool(raw_slack, "enabled", f"{section}.slack"),
            webhook_url=_parse_str(raw_slack, "webhook_url", f"{section}.slack"),
        )
    else:
        slack = SlackConfig()

    return NotificationConfig(
        enabled=enabled,
        on_transcode_complete=on_transcode_complete,
        on_merge_complete=on_merge_complete,
        on_upload_complete=on_upload_complete,
        on_error=on_error,
        macos=macos,
        telegram=telegram,
        discord=discord,
        slack=slack,
    )


def _parse_hooks(data: dict[str, object]) -> HooksConfig:
    """``[hooks]`` 섹션 파싱. 타입 오류 시 해당 필드 무시."""
    section = "hooks"

    timeout_sec = _parse_hook_timeout(data)

    return HooksConfig(
        on_transcode=_parse_hook_commands(data, "on_transcode", section),
        on_merge=_parse_hook_commands(data, "on_merge", section),
        on_upload=_parse_hook_commands(data, "on_upload", section),
        on_error=_parse_hook_commands(data, "on_error", section),
        timeout_sec=timeout_sec,
    )


def load_config(path: Path | None = None) -> AppConfig:
    """
    TOML 설정 파일 로드.

    Args:
        path: 설정 파일 경로 (None이면 기본 경로 사용)

    Returns:
        AppConfig (파일 없음/에러 시 빈 AppConfig)
    """
    config_path = path or get_default_config_path()

    if not config_path.is_file():
        return AppConfig()

    try:
        with config_path.open("rb") as f:
            raw = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        logger.warning(f"config: TOML 문법 오류 ({config_path}): {e}")
        return AppConfig()
    except OSError as e:
        logger.warning(f"config: 파일 읽기 실패 ({config_path}): {e}")
        return AppConfig()

    general_data = raw.get("general", {})
    bgm_data = raw.get("bgm", {})
    youtube_data = raw.get("youtube", {})
    archive_data = raw.get("archive", {})
    color_grading_data = raw.get("color_grading", {})
    template_data = raw.get("template", {})
    notification_data = raw.get("notification", {})
    hooks_data = raw.get("hooks", {})
    watch_data = raw.get("watch", {})

    if isinstance(general_data, dict):
        general = _parse_general(general_data)
    else:
        if general_data:
            logger.warning(
                "config: [general] 섹션이 테이블이 아닙니다 (got %s)",
                type(general_data).__name__,
            )
        general = GeneralConfig()

    if isinstance(bgm_data, dict):
        bgm = _parse_bgm(bgm_data)
    else:
        if bgm_data:
            logger.warning(
                "config: [bgm] 섹션이 테이블이 아닙니다 (got %s)",
                type(bgm_data).__name__,
            )
        bgm = BGMConfig()

    if isinstance(youtube_data, dict):
        youtube = _parse_youtube(youtube_data)
    else:
        if youtube_data:
            logger.warning(
                "config: [youtube] 섹션이 테이블이 아닙니다 (got %s)",
                type(youtube_data).__name__,
            )
        youtube = YouTubeConfig()

    if isinstance(archive_data, dict):
        archive = _parse_archive(archive_data)
    else:
        if archive_data:
            logger.warning(
                "config: [archive] 섹션이 테이블이 아닙니다 (got %s)",
                type(archive_data).__name__,
            )
        archive = ArchiveConfig()

    backup_data = raw.get("backup", {})
    if isinstance(backup_data, dict):
        backup = _parse_backup(backup_data)
    else:
        if backup_data:
            logger.warning(
                "config: [backup] 섹션이 테이블이 아닙니다 (got %s)",
                type(backup_data).__name__,
            )
        backup = BackupConfig()

    if isinstance(color_grading_data, dict):
        color_grading = _parse_color_grading(color_grading_data)
    else:
        if color_grading_data:
            logger.warning(
                "config: [color_grading] 섹션이 테이블이 아닙니다 (got %s)",
                type(color_grading_data).__name__,
            )
        color_grading = ColorGradingConfig()

    if isinstance(template_data, dict):
        template = _parse_template(template_data)
    else:
        if template_data:
            logger.warning(
                "config: [template] 섹션이 테이블이 아닙니다 (got %s)",
                type(template_data).__name__,
            )
        template = TemplateConfig()

    if isinstance(notification_data, dict):
        notification = _parse_notification(notification_data)
    else:
        if notification_data:
            logger.warning(
                "config: [notification] 섹션이 테이블이 아닙니다 (got %s)",
                type(notification_data).__name__,
            )
        notification = NotificationConfig()

    if isinstance(hooks_data, dict):
        hooks = _parse_hooks(hooks_data)
    else:
        if hooks_data:
            logger.warning(
                "config: [hooks] 섹션이 테이블이 아닙니다 (got %s)",
                type(hooks_data).__name__,
            )
        hooks = HooksConfig()

    if isinstance(watch_data, dict):
        watch = _parse_watch(watch_data)
    else:
        if watch_data:
            logger.warning(
                "config: [watch] 섹션이 테이블이 아닙니다 (got %s)",
                type(watch_data).__name__,
            )
        watch = WatchConfig()

    return AppConfig(
        general=general,
        bgm=bgm,
        youtube=youtube,
        archive=archive,
        backup=backup,
        color_grading=color_grading,
        watch=watch,
        template=template,
        hooks=hooks,
        notification=notification,
    )


def apply_config_to_env(config: AppConfig, *, overwrite: bool = False) -> None:
    """
    설정값을 환경변수에 주입.

    기본 동작은 기존 환경변수를 보존한다.
    reload 모드에서 최신 config 값을 반영하려면 overwrite=True로 호출한다.
    """
    mappings: list[tuple[str, str | None]] = [
        (ENV_OUTPUT_DIR, config.general.output_dir),
        (ENV_DB_PATH, config.general.db_path),
        (ENV_YOUTUBE_CLIENT_SECRETS, config.youtube.client_secrets),
        (ENV_YOUTUBE_TOKEN, config.youtube.token),
    ]

    # int 필드
    if config.general.parallel is not None:
        mappings.append((ENV_PARALLEL, str(config.general.parallel)))
    if config.youtube.upload_chunk_mb is not None:
        mappings.append((ENV_UPLOAD_CHUNK_MB, str(config.youtube.upload_chunk_mb)))

    # bool → "true"/"false"
    if config.general.denoise is not None:
        mappings.append((ENV_DENOISE, str(config.general.denoise).lower()))
    if config.general.denoise_level is not None:
        mappings.append((ENV_DENOISE_LEVEL, config.general.denoise_level))
    if config.general.normalize_audio is not None:
        mappings.append((ENV_NORMALIZE_AUDIO, str(config.general.normalize_audio).lower()))
    if config.general.group_sequences is not None:
        mappings.append((ENV_GROUP_SEQUENCES, str(config.general.group_sequences).lower()))
    if config.general.fade_duration is not None:
        mappings.append((ENV_FADE_DURATION, str(config.general.fade_duration)))
    if config.general.trim_silence is not None:
        mappings.append((ENV_TRIM_SILENCE, str(config.general.trim_silence).lower()))
    if config.general.silence_threshold is not None:
        mappings.append((ENV_SILENCE_THRESHOLD, config.general.silence_threshold))
    if config.general.silence_min_duration is not None:
        mappings.append((ENV_SILENCE_MIN_DURATION, str(config.general.silence_min_duration)))

    # BGM 설정
    if config.bgm.bgm_path is not None:
        mappings.append((ENV_BGM_PATH, config.bgm.bgm_path))
    if config.bgm.bgm_volume is not None:
        mappings.append((ENV_BGM_VOLUME, str(config.bgm.bgm_volume)))
    if config.bgm.bgm_loop is not None:
        mappings.append((ENV_BGM_LOOP, str(config.bgm.bgm_loop).lower()))

    # playlist: list → CSV
    if config.youtube.playlist:
        mappings.append((ENV_YOUTUBE_PLAYLIST, ",".join(config.youtube.playlist)))

    # stabilize
    if config.general.stabilize is not None:
        mappings.append((ENV_STABILIZE, str(config.general.stabilize).lower()))
    if config.general.stabilize_strength is not None:
        mappings.append((ENV_STABILIZE_STRENGTH, config.general.stabilize_strength))
    if config.general.stabilize_crop is not None:
        mappings.append((ENV_STABILIZE_CROP, config.general.stabilize_crop))
    if config.general.subtitle_lang is not None:
        mappings.append((ENV_SUBTITLE_LANG, config.general.subtitle_lang))
    if config.general.subtitle_model is not None:
        mappings.append((ENV_SUBTITLE_MODEL, config.general.subtitle_model))
    if config.general.subtitle_format is not None:
        mappings.append((ENV_SUBTITLE_FORMAT, config.general.subtitle_format))
    if config.general.subtitle_burn is not None:
        mappings.append((ENV_SUBTITLE_BURN, str(config.general.subtitle_burn).lower()))

    # watch
    if config.watch.paths:
        mappings.append((ENV_WATCH_PATHS, ",".join(config.watch.paths)))
    if config.watch.poll_interval is not None:
        mappings.append((ENV_WATCH_POLL_INTERVAL, str(config.watch.poll_interval)))
    if config.watch.stability_checks is not None:
        mappings.append((ENV_WATCH_STABILITY_CHECKS, str(config.watch.stability_checks)))
    if config.watch.log_path is not None:
        mappings.append((ENV_WATCH_LOG, config.watch.log_path))

    # archive policy
    if config.archive.policy is not None:
        mappings.append((ENV_ARCHIVE_POLICY, config.archive.policy))
    if config.archive.destination is not None:
        mappings.append((ENV_ARCHIVE_DESTINATION, config.archive.destination))

    if config.backup.remote is not None:
        mappings.append((ENV_BACKUP_REMOTE, config.backup.remote))
    mappings.append((ENV_BACKUP_INCLUDE_ORIGINALS, str(config.backup.include_originals).lower()))

    # color grading
    if config.color_grading.auto_lut is not None:
        mappings.append((ENV_AUTO_LUT, str(config.color_grading.auto_lut).lower()))

    # template
    if config.template.intro is not None:
        mappings.append((ENV_TEMPLATE_INTRO, config.template.intro))
    if config.template.outro is not None:
        mappings.append((ENV_TEMPLATE_OUTRO, config.template.outro))

    # notification
    notif = config.notification
    if notif.enabled is not None:
        mappings.append((ENV_NOTIFY, str(notif.enabled).lower()))
    if notif.macos.enabled is not None:
        mappings.append((ENV_NOTIFY_MACOS, str(notif.macos.enabled).lower()))
    if notif.macos.sound is not None:
        mappings.append((ENV_NOTIFY_MACOS_SOUND, str(notif.macos.sound).lower()))
    if notif.telegram.enabled is not None:
        mappings.append((ENV_NOTIFY_TELEGRAM, str(notif.telegram.enabled).lower()))
    if notif.telegram.bot_token is not None:
        mappings.append((ENV_TELEGRAM_BOT_TOKEN, notif.telegram.bot_token))
    if notif.telegram.chat_id is not None:
        mappings.append((ENV_TELEGRAM_CHAT_ID, notif.telegram.chat_id))
    if notif.discord.enabled is not None:
        mappings.append((ENV_NOTIFY_DISCORD, str(notif.discord.enabled).lower()))
    if notif.discord.webhook_url is not None:
        mappings.append((ENV_DISCORD_WEBHOOK_URL, notif.discord.webhook_url))
    if notif.slack.enabled is not None:
        mappings.append((ENV_NOTIFY_SLACK, str(notif.slack.enabled).lower()))
    if notif.slack.webhook_url is not None:
        mappings.append((ENV_SLACK_WEBHOOK_URL, notif.slack.webhook_url))

    for env_key, value in mappings:
        if value is not None and (overwrite or env_key not in os.environ):
            os.environ[env_key] = value


def generate_default_config() -> str:
    """주석 포함 기본 설정 파일 템플릿 반환."""
    return """\
# TubeArchive 설정 파일
# 위치: ~/.tubearchive/config.toml
#
# 우선순위: CLI 옵션 > 환경변수 > 이 파일 > 기본값
# 주석 해제 후 값을 수정하세요.

[general]
# output_dir = "~/Videos/output"            # TUBEARCHIVE_OUTPUT_DIR
# parallel = 1                              # TUBEARCHIVE_PARALLEL
# db_path = "~/.tubearchive/tubearchive.db" # TUBEARCHIVE_DB_PATH
# denoise = false                           # TUBEARCHIVE_DENOISE
# denoise_level = "medium"                  # light/medium/heavy (TUBEARCHIVE_DENOISE_LEVEL)
# normalize_audio = true                    # EBU R128 loudnorm (TUBEARCHIVE_NORMALIZE_AUDIO)
# group_sequences = true                    # 연속 파일 시퀀스 그룹핑 (TUBEARCHIVE_GROUP_SEQUENCES)
# fade_duration = 0.5                       # 기본 페이드 시간 (초, TUBEARCHIVE_FADE_DURATION)
# trim_silence = false                      # 무음 구간 제거 (TUBEARCHIVE_TRIM_SILENCE)
# silence_threshold = "-30dB"               # 무음 기준 데시벨 (TUBEARCHIVE_SILENCE_THRESHOLD)
# silence_min_duration = 2.0                # 최소 무음 길이(초, TUBEARCHIVE_SILENCE_MIN_DURATION)
# stabilize = false                         # 영상 안정화 (TUBEARCHIVE_STABILIZE)
# stabilize_strength = "medium"             # light/medium/heavy (TUBEARCHIVE_STABILIZE_STRENGTH)
# stabilize_crop = "crop"                   # crop/expand (TUBEARCHIVE_STABILIZE_CROP)
# subtitle_lang = "en"                      # 자막 언어 코드 (TUBEARCHIVE_SUBTITLE_LANG)
# subtitle_model = "base"                   # tiny/base/small/medium/large
# subtitle_format = "srt"                  # srt/vtt (TUBEARCHIVE_SUBTITLE_FORMAT)
# subtitle_burn = false                     # 자막 하드코딩(burn) 기본값 (TUBEARCHIVE_SUBTITLE_BURN)

[bgm]
# bgm_path = "~/Music/bgm.mp3"              # 배경음악 파일 경로 (TUBEARCHIVE_BGM_PATH)
# bgm_volume = 0.2                          # 배경음악 볼륨 0.0~1.0 (TUBEARCHIVE_BGM_VOLUME)
# bgm_loop = false                          # BGM 루프 재생 (TUBEARCHIVE_BGM_LOOP)

[youtube]
# client_secrets = "~/.tubearchive/client_secrets.json"
# token = "~/.tubearchive/youtube_token.json"
# playlist = ["PLxxxxxxxx"]
# upload_chunk_mb = 32                      # 1-256 (TUBEARCHIVE_UPLOAD_CHUNK_MB)
# upload_privacy = "unlisted"               # public/unlisted/private

[archive]
# policy = "keep"                           # keep/move/delete (TUBEARCHIVE_ARCHIVE_POLICY)
# destination = "~/Videos/archive"          # move 시 이동 경로 (TUBEARCHIVE_ARCHIVE_DESTINATION)

[backup]
# remote = "s3:bucket/path"                # rclone remote 또는 경로 (TUBEARCHIVE_BACKUP_REMOTE)
# include_originals = false                # true: 결과물 + 원본 백업 (기본 false)

[color_grading]
# auto_lut = false                          # 기기별 자동 LUT 적용 (TUBEARCHIVE_AUTO_LUT)
# [color_grading.device_luts]
# nikon = "~/LUTs/nikon_nlog_to_rec709.cube"
# gopro = "~/LUTs/gopro_flat_to_rec709.cube"

[template]
# intro = "~/templates/intro.mov"         # 병합 맨 앞에 붙일 템플릿
# outro = "~/templates/outro.mov"         # 병합 맨 뒤에 붙일 템플릿
[notification]
# enabled = false                         # 전역 알림 활성화 (TUBEARCHIVE_NOTIFY)
# on_transcode_complete = true            # 트랜스코딩 완료 알림
# on_merge_complete = true                # 병합 완료 알림
# on_upload_complete = true               # 업로드 완료 알림
# on_error = true                         # 에러 알림

[notification.macos]
# enabled = true                          # macOS 알림센터 (TUBEARCHIVE_NOTIFY_MACOS)
# sound = true                            # 알림음 재생 (TUBEARCHIVE_NOTIFY_MACOS_SOUND)

[notification.telegram]
# enabled = false                         # Telegram Bot (TUBEARCHIVE_NOTIFY_TELEGRAM)
# bot_token = ""                          # Bot 토큰 (TUBEARCHIVE_TELEGRAM_BOT_TOKEN)
# chat_id = ""                            # 채팅 ID (TUBEARCHIVE_TELEGRAM_CHAT_ID)

[notification.discord]
# enabled = false                         # Discord Webhook (TUBEARCHIVE_NOTIFY_DISCORD)
# webhook_url = ""                        # Webhook URL (TUBEARCHIVE_DISCORD_WEBHOOK_URL)

[notification.slack]
# enabled = false                         # Slack Webhook (TUBEARCHIVE_NOTIFY_SLACK)
# webhook_url = ""                        # Webhook URL (TUBEARCHIVE_SLACK_WEBHOOK_URL)

[hooks]
# timeout_sec = 60                         # 훅 기본 타임아웃(초)
# on_transcode = ["/path/to/transcode_hook.sh"] # 트랜스코딩 완료 후 실행
# on_merge = ["/path/to/merge_hook.sh"]      # 병합 완료 후 실행
# on_upload = "/path/to/upload_hook.sh"      # 업로드 완료 후 실행
# on_error = "/path/to/error_hook.sh"        # 에러 발생 시 실행

[watch]
# paths = ["/Users/you/Videos/Incoming"]     # 감시 대상 디렉토리 목록
# poll_interval = 1.0                      # 파일 안정성 대기(초)
# stability_checks = 2                      # 동일 크기 반복 횟수
# log_path = "/Users/you/.tubearchive/watch.log" # watch 모드 로그 파일
"""


# ---------------------------------------------------------------------------
# 환경변수 기본값 헬퍼
# ---------------------------------------------------------------------------


def parse_env_bool(value: str) -> bool:
    """환경변수 문자열을 bool로 변환한다.

    '1', 'true', 'yes', 'y', 'on' (대소문자 무시)이면 True, 그 외 False.

    Args:
        value: 환경변수 원본 문자열.

    Returns:
        변환된 bool 값.
    """
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_default_output_dir() -> Path | None:
    """환경변수에서 기본 출력 디렉토리를 가져온다.

    ``TUBEARCHIVE_OUTPUT_DIR`` 환경변수가 유효한 디렉토리를 가리킬 때만
    ``Path`` 를 반환하고, 그 외에는 ``None`` 을 반환한다.

    Returns:
        유효한 디렉토리 경로 또는 None.
    """
    env_dir = os.environ.get(ENV_OUTPUT_DIR)
    if env_dir:
        path = Path(env_dir)
        if path.is_dir():
            return path
        logger.warning("%s=%s is not a valid directory", ENV_OUTPUT_DIR, env_dir)
    return None


def get_default_parallel() -> int:
    """환경변수에서 기본 병렬 처리 수를 가져온다.

    ``TUBEARCHIVE_PARALLEL`` 환경변수에서 정수를 읽어 반환한다.
    유효하지 않은 값이면 기본값 **1** (순차 처리)을 반환한다.

    Returns:
        병렬 워커 수 (최소 1).
    """
    env_parallel = os.environ.get(ENV_PARALLEL)
    if env_parallel:
        try:
            val = int(env_parallel)
            if val >= 1:
                return val
            logger.warning("%s=%s must be >= 1, using 1", ENV_PARALLEL, env_parallel)
        except ValueError:
            logger.warning("%s=%s is not a valid number", ENV_PARALLEL, env_parallel)
    return 1


def _get_env_bool(env_key: str, *, default: bool = False) -> bool:
    """환경변수에서 bool 값을 가져온다.

    Args:
        env_key: 환경변수 이름
        default: 미설정 시 기본값

    Returns:
        파싱된 bool 값.
    """
    env_val = os.environ.get(env_key)
    if env_val is None:
        return default
    return parse_env_bool(env_val)


def get_default_denoise() -> bool:
    """환경변수 ``TUBEARCHIVE_DENOISE`` 에서 노이즈 제거 활성화 여부를 가져온다."""
    return _get_env_bool(ENV_DENOISE)


def get_default_denoise_level() -> str | None:
    """환경변수 ``TUBEARCHIVE_DENOISE_LEVEL`` 에서 노이즈 제거 강도를 가져온다.

    Returns:
        ``light`` / ``medium`` / ``heavy`` 또는 None (미설정·유효하지 않은 값).
    """
    env_level = os.environ.get(ENV_DENOISE_LEVEL)
    if not env_level:
        return None
    normalized = env_level.strip().lower()
    if normalized in {"light", "medium", "heavy"}:
        return normalized
    logger.warning("%s=%s is not a valid level", ENV_DENOISE_LEVEL, env_level)
    return None


def get_default_normalize_audio() -> bool:
    """환경변수 ``TUBEARCHIVE_NORMALIZE_AUDIO`` 에서 라우드니스 정규화 여부를 가져온다."""
    return _get_env_bool(ENV_NORMALIZE_AUDIO, default=True)


def get_default_group_sequences() -> bool:
    """환경변수 ``TUBEARCHIVE_GROUP_SEQUENCES`` 에서 시퀀스 그룹핑 여부를 가져온다."""
    return _get_env_bool(ENV_GROUP_SEQUENCES, default=True)


def get_default_fade_duration() -> float:
    """환경변수에서 기본 페이드 시간(초)을 가져온다.

    ``TUBEARCHIVE_FADE_DURATION`` 환경변수에서 실수를 읽어 반환한다.
    유효하지 않거나 음수이면 기본값 **0.5** 를 반환한다.

    Returns:
        페이드 시간(초, >= 0).
    """
    env_val = os.environ.get(ENV_FADE_DURATION)
    if not env_val:
        return 0.5
    try:
        val = float(env_val)
    except ValueError:
        logger.warning("%s=%s is not a valid number", ENV_FADE_DURATION, env_val)
        return 0.5
    if val < 0:
        logger.warning("%s=%s must be >= 0, using 0.5", ENV_FADE_DURATION, env_val)
        return 0.5
    return val


def get_default_backup_remote() -> str | None:
    """환경변수 ``TUBEARCHIVE_BACKUP_REMOTE`` 에서 백업 원격 경로를 가져온다."""
    env_val = os.environ.get(ENV_BACKUP_REMOTE)
    if not env_val:
        return None
    return env_val.strip()


def get_default_backup_include_originals() -> bool:
    """환경변수 ``TUBEARCHIVE_BACKUP_INCLUDE_ORIGINALS`` 값을 bool로 반환."""
    return _get_env_bool(ENV_BACKUP_INCLUDE_ORIGINALS)


def get_default_archive_policy() -> str:
    """환경변수 ``TUBEARCHIVE_ARCHIVE_POLICY`` 에서 아카이브 정책을 가져온다.

    Returns:
        ``keep`` / ``move`` / ``delete`` (기본값: ``keep``).
    """
    env_policy = os.environ.get(ENV_ARCHIVE_POLICY)
    if not env_policy:
        return "keep"
    normalized = env_policy.strip().lower()
    if normalized in {"keep", "move", "delete"}:
        return normalized
    logger.warning("%s=%s is not a valid policy", ENV_ARCHIVE_POLICY, env_policy)
    return "keep"


def get_default_archive_destination() -> Path | None:
    """환경변수 ``TUBEARCHIVE_ARCHIVE_DESTINATION`` 에서 아카이브 경로를 가져온다.

    Returns:
        유효한 디렉토리 경로 또는 None.
    """
    env_dest = os.environ.get(ENV_ARCHIVE_DESTINATION)
    if env_dest:
        path = Path(env_dest).expanduser()
        return path
    return None


def _get_template_file_path(env_key: str) -> Path | None:
    """템플릿 경로 환경변수 값을 파일 경로로 변환한다."""
    env_path = os.environ.get(env_key)
    if not env_path:
        return None

    path = Path(env_path).expanduser()
    if path.is_file():
        return path
    logger.warning("%s=%s is not a valid file", env_key, env_path)
    return None


def get_default_template_intro() -> Path | None:
    """환경변수 ``TUBEARCHIVE_TEMPLATE_INTRO`` 템플릿 경로를 반환한다."""
    return _get_template_file_path(ENV_TEMPLATE_INTRO)


def get_default_template_outro() -> Path | None:
    """환경변수 ``TUBEARCHIVE_TEMPLATE_OUTRO`` 템플릿 경로를 반환한다."""
    return _get_template_file_path(ENV_TEMPLATE_OUTRO)


def get_default_bgm_path() -> Path | None:
    """환경변수에서 기본 BGM 파일 경로를 가져온다.

    ``TUBEARCHIVE_BGM_PATH`` 환경변수가 유효한 파일을 가리킬 때만
    ``Path`` 를 반환하고, 그 외에는 ``None`` 을 반환한다.

    Returns:
        유효한 파일 경로 또는 None.
    """
    env_path = os.environ.get(ENV_BGM_PATH)
    if env_path:
        path = Path(env_path).expanduser()
        if path.is_file():
            return path
        logger.warning("%s=%s is not a valid file", ENV_BGM_PATH, env_path)
    return None


def get_default_bgm_volume() -> float | None:
    """환경변수에서 기본 BGM 볼륨을 가져온다.

    ``TUBEARCHIVE_BGM_VOLUME`` 환경변수에서 실수를 읽어 반환한다.
    범위는 0.0~1.0이며, 범위 밖이면 None을 반환한다.

    Returns:
        BGM 볼륨 (0.0~1.0) 또는 None.
    """
    env_val = os.environ.get(ENV_BGM_VOLUME)
    if not env_val:
        return None
    try:
        val = float(env_val)
    except ValueError:
        logger.warning("%s=%s is not a valid number", ENV_BGM_VOLUME, env_val)
        return None
    if not (0.0 <= val <= 1.0):
        logger.warning("%s=%s must be in range [0.0, 1.0]", ENV_BGM_VOLUME, env_val)
        return None
    return val


def get_default_bgm_loop() -> bool:
    """환경변수 ``TUBEARCHIVE_BGM_LOOP`` 에서 BGM 루프 재생 여부를 가져온다."""
    return _get_env_bool(ENV_BGM_LOOP)


def get_default_stabilize() -> bool:
    """환경변수 ``TUBEARCHIVE_STABILIZE`` 에서 영상 안정화 활성화 여부를 가져온다."""
    return _get_env_bool(ENV_STABILIZE)


def get_default_stabilize_strength() -> str | None:
    """환경변수 ``TUBEARCHIVE_STABILIZE_STRENGTH`` 에서 안정화 강도를 가져온다.

    Returns:
        ``light`` / ``medium`` / ``heavy`` 또는 None (미설정·유효하지 않은 값).
    """
    env_val = os.environ.get(ENV_STABILIZE_STRENGTH)
    if not env_val:
        return None
    normalized = env_val.strip().lower()
    if normalized in {"light", "medium", "heavy"}:
        return normalized
    logger.warning("%s=%s is not a valid strength", ENV_STABILIZE_STRENGTH, env_val)
    return None


def get_default_stabilize_crop() -> str | None:
    """환경변수 ``TUBEARCHIVE_STABILIZE_CROP`` 에서 안정화 크롭 모드를 가져온다.

    Returns:
        ``crop`` / ``expand`` 또는 None (미설정·유효하지 않은 값).
    """
    env_val = os.environ.get(ENV_STABILIZE_CROP)
    if not env_val:
        return None
    normalized = env_val.strip().lower()
    if normalized in {"crop", "expand"}:
        return normalized
    logger.warning("%s=%s is not a valid crop mode", ENV_STABILIZE_CROP, env_val)
    return None


def get_default_watch_paths() -> tuple[str, ...]:
    """환경변수 ``TUBEARCHIVE_WATCH_PATHS`` 에서 watch 대상 경로를 가져온다."""
    raw = os.environ.get(ENV_WATCH_PATHS)
    if not raw:
        return ()
    raw_paths = [p.strip() for p in raw.split(",")]
    return tuple(p for p in raw_paths if p)


def get_default_watch_poll_interval() -> float:
    """환경변수 ``TUBEARCHIVE_WATCH_POLL_INTERVAL`` 에서 값을 읽는다."""
    env_val = os.environ.get(ENV_WATCH_POLL_INTERVAL)
    if not env_val:
        return 1.0
    try:
        val = float(env_val)
    except ValueError:
        logger.warning("%s=%s is not a valid number", ENV_WATCH_POLL_INTERVAL, env_val)
        return 1.0
    if val <= 0:
        logger.warning("%s=%s must be > 0, using 1.0", ENV_WATCH_POLL_INTERVAL, env_val)
        return 1.0
    return val


def get_default_watch_stability_checks() -> int:
    """환경변수 ``TUBEARCHIVE_WATCH_STABILITY_CHECKS`` 에서 값을 읽는다."""
    env_val = os.environ.get(ENV_WATCH_STABILITY_CHECKS)
    if not env_val:
        return 2
    try:
        val = int(env_val)
    except ValueError:
        logger.warning(
            "%s=%s is not a valid integer",
            ENV_WATCH_STABILITY_CHECKS,
            env_val,
        )
        return 2
    if val <= 0:
        logger.warning("%s=%s must be > 0, using 2", ENV_WATCH_STABILITY_CHECKS, env_val)
        return 2
    return val


def get_default_watch_log_path() -> Path | None:
    """환경변수 ``TUBEARCHIVE_WATCH_LOG_PATH`` 에서 경로를 가져온다."""
    raw = os.environ.get(ENV_WATCH_LOG)
    if not raw:
        return None
    return Path(raw).expanduser()


def get_default_auto_lut() -> bool:
    """환경변수 ``TUBEARCHIVE_AUTO_LUT`` 에서 자동 LUT 적용 여부를 가져온다."""
    return _get_env_bool(ENV_AUTO_LUT)


def get_default_notify() -> bool:
    """환경변수 ``TUBEARCHIVE_NOTIFY`` 에서 알림 활성화 여부를 가져온다."""
    return _get_env_bool(ENV_NOTIFY)


def get_default_subtitle_lang() -> str | None:
    """환경변수 ``TUBEARCHIVE_SUBTITLE_LANG`` 에서 자막 언어를 가져온다."""
    env_lang = os.environ.get(ENV_SUBTITLE_LANG)
    if not env_lang:
        return None
    normalized = env_lang.strip().lower()
    return normalized or None


def get_default_subtitle_model() -> str | None:
    """환경변수 ``TUBEARCHIVE_SUBTITLE_MODEL`` 에서 모델을 가져온다.

    ``tiny/base/small/medium/large``만 허용한다.
    """
    env_model = os.environ.get(ENV_SUBTITLE_MODEL)
    if not env_model:
        return None
    normalized = env_model.strip().lower()
    if normalized in {"tiny", "base", "small", "medium", "large"}:
        return normalized
    logger.warning("%s=%s is not a valid model", ENV_SUBTITLE_MODEL, env_model)
    return None


def get_default_subtitle_format() -> str | None:
    """환경변수 ``TUBEARCHIVE_SUBTITLE_FORMAT`` 에서 자막 포맷을 가져온다.

    ``srt``/``vtt``만 허용한다.
    """
    env_format = os.environ.get(ENV_SUBTITLE_FORMAT)
    if not env_format:
        return None
    normalized = env_format.strip().lower()
    if normalized in {"srt", "vtt"}:
        return normalized
    logger.warning("%s=%s is not a valid format", ENV_SUBTITLE_FORMAT, env_format)
    return None


def get_default_subtitle_burn() -> bool:
    """환경변수 ``TUBEARCHIVE_SUBTITLE_BURN`` 에서 자막 하드코딩 기본값을 가져온다."""
    return _get_env_bool(ENV_SUBTITLE_BURN)
