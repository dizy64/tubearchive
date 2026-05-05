"""TUI 옵션 상태 모델.

TUI 위젯 상태를 담는 가변 데이터클래스와 카테고리 정의.
``bridge.build_validated_args()`` 에서 :class:`ValidatedArgs` 로 변환된다.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import tomlkit

if TYPE_CHECKING:
    from tubearchive.config import AppConfig

# ---------------------------------------------------------------------------
# 옵션 상태 데이터클래스
# ---------------------------------------------------------------------------


@dataclass
class TuiOptionState:
    """TUI 위젯 상태를 담는 가변 데이터클래스.

    각 필드는 ValidatedArgs의 대응 필드와 동일한 기본값을 사용한다.
    Path 필드는 문자열로 저장하고 bridge에서 Path로 변환한다.
    """

    # General
    output_dir: str = ""
    parallel: int = 1
    dry_run: bool = False
    no_resume: bool = False
    keep_temp: bool = False

    # Audio
    normalize_audio: bool = False
    denoise: bool = False
    denoise_level: str = "medium"

    # BGM
    bgm_path: str = ""
    bgm_volume: float = 0.2
    bgm_loop: bool = False

    # Silence
    trim_silence: bool = False
    detect_silence: bool = False
    silence_threshold: str = "-30dB"
    silence_min_duration: float = 2.0

    # Video Effects
    stabilize: bool = False
    stabilize_strength: str = "medium"
    stabilize_crop: str = "crop"
    fade_duration: float = 0.5

    # Color
    lut_path: str = ""
    auto_lut: bool = False
    lut_before_hdr: bool = False

    # Watermark
    watermark: bool = False
    watermark_text: str = ""
    watermark_pos: str = "bottom-right"
    watermark_size: int = 48
    watermark_color: str = "white"
    watermark_alpha: float = 0.85

    # Sequence
    group_sequences: bool = True
    sort_key: str = "time"
    reorder: bool = False
    exclude_patterns: str = ""
    include_only_patterns: str = ""

    # Split
    split_duration: str = ""
    split_size: str = ""

    # Timelapse
    timelapse_speed: str = ""
    timelapse_audio: bool = False
    timelapse_resolution: str = ""

    # Thumbnail
    thumbnail: bool = False
    thumbnail_quality: int = 2

    # Subtitle
    subtitle: bool = False
    subtitle_model: str = "tiny"
    subtitle_format: str = "srt"
    subtitle_burn: bool = False

    # Template
    template_intro: str = ""
    template_outro: str = ""

    # Archive
    archive_originals: str = ""
    archive_force: bool = False

    # Upload / Project
    upload: bool = False
    project: str = ""
    notify: bool = False

    # YouTube
    upload_privacy: str = "unlisted"
    upload_playlists: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 카테고리 정의
# ---------------------------------------------------------------------------


WidgetKind = Literal["switch", "input", "select", "input_float", "input_int"]


@dataclass(frozen=True)
class OptionDef:
    """단일 옵션 정의."""

    field: str
    """TuiOptionState 필드명."""

    label: str
    """화면에 표시할 라벨."""

    widget: WidgetKind
    """위젯 종류."""

    choices: tuple[tuple[str, str], ...] = ()
    """Select 위젯용 (label, value) 목록."""

    hint: str = ""
    """플레이스홀더 또는 도움말."""


@dataclass(frozen=True)
class CategoryDef:
    """옵션 카테고리 정의."""

    title: str
    options: tuple[OptionDef, ...]
    collapsed: bool = True


CATEGORY_DEFS: tuple[CategoryDef, ...] = (
    CategoryDef(
        title="General",
        collapsed=False,
        options=(
            OptionDef("dry_run", "Dry Run", "switch"),
            OptionDef("no_resume", "Resume 건너뜀", "switch"),
            OptionDef("keep_temp", "임시 파일 유지", "switch"),
            OptionDef("parallel", "병렬 트랜스코딩 수", "input_int", hint="1"),
            OptionDef("output_dir", "출력 디렉토리", "input", hint="기본: 입력과 동일 위치"),
        ),
    ),
    CategoryDef(
        title="Audio",
        options=(
            OptionDef("normalize_audio", "EBU R128 Loudnorm", "switch"),
            OptionDef("denoise", "오디오 노이즈 제거", "switch"),
            OptionDef(
                "denoise_level",
                "노이즈 제거 강도",
                "select",
                choices=(("Light", "light"), ("Medium", "medium"), ("Heavy", "heavy")),
            ),
        ),
    ),
    CategoryDef(
        title="BGM",
        options=(
            OptionDef("bgm_path", "BGM 파일 경로", "input", hint="예: ~/Music/bgm.mp3"),
            OptionDef("bgm_volume", "BGM 볼륨 (0.0~1.0)", "input_float", hint="0.2"),
            OptionDef("bgm_loop", "BGM 루프 재생", "switch"),
        ),
    ),
    CategoryDef(
        title="Silence",
        options=(
            OptionDef("trim_silence", "무음 구간 자동 제거", "switch"),
            OptionDef("detect_silence", "무음 구간 표시만 (제거 안 함)", "switch"),
            OptionDef("silence_threshold", "무음 기준 (dB)", "input", hint="-30dB"),
            OptionDef("silence_min_duration", "최소 무음 길이 (초)", "input_float", hint="2.0"),
        ),
    ),
    CategoryDef(
        title="Video Effects",
        options=(
            OptionDef("stabilize", "영상 안정화 (vidstab)", "switch"),
            OptionDef(
                "stabilize_strength",
                "안정화 강도",
                "select",
                choices=(("Light", "light"), ("Medium", "medium"), ("Heavy", "heavy")),
            ),
            OptionDef(
                "stabilize_crop",
                "안정화 크롭",
                "select",
                choices=(("Crop", "crop"), ("Expand", "expand")),
            ),
            OptionDef("fade_duration", "페이드 시간 (초)", "input_float", hint="0.5"),
        ),
    ),
    CategoryDef(
        title="Color Grading",
        options=(
            OptionDef("auto_lut", "기기별 자동 LUT 매칭", "switch"),
            OptionDef("lut_path", "LUT 파일 경로", "input", hint="예: ~/LUTs/nikon.cube"),
            OptionDef("lut_before_hdr", "LUT를 HDR 변환 전 적용", "switch"),
        ),
    ),
    CategoryDef(
        title="Watermark",
        options=(
            OptionDef("watermark", "워터마크 사용", "switch"),
            OptionDef(
                "watermark_pos",
                "위치",
                "select",
                choices=(
                    ("우하단", "bottom-right"),
                    ("우상단", "top-right"),
                    ("좌하단", "bottom-left"),
                    ("좌상단", "top-left"),
                    ("중앙", "center"),
                ),
            ),
            OptionDef(
                "watermark_text",
                "워터마크 텍스트",
                "input",
                hint="비우면 자동 생성 (촬영일|위치)",
            ),
            OptionDef("watermark_size", "크기 (pt)", "input_int", hint="48"),
            OptionDef("watermark_color", "색상", "input", hint="white"),
            OptionDef("watermark_alpha", "투명도 (0.0~1.0)", "input_float", hint="0.85"),
        ),
    ),
    CategoryDef(
        title="Sequence",
        options=(
            OptionDef("group_sequences", "연속 파일 시퀀스 그룹핑", "switch"),
            OptionDef(
                "sort_key",
                "정렬 기준",
                "select",
                choices=(("촬영 시각", "time"), ("파일명", "name"), ("크기", "size")),
            ),
            OptionDef("reorder", "수동 정렬 모드", "switch"),
            OptionDef("exclude_patterns", "제외 패턴 (쉼표)", "input", hint="예: *.tmp, test_*"),
        ),
    ),
    CategoryDef(
        title="Split",
        options=(
            OptionDef("split_duration", "시간 단위 분할", "input", hint="예: 1h, 30m"),
            OptionDef("split_size", "크기 단위 분할", "input", hint="예: 10G, 500M"),
        ),
    ),
    CategoryDef(
        title="Timelapse",
        options=(
            OptionDef("timelapse_speed", "배속 (2~60)", "input", hint="비어있으면 비활성"),
            OptionDef("timelapse_audio", "오디오 유지 (atempo)", "switch"),
            OptionDef(
                "timelapse_resolution",
                "해상도",
                "select",
                choices=(
                    ("원본", ""),
                    ("4K (3840x2160)", "4k"),
                    ("1080p", "1080p"),
                    ("720p", "720p"),
                ),
            ),
        ),
    ),
    CategoryDef(
        title="Thumbnail",
        options=(
            OptionDef("thumbnail", "썸네일 생성", "switch"),
            OptionDef("thumbnail_quality", "품질 (1~31, 낮을수록 고품질)", "input_int", hint="2"),
        ),
    ),
    CategoryDef(
        title="Subtitle",
        options=(
            OptionDef("subtitle", "자막 생성 (Whisper)", "switch"),
            OptionDef(
                "subtitle_model",
                "모델 크기",
                "select",
                choices=(
                    ("Tiny", "tiny"),
                    ("Base", "base"),
                    ("Small", "small"),
                    ("Medium", "medium"),
                ),
            ),
            OptionDef(
                "subtitle_format",
                "포맷",
                "select",
                choices=(("SRT", "srt"), ("VTT", "vtt"), ("ASS", "ass")),
            ),
            OptionDef("subtitle_burn", "자막 영상에 삽입 (burn-in)", "switch"),
        ),
    ),
    CategoryDef(
        title="Template",
        options=(
            OptionDef("template_intro", "인트로 영상 경로", "input", hint="예: ~/intro.mp4"),
            OptionDef("template_outro", "아웃트로 영상 경로", "input", hint="예: ~/outro.mp4"),
        ),
    ),
    CategoryDef(
        title="Archive",
        options=(
            OptionDef(
                "archive_originals",
                "원본 아카이브 대상 경로",
                "input",
                hint="예: ~/Videos/archive",
            ),
            OptionDef("archive_force", "삭제 확인 프롬프트 생략", "switch"),
        ),
    ),
    CategoryDef(
        title="Upload / Project",
        options=(
            OptionDef("upload", "YouTube 업로드", "switch"),
            OptionDef("project", "프로젝트 이름", "input", hint="생략 시 선택 폴더명 사용"),
            OptionDef("notify", "완료 알림", "switch"),
        ),
    ),
)


def state_from_config(config: AppConfig) -> TuiOptionState:
    """환경변수와 AppConfig를 반영한 TuiOptionState를 반환한다.

    우선순위: **환경변수 > config.toml > TuiOptionState 기본값**

    ``apply_config_to_env(config)`` 가 호출된 뒤 실행되므로,
    ``get_default_*()`` 헬퍼가 이미 ENV > config 순으로 병합된 값을 반환한다.
    env가 미설정이고 config도 None이면 TuiOptionState 기본값을 유지한다.
    """
    import contextlib
    import os

    from tubearchive.config import (
        ENV_BGM_PATH,
        ENV_BGM_VOLUME,
        ENV_OUTPUT_DIR,
        ENV_PARALLEL,
        ENV_SILENCE_MIN_DURATION,
        ENV_SILENCE_THRESHOLD,
        get_default_auto_lut,
        get_default_bgm_loop,
        get_default_denoise,
        get_default_denoise_level,
        get_default_fade_duration,
        get_default_group_sequences,
        get_default_normalize_audio,
        get_default_stabilize,
        get_default_stabilize_crop,
        get_default_stabilize_strength,
        get_default_subtitle_format,
        get_default_subtitle_model,
    )

    state = TuiOptionState()

    # --- General ---
    env_output = os.environ.get(ENV_OUTPUT_DIR)
    if env_output:
        state.output_dir = env_output
    elif config.general.output_dir is not None:
        state.output_dir = config.general.output_dir

    env_parallel = os.environ.get(ENV_PARALLEL)
    if env_parallel:
        with contextlib.suppress(ValueError):
            state.parallel = int(env_parallel)
    elif config.general.parallel is not None:
        state.parallel = config.general.parallel

    # bool/enum 필드: get_default_*() 가 ENV > config 이미 병합
    state.denoise = get_default_denoise()
    state.normalize_audio = get_default_normalize_audio()
    state.stabilize = get_default_stabilize()
    state.group_sequences = get_default_group_sequences()
    state.fade_duration = get_default_fade_duration()
    state.auto_lut = get_default_auto_lut()
    state.bgm_loop = get_default_bgm_loop()

    level = get_default_denoise_level()
    if level:
        state.denoise_level = level

    strength = get_default_stabilize_strength()
    if strength:
        state.stabilize_strength = strength

    crop = get_default_stabilize_crop()
    if crop:
        state.stabilize_crop = crop

    # --- Silence ---
    env_threshold = os.environ.get(ENV_SILENCE_THRESHOLD)
    if env_threshold:
        state.silence_threshold = env_threshold.strip()
    elif config.general.silence_threshold is not None:
        state.silence_threshold = config.general.silence_threshold

    env_min_dur = os.environ.get(ENV_SILENCE_MIN_DURATION)
    if env_min_dur:
        with contextlib.suppress(ValueError):
            state.silence_min_duration = float(env_min_dur)
    elif config.general.silence_min_duration is not None:
        state.silence_min_duration = config.general.silence_min_duration

    if config.general.trim_silence is not None:
        state.trim_silence = config.general.trim_silence

    # --- Subtitle ---
    sub_model = get_default_subtitle_model()
    if sub_model:
        state.subtitle_model = sub_model

    sub_fmt = get_default_subtitle_format()
    if sub_fmt:
        state.subtitle_format = sub_fmt

    if config.general.subtitle_burn is not None:
        state.subtitle_burn = config.general.subtitle_burn

    # --- YouTube ---
    if config.youtube.upload_privacy is not None:
        state.upload_privacy = config.youtube.upload_privacy
    if config.youtube.playlist:
        state.upload_playlists = list(config.youtube.playlist)

    # --- BGM ---
    env_bgm = os.environ.get(ENV_BGM_PATH)
    if env_bgm:
        state.bgm_path = env_bgm.strip()
    elif config.bgm.bgm_path is not None:
        state.bgm_path = config.bgm.bgm_path

    env_vol = os.environ.get(ENV_BGM_VOLUME)
    if env_vol:
        with contextlib.suppress(ValueError):
            state.bgm_volume = float(env_vol)
    elif config.bgm.bgm_volume is not None:
        state.bgm_volume = config.bgm.bgm_volume

    return state


def state_to_dict(state: TuiOptionState) -> dict[str, Any]:
    """TuiOptionState를 필드명→값 딕셔너리로 변환한다."""
    return {f.name: getattr(state, f.name) for f in dataclasses.fields(state)}


def state_from_dict(d: dict[str, Any]) -> TuiOptionState:
    """딕셔너리에서 TuiOptionState를 복원한다. 알 수 없는 키는 무시한다."""
    known = {f.name for f in dataclasses.fields(TuiOptionState)}
    kwargs = {k: v for k, v in d.items() if k in known}
    return TuiOptionState(**kwargs)


# ---------------------------------------------------------------------------
# 프리셋 저장/불러오기
# ---------------------------------------------------------------------------

_PRESETS_DIR = Path("~/.tubearchive/presets").expanduser()
_PRESET_NAME_RE = re.compile(r"[^\w가-힣\- ]+")


def _preset_path(name: str) -> Path:
    safe = _PRESET_NAME_RE.sub("_", name).strip("_").strip()
    if not safe:
        safe = "preset"
    return _PRESETS_DIR / f"{safe}.json"


def save_preset(name: str, state: TuiOptionState, presets_dir: Path | None = None) -> Path:
    """TuiOptionState를 프리셋 JSON 파일로 저장한다."""
    root = presets_dir or _PRESETS_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = _preset_path(name) if presets_dir is None else root / f"{name}.json"
    payload = {
        "name": name,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "options": state_to_dict(state),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_preset(path: Path) -> TuiOptionState:
    """프리셋 JSON 파일에서 TuiOptionState를 복원한다."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return state_from_dict(payload["options"])


def save_state_as_defaults(state: TuiOptionState, path: Path | None = None) -> Path:
    """TuiOptionState를 config.toml 기본값으로 저장한다.

    기존 config.toml의 주석/구조를 tomlkit round-trip으로 보존하면서
    TuiOptionState의 각 필드를 해당 섹션에 기록한다.
    파일이 없으면 generate_default_config() 템플릿으로 신규 생성한다.
    임시 파일 → os.replace() atomic write로 부분 쓰기를 방지한다.

    저장 대상 (run-time 전용 플래그 제외):
    - [general]: parallel, denoise, denoise_level, normalize_audio, group_sequences,
                 fade_duration, trim_silence, silence_threshold, silence_min_duration,
                 stabilize, stabilize_strength, stabilize_crop,
                 subtitle_model, subtitle_format, subtitle_burn, output_dir(비어있으면 제거)
    - [bgm]: bgm_path(비어있으면 제거), bgm_volume, bgm_loop
    - [color_grading]: auto_lut
    - [youtube]: upload_privacy
    """
    from tubearchive.config import generate_default_config, get_default_config_path

    target = path or get_default_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.exists():
        doc = tomlkit.parse(target.read_text(encoding="utf-8"))
    else:
        doc = tomlkit.parse(generate_default_config())

    # [general]
    if "general" not in doc:
        doc.add("general", tomlkit.table())
    gen = doc["general"]

    if state.output_dir:
        gen["output_dir"] = state.output_dir  # type: ignore[index]
    elif "output_dir" in gen:  # type: ignore[operator]
        del gen["output_dir"]  # type: ignore[union-attr]

    gen["parallel"] = state.parallel  # type: ignore[index]
    gen["denoise"] = state.denoise  # type: ignore[index]
    gen["denoise_level"] = state.denoise_level  # type: ignore[index]
    gen["normalize_audio"] = state.normalize_audio  # type: ignore[index]
    gen["group_sequences"] = state.group_sequences  # type: ignore[index]
    gen["fade_duration"] = state.fade_duration  # type: ignore[index]
    gen["trim_silence"] = state.trim_silence  # type: ignore[index]

    if state.silence_threshold:
        gen["silence_threshold"] = state.silence_threshold  # type: ignore[index]
    elif "silence_threshold" in gen:  # type: ignore[operator]
        del gen["silence_threshold"]  # type: ignore[union-attr]

    gen["silence_min_duration"] = state.silence_min_duration  # type: ignore[index]
    gen["stabilize"] = state.stabilize  # type: ignore[index]
    gen["stabilize_strength"] = state.stabilize_strength  # type: ignore[index]
    gen["stabilize_crop"] = state.stabilize_crop  # type: ignore[index]
    gen["subtitle_model"] = state.subtitle_model  # type: ignore[index]
    gen["subtitle_format"] = state.subtitle_format  # type: ignore[index]
    gen["subtitle_burn"] = state.subtitle_burn  # type: ignore[index]

    # [bgm]
    if "bgm" not in doc:
        doc.add("bgm", tomlkit.table())
    bgm = doc["bgm"]

    if state.bgm_path:
        bgm["bgm_path"] = state.bgm_path  # type: ignore[index]
    elif "bgm_path" in bgm:  # type: ignore[operator]
        del bgm["bgm_path"]  # type: ignore[union-attr]

    bgm["bgm_volume"] = state.bgm_volume  # type: ignore[index]
    bgm["bgm_loop"] = state.bgm_loop  # type: ignore[index]

    # [color_grading]
    if "color_grading" not in doc:
        doc.add("color_grading", tomlkit.table())
    cg = doc["color_grading"]
    cg["auto_lut"] = state.auto_lut  # type: ignore[index]

    # [youtube]
    if "youtube" not in doc:
        doc.add("youtube", tomlkit.table())
    yt = doc["youtube"]
    yt["upload_privacy"] = state.upload_privacy  # type: ignore[index]

    # atomic write
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=".config_", suffix=".toml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(tomlkit.dumps(doc))
        Path(tmp_name).replace(target)
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp_name).unlink()
        raise

    return target


def list_presets(presets_dir: Path | None = None) -> list[tuple[str, Path]]:
    """(이름, 경로) 목록을 최근 저장 순으로 반환한다."""
    root = presets_dir or _PRESETS_DIR
    if not root.exists():
        return []
    items: list[tuple[str, Path]] = []
    for f in sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            items.append((data.get("name", f.stem), f))
        except Exception:  # noqa: S112
            continue
    return items
