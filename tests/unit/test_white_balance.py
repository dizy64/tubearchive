"""화이트밸런스 필터 단위 테스트."""

from __future__ import annotations

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
