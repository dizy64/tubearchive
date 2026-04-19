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
# state_from_config — config 파일 기반 기본값
# ---------------------------------------------------------------------------


def test_state_from_config_defaults() -> None:
    """기본 AppConfig(빈 설정)는 TuiOptionState 기본값과 같다."""
    from tubearchive.config import AppConfig

    config = AppConfig()
    state = state_from_config(config)

    expected = default_state()
    assert state.normalize_audio == expected.normalize_audio
    assert state.stabilize == expected.stabilize
    assert state.bgm_volume == expected.bgm_volume


def test_state_from_config_applies_general() -> None:
    """GeneralConfig 값이 TuiOptionState에 반영된다."""
    from tubearchive.config import AppConfig, GeneralConfig

    config = AppConfig(
        general=GeneralConfig(
            normalize_audio=True,
            stabilize=True,
            stabilize_strength="heavy",
            parallel=4,
            fade_duration=1.0,
        )
    )
    state = state_from_config(config)

    assert state.normalize_audio is True
    assert state.stabilize is True
    assert state.stabilize_strength == "heavy"
    assert state.parallel == 4
    assert state.fade_duration == pytest.approx(1.0)


def test_state_from_config_applies_bgm() -> None:
    """BGMConfig 값이 TuiOptionState에 반영된다."""
    from tubearchive.config import AppConfig, BGMConfig

    config = AppConfig(bgm=BGMConfig(bgm_path="~/Music/bgm.mp3", bgm_volume=0.4, bgm_loop=True))
    state = state_from_config(config)

    assert state.bgm_path == "~/Music/bgm.mp3"
    assert state.bgm_volume == pytest.approx(0.4)
    assert state.bgm_loop is True


def test_state_from_config_applies_color_grading() -> None:
    """ColorGradingConfig.auto_lut이 반영된다."""
    from tubearchive.config import AppConfig, ColorGradingConfig

    config = AppConfig(color_grading=ColorGradingConfig(auto_lut=True))
    state = state_from_config(config)
    assert state.auto_lut is True


def test_state_from_config_none_fields_keep_defaults() -> None:
    """None 필드는 TuiOptionState 기본값을 유지한다."""
    from tubearchive.config import AppConfig, GeneralConfig

    # normalize_audio만 지정, 나머지는 None
    config = AppConfig(general=GeneralConfig(normalize_audio=True))
    state = state_from_config(config)

    # normalize_audio만 변경됨
    assert state.normalize_audio is True
    # 나머지는 기본값
    assert state.stabilize == default_state().stabilize
    assert state.parallel == default_state().parallel


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
