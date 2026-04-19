"""TUI 프리셋·모델 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest

from tubearchive.app.tui.models import (
    TuiOptionState,
    default_state,
    list_presets,
    load_preset,
    save_preset,
    state_from_config,
    state_from_dict,
    state_to_dict,
)

# ---------------------------------------------------------------------------
# state_to_dict / state_from_dict 왕복 변환
# ---------------------------------------------------------------------------


def test_state_roundtrip() -> None:
    """state_to_dict → state_from_dict 왕복 변환이 동일 값을 반환한다."""
    original = TuiOptionState(
        normalize_audio=True,
        watermark=True,
        watermark_text="2024.01.01 | Seoul",
        bgm_volume=0.5,
        parallel=4,
    )
    restored = state_from_dict(state_to_dict(original))
    assert restored.normalize_audio == original.normalize_audio
    assert restored.watermark_text == original.watermark_text
    assert restored.bgm_volume == original.bgm_volume
    assert restored.parallel == original.parallel


def test_state_from_dict_ignores_unknown_keys() -> None:
    """알 수 없는 키는 무시한다."""
    d = state_to_dict(default_state())
    d["nonexistent_field"] = "should be ignored"
    state = state_from_dict(d)
    assert isinstance(state, TuiOptionState)


# ---------------------------------------------------------------------------
# 프리셋 저장 / 불러오기 / 목록
# ---------------------------------------------------------------------------


def test_save_and_load_preset(tmp_path: Path) -> None:
    """저장 → 불러오기가 동일한 상태를 반환한다."""
    state = TuiOptionState(
        stabilize=True,
        watermark_text="Custom Text",
        bgm_volume=0.3,
    )
    path = save_preset("테스트 프리셋", state, presets_dir=tmp_path)

    assert path.exists()
    restored = load_preset(path)

    assert restored.stabilize is True
    assert restored.watermark_text == "Custom Text"
    assert restored.bgm_volume == pytest.approx(0.3)


def test_save_preset_creates_directory(tmp_path: Path) -> None:
    """프리셋 디렉토리가 없으면 자동 생성한다."""
    nested = tmp_path / "a" / "b" / "presets"
    state = default_state()
    path = save_preset("new", state, presets_dir=nested)
    assert nested.exists()
    assert path.exists()


def test_list_presets_empty(tmp_path: Path) -> None:
    """프리셋 없으면 빈 목록을 반환한다."""
    assert list_presets(presets_dir=tmp_path) == []


def test_list_presets_nonexistent_dir() -> None:
    """디렉토리 자체가 없으면 빈 목록을 반환한다."""
    assert list_presets(presets_dir=Path("/nonexistent/path/xyz")) == []


def test_list_presets_multiple(tmp_path: Path) -> None:
    """저장된 프리셋 목록을 최신 순으로 반환한다."""
    import time

    save_preset("첫 번째", default_state(), presets_dir=tmp_path)
    time.sleep(0.01)
    save_preset("두 번째", default_state(), presets_dir=tmp_path)

    items = list_presets(presets_dir=tmp_path)
    assert len(items) == 2
    # 최신 저장 순 (내림차순)
    assert items[0][0] == "두 번째"
    assert items[1][0] == "첫 번째"


def test_list_presets_skips_corrupt_json(tmp_path: Path) -> None:
    """손상된 JSON 파일은 건너뛴다."""
    bad = tmp_path / "bad.json"
    bad.write_text("{invalid json", encoding="utf-8")
    save_preset("good", default_state(), presets_dir=tmp_path)

    items = list_presets(presets_dir=tmp_path)
    assert len(items) == 1
    assert items[0][0] == "good"


# ---------------------------------------------------------------------------
# state_from_config — ENV > config.toml > 기본값 우선순위
# ---------------------------------------------------------------------------

# 테스트마다 apply_config_to_env()를 호출하므로 env 격리가 필요하다.
# monkeypatch가 테스트 종료 시 자동 rollback해 준다.

_CONFIG_ENV_KEYS = [
    "TUBEARCHIVE_OUTPUT_DIR",
    "TUBEARCHIVE_PARALLEL",
    "TUBEARCHIVE_DENOISE",
    "TUBEARCHIVE_DENOISE_LEVEL",
    "TUBEARCHIVE_NORMALIZE_AUDIO",
    "TUBEARCHIVE_GROUP_SEQUENCES",
    "TUBEARCHIVE_FADE_DURATION",
    "TUBEARCHIVE_STABILIZE",
    "TUBEARCHIVE_STABILIZE_STRENGTH",
    "TUBEARCHIVE_STABILIZE_CROP",
    "TUBEARCHIVE_TRIM_SILENCE",
    "TUBEARCHIVE_SILENCE_THRESHOLD",
    "TUBEARCHIVE_SILENCE_MIN_DURATION",
    "TUBEARCHIVE_BGM_PATH",
    "TUBEARCHIVE_BGM_VOLUME",
    "TUBEARCHIVE_BGM_LOOP",
    "TUBEARCHIVE_AUTO_LUT",
    "TUBEARCHIVE_SUBTITLE_MODEL",
    "TUBEARCHIVE_SUBTITLE_FORMAT",
]


@pytest.fixture()
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """config 관련 환경변수를 전부 제거해 테스트 간 격리한다."""
    for key in _CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_state_from_config_defaults(_clean_env: None) -> None:
    """빈 AppConfig + 빈 ENV → TuiOptionState 기본값."""
    from tubearchive.config import AppConfig, apply_config_to_env

    config = AppConfig()
    apply_config_to_env(config)
    state = state_from_config(config)

    expected = default_state()
    # normalize_audio 기본값은 get_default_normalize_audio()가 True를 반환
    assert state.normalize_audio is True
    assert state.stabilize == expected.stabilize
    assert state.bgm_volume == expected.bgm_volume


def test_state_from_config_applies_general(_clean_env: None) -> None:
    """config.toml 값이 TuiOptionState에 반영된다."""
    from tubearchive.config import AppConfig, GeneralConfig, apply_config_to_env

    config = AppConfig(
        general=GeneralConfig(
            normalize_audio=True,
            stabilize=True,
            stabilize_strength="heavy",
            parallel=4,
            fade_duration=1.0,
        )
    )
    apply_config_to_env(config)
    state = state_from_config(config)

    assert state.normalize_audio is True
    assert state.stabilize is True
    assert state.stabilize_strength == "heavy"
    assert state.parallel == 4
    assert state.fade_duration == pytest.approx(1.0)


def test_state_from_config_applies_bgm(_clean_env: None) -> None:
    """BGMConfig 값이 TuiOptionState에 반영된다."""
    from tubearchive.config import AppConfig, BGMConfig, apply_config_to_env

    config = AppConfig(bgm=BGMConfig(bgm_path="~/Music/bgm.mp3", bgm_volume=0.4, bgm_loop=True))
    apply_config_to_env(config)
    state = state_from_config(config)

    assert state.bgm_path == "~/Music/bgm.mp3"
    assert state.bgm_volume == pytest.approx(0.4)
    assert state.bgm_loop is True


def test_state_from_config_applies_color_grading(_clean_env: None) -> None:
    """ColorGradingConfig.auto_lut이 반영된다."""
    from tubearchive.config import AppConfig, ColorGradingConfig, apply_config_to_env

    config = AppConfig(color_grading=ColorGradingConfig(auto_lut=True))
    apply_config_to_env(config)
    state = state_from_config(config)
    assert state.auto_lut is True


def test_state_from_config_none_fields_keep_defaults(_clean_env: None) -> None:
    """None 필드는 기본값을 유지한다."""
    from tubearchive.config import AppConfig, GeneralConfig, apply_config_to_env

    config = AppConfig(general=GeneralConfig(stabilize=True))
    apply_config_to_env(config)
    state = state_from_config(config)

    assert state.stabilize is True
    # stabilize 외 나머지는 기본값
    assert state.parallel == default_state().parallel


# ---------------------------------------------------------------------------
# ENV 우선순위 테스트 — ENV > config.toml
# ---------------------------------------------------------------------------


def test_env_overrides_config(monkeypatch: pytest.MonkeyPatch, _clean_env: None) -> None:
    """환경변수가 config.toml 값을 덮어쓴다."""
    from tubearchive.config import AppConfig, GeneralConfig, apply_config_to_env

    # config.toml은 stabilize=False, env는 true
    monkeypatch.setenv("TUBEARCHIVE_STABILIZE", "true")
    config = AppConfig(general=GeneralConfig(stabilize=False))
    apply_config_to_env(config)
    state = state_from_config(config)

    assert state.stabilize is True  # ENV wins


def test_env_overrides_config_parallel(monkeypatch: pytest.MonkeyPatch, _clean_env: None) -> None:
    """환경변수 PARALLEL이 config.toml을 덮어쓴다."""
    from tubearchive.config import AppConfig, GeneralConfig, apply_config_to_env

    monkeypatch.setenv("TUBEARCHIVE_PARALLEL", "8")
    config = AppConfig(general=GeneralConfig(parallel=2))
    apply_config_to_env(config)
    state = state_from_config(config)

    assert state.parallel == 8


def test_env_overrides_config_bgm_volume(monkeypatch: pytest.MonkeyPatch, _clean_env: None) -> None:
    """환경변수 BGM_VOLUME이 config.toml을 덮어쓴다."""
    from tubearchive.config import AppConfig, BGMConfig, apply_config_to_env

    monkeypatch.setenv("TUBEARCHIVE_BGM_VOLUME", "0.7")
    config = AppConfig(bgm=BGMConfig(bgm_volume=0.3))
    apply_config_to_env(config)
    state = state_from_config(config)

    assert state.bgm_volume == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# TuiOptionState.watermark_text 필드
# ---------------------------------------------------------------------------


def test_watermark_text_default_empty() -> None:
    """기본 watermark_text는 빈 문자열이다."""
    state = default_state()
    assert state.watermark_text == ""


def test_watermark_text_persisted_in_preset(tmp_path: Path) -> None:
    """워터마크 텍스트가 프리셋에 저장·복원된다."""
    state = TuiOptionState(watermark_text="Seoul | 2024.06.15")
    save_preset("wm", state, presets_dir=tmp_path)
    items = list_presets(presets_dir=tmp_path)
    restored = load_preset(items[0][1])
    assert restored.watermark_text == "Seoul | 2024.06.15"
