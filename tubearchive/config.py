"""TOML 설정 파일 지원.

~/.tubearchive/config.toml로 기본 설정값을 관리한다.
환경변수 Shim 패턴: config 값을 환경변수에 주입 (미설정인 경우만).

우선순위: CLI 옵션 > 환경변수 > 설정 파일 > 기본값
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


@dataclass(frozen=True)
class GeneralConfig:
    """[general] 섹션 설정."""

    output_dir: str | None = None
    parallel: int | None = None
    db_path: str | None = None
    denoise: bool | None = None
    denoise_level: str | None = None
    normalize_audio: bool | None = None
    group_sequences: bool | None = None
    fade_duration: float | None = None


@dataclass(frozen=True)
class YouTubeConfig:
    """[youtube] 섹션 설정."""

    client_secrets: str | None = None
    token: str | None = None
    playlist: list[str] = field(default_factory=list)
    upload_chunk_mb: int | None = None
    upload_privacy: str | None = None


@dataclass(frozen=True)
class AppConfig:
    """애플리케이션 전체 설정."""

    general: GeneralConfig = field(default_factory=GeneralConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)


def _warn_type(field: str, expected: str, value: object) -> None:
    """타입 불일치 경고 출력."""
    logger.warning(
        "config: %s 타입 오류 (expected %s, got %s)",
        field,
        expected,
        type(value).__name__,
    )


def get_default_config_path() -> Path:
    """기본 설정 파일 경로 반환."""
    return Path.home() / ".tubearchive" / "config.toml"


def _parse_general(data: dict[str, object]) -> GeneralConfig:
    """[general] 섹션 파싱. 타입 오류 시 해당 필드 무시."""
    output_dir: str | None = None
    parallel: int | None = None
    db_path: str | None = None
    denoise: bool | None = None
    denoise_level: str | None = None
    normalize_audio: bool | None = None
    group_sequences: bool | None = None
    fade_duration: float | None = None

    raw_output_dir = data.get("output_dir")
    if isinstance(raw_output_dir, str):
        output_dir = raw_output_dir
    elif raw_output_dir is not None:
        _warn_type("general.output_dir", "str", raw_output_dir)

    raw_parallel = data.get("parallel")
    if isinstance(raw_parallel, int) and not isinstance(raw_parallel, bool):
        parallel = raw_parallel
    elif raw_parallel is not None:
        _warn_type("general.parallel", "int", raw_parallel)

    raw_db_path = data.get("db_path")
    if isinstance(raw_db_path, str):
        db_path = raw_db_path
    elif raw_db_path is not None:
        _warn_type("general.db_path", "str", raw_db_path)

    raw_denoise = data.get("denoise")
    if isinstance(raw_denoise, bool):
        denoise = raw_denoise
    elif raw_denoise is not None:
        _warn_type("general.denoise", "bool", raw_denoise)

    raw_denoise_level = data.get("denoise_level")
    if isinstance(raw_denoise_level, str):
        if raw_denoise_level in ("light", "medium", "heavy"):
            denoise_level = raw_denoise_level
        else:
            logger.warning("config: general.denoise_level 값 오류: %r", raw_denoise_level)
    elif raw_denoise_level is not None:
        _warn_type("general.denoise_level", "str", raw_denoise_level)

    raw_normalize_audio = data.get("normalize_audio")
    if isinstance(raw_normalize_audio, bool):
        normalize_audio = raw_normalize_audio
    elif raw_normalize_audio is not None:
        _warn_type("general.normalize_audio", "bool", raw_normalize_audio)

    raw_group_sequences = data.get("group_sequences")
    if isinstance(raw_group_sequences, bool):
        group_sequences = raw_group_sequences
    elif raw_group_sequences is not None:
        _warn_type("general.group_sequences", "bool", raw_group_sequences)

    raw_fade_duration = data.get("fade_duration")
    if isinstance(raw_fade_duration, (int, float)) and not isinstance(raw_fade_duration, bool):
        if raw_fade_duration >= 0:
            fade_duration = float(raw_fade_duration)
        else:
            logger.warning("config: general.fade_duration 값 오류: %r", raw_fade_duration)
    elif raw_fade_duration is not None:
        _warn_type("general.fade_duration", "float", raw_fade_duration)

    return GeneralConfig(
        output_dir=output_dir,
        parallel=parallel,
        db_path=db_path,
        denoise=denoise,
        denoise_level=denoise_level,
        normalize_audio=normalize_audio,
        group_sequences=group_sequences,
        fade_duration=fade_duration,
    )


def _parse_youtube(data: dict[str, object]) -> YouTubeConfig:
    """[youtube] 섹션 파싱. 타입 오류 시 해당 필드 무시."""
    client_secrets: str | None = None
    token: str | None = None
    playlist: list[str] = []
    upload_chunk_mb: int | None = None
    upload_privacy: str | None = None

    raw_secrets = data.get("client_secrets")
    if isinstance(raw_secrets, str):
        client_secrets = raw_secrets
    elif raw_secrets is not None:
        _warn_type("youtube.client_secrets", "str", raw_secrets)

    raw_token = data.get("token")
    if isinstance(raw_token, str):
        token = raw_token
    elif raw_token is not None:
        _warn_type("youtube.token", "str", raw_token)

    raw_playlist = data.get("playlist")
    if isinstance(raw_playlist, list):
        playlist = [item for item in raw_playlist if isinstance(item, str)]
        skipped = len(raw_playlist) - len(playlist)
        if skipped > 0:
            logger.warning(
                "config: youtube.playlist에 비문자열 항목 %d개 무시됨",
                skipped,
            )
    elif isinstance(raw_playlist, str):
        # 단일 문자열도 허용
        playlist = [raw_playlist]
    elif raw_playlist is not None:
        _warn_type("youtube.playlist", "list|str", raw_playlist)

    raw_chunk = data.get("upload_chunk_mb")
    if isinstance(raw_chunk, int) and not isinstance(raw_chunk, bool):
        if 1 <= raw_chunk <= 256:
            upload_chunk_mb = raw_chunk
        else:
            logger.warning("config: youtube.upload_chunk_mb 범위 초과: %d (1-256)", raw_chunk)
    elif raw_chunk is not None:
        _warn_type("youtube.upload_chunk_mb", "int", raw_chunk)

    raw_privacy = data.get("upload_privacy")
    if isinstance(raw_privacy, str):
        if raw_privacy in ("public", "unlisted", "private"):
            upload_privacy = raw_privacy
        else:
            logger.warning("config: youtube.upload_privacy 값 오류: %r", raw_privacy)
    elif raw_privacy is not None:
        _warn_type("youtube.upload_privacy", "str", raw_privacy)

    return YouTubeConfig(
        client_secrets=client_secrets,
        token=token,
        playlist=playlist,
        upload_chunk_mb=upload_chunk_mb,
        upload_privacy=upload_privacy,
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
    youtube_data = raw.get("youtube", {})

    if isinstance(general_data, dict):
        general = _parse_general(general_data)
    else:
        if general_data:
            logger.warning(
                "config: [general] 섹션이 테이블이 아닙니다 (got %s)",
                type(general_data).__name__,
            )
        general = GeneralConfig()

    if isinstance(youtube_data, dict):
        youtube = _parse_youtube(youtube_data)
    else:
        if youtube_data:
            logger.warning(
                "config: [youtube] 섹션이 테이블이 아닙니다 (got %s)",
                type(youtube_data).__name__,
            )
        youtube = YouTubeConfig()

    return AppConfig(general=general, youtube=youtube)


def apply_config_to_env(config: AppConfig) -> None:
    """
    설정값을 환경변수에 주입 (미설정인 경우만).

    이미 설정된 환경변수는 보존된다 (환경변수 > config).
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

    # playlist: list → CSV
    if config.youtube.playlist:
        mappings.append((ENV_YOUTUBE_PLAYLIST, ",".join(config.youtube.playlist)))

    for env_key, value in mappings:
        if value is not None and env_key not in os.environ:
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
# normalize_audio = false                   # EBU R128 loudnorm (TUBEARCHIVE_NORMALIZE_AUDIO)
# group_sequences = true                    # 연속 파일 시퀀스 그룹핑 (TUBEARCHIVE_GROUP_SEQUENCES)
# fade_duration = 0.5                       # 기본 페이드 시간 (초, TUBEARCHIVE_FADE_DURATION)

[youtube]
# client_secrets = "~/.tubearchive/client_secrets.json"
# token = "~/.tubearchive/youtube_token.json"
# playlist = ["PLxxxxxxxx"]
# upload_chunk_mb = 32                      # 1-256 (TUBEARCHIVE_UPLOAD_CHUNK_MB)
# upload_privacy = "unlisted"               # public/unlisted/private
"""
