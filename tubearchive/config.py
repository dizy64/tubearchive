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
class ArchiveConfig:
    """``config.toml`` 의 ``[archive]`` 섹션.

    트랜스코딩 완료 후 원본 파일 관리 정책을 정의한다.
    """

    policy: str | None = None  # keep/move/delete
    destination: str | None = None  # move 시 이동 경로


@dataclass(frozen=True)
class ColorGradingConfig:
    """``config.toml`` 의 ``[color_grading]`` 섹션.

    LUT(Look-Up Table) 기반 컬러 그레이딩 설정을 관리한다.
    """

    auto_lut: bool | None = None
    """기기 모델명 기반 자동 LUT 적용 여부."""
    device_luts: dict[str, str] = field(default_factory=dict)
    """기기 키워드 → LUT 파일 경로 매핑."""


@dataclass(frozen=True)
class AppConfig:
    """애플리케이션 전체 설정.

    [general] + [bgm] + [youtube] + [archive] + [color_grading] 통합.
    """

    general: GeneralConfig = field(default_factory=GeneralConfig)
    bgm: BGMConfig = field(default_factory=BGMConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    archive: ArchiveConfig = field(default_factory=ArchiveConfig)
    color_grading: ColorGradingConfig = field(default_factory=ColorGradingConfig)


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

    if isinstance(color_grading_data, dict):
        color_grading = _parse_color_grading(color_grading_data)
    else:
        if color_grading_data:
            logger.warning(
                "config: [color_grading] 섹션이 테이블이 아닙니다 (got %s)",
                type(color_grading_data).__name__,
            )
        color_grading = ColorGradingConfig()

    return AppConfig(
        general=general,
        bgm=bgm,
        youtube=youtube,
        archive=archive,
        color_grading=color_grading,
    )


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

    # archive policy
    if config.archive.policy is not None:
        mappings.append((ENV_ARCHIVE_POLICY, config.archive.policy))
    if config.archive.destination is not None:
        mappings.append((ENV_ARCHIVE_DESTINATION, config.archive.destination))

    # color grading
    if config.color_grading.auto_lut is not None:
        mappings.append((ENV_AUTO_LUT, str(config.color_grading.auto_lut).lower()))

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
# normalize_audio = true                    # EBU R128 loudnorm (TUBEARCHIVE_NORMALIZE_AUDIO)
# group_sequences = true                    # 연속 파일 시퀀스 그룹핑 (TUBEARCHIVE_GROUP_SEQUENCES)
# fade_duration = 0.5                       # 기본 페이드 시간 (초, TUBEARCHIVE_FADE_DURATION)
# trim_silence = false                      # 무음 구간 제거 (TUBEARCHIVE_TRIM_SILENCE)
# silence_threshold = "-30dB"               # 무음 기준 데시벨 (TUBEARCHIVE_SILENCE_THRESHOLD)
# silence_min_duration = 2.0                # 최소 무음 길이(초, TUBEARCHIVE_SILENCE_MIN_DURATION)
# stabilize = false                         # 영상 안정화 (TUBEARCHIVE_STABILIZE)
# stabilize_strength = "medium"             # light/medium/heavy (TUBEARCHIVE_STABILIZE_STRENGTH)
# stabilize_crop = "crop"                   # crop/expand (TUBEARCHIVE_STABILIZE_CROP)

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

[color_grading]
# auto_lut = false                          # 기기별 자동 LUT 적용 (TUBEARCHIVE_AUTO_LUT)
# [color_grading.device_luts]
# nikon = "~/LUTs/nikon_nlog_to_rec709.cube"
# gopro = "~/LUTs/gopro_flat_to_rec709.cube"
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


def get_default_auto_lut() -> bool:
    """환경변수 ``TUBEARCHIVE_AUTO_LUT`` 에서 자동 LUT 적용 여부를 가져온다."""
    return _get_env_bool(ENV_AUTO_LUT)
