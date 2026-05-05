"""화이트밸런스 필터 단위 테스트."""

from __future__ import annotations

import pytest

from tubearchive.infra.ffmpeg.effects import WB_PRESETS, create_wb_filter


class TestWbPresets:
    """WB_PRESETS 상수 테스트."""

    def test_presets_contain_required_keys(self) -> None:
        """필수 프리셋 키 존재 확인."""
        assert "tungsten" in WB_PRESETS
        assert "fluorescent" in WB_PRESETS
        assert "daylight" in WB_PRESETS
        assert "cloudy" in WB_PRESETS
        assert "shade" in WB_PRESETS

    def test_preset_values_are_kelvin_ints(self) -> None:
        """프리셋 값은 정수 Kelvin."""
        assert WB_PRESETS["daylight"] == 5500
        assert WB_PRESETS["cloudy"] == 6500

    def test_device_defaults_reference_valid_presets(self) -> None:
        """WB_DEVICE_DEFAULTS의 모든 값이 WB_PRESETS의 유효한 키인지 확인."""
        from tubearchive.infra.ffmpeg.effects import WB_DEVICE_DEFAULTS

        for device, preset in WB_DEVICE_DEFAULTS.items():
            assert preset in WB_PRESETS, f"{device!r}의 기본값 {preset!r}이 WB_PRESETS에 없음"


class TestCreateWbFilter:
    """create_wb_filter() 단위 테스트."""

    def test_returns_colortemperature_filter(self) -> None:
        """colortemperature 필터 문자열 반환."""
        result = create_wb_filter(5500)
        assert result == "colortemperature=temperature=5500"

    def test_different_kelvin_values(self) -> None:
        """다양한 Kelvin 값."""
        assert create_wb_filter(3200) == "colortemperature=temperature=3200"
        assert create_wb_filter(7500) == "colortemperature=temperature=7500"

    def test_invalid_kelvin_zero_raises(self) -> None:
        """0K → ValueError."""
        with pytest.raises(ValueError, match="Kelvin must be"):
            create_wb_filter(0)

    def test_boundary_kelvin_values(self) -> None:
        """경계값 1000K, 40000K."""
        assert "1000" in create_wb_filter(1000)
        assert "40000" in create_wb_filter(40_000)
