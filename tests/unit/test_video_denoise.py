"""영상 노이즈 제거 필터 단위 테스트."""

from __future__ import annotations

import pytest

from tubearchive.infra.ffmpeg.effects import create_combined_filter, create_video_denoise_filter


class TestCreateVideoDenoiseFilter:
    """create_video_denoise_filter() 단위 테스트."""

    def test_light_returns_correct_filter(self) -> None:
        """light 강도 필터 문자열 반환."""
        result = create_video_denoise_filter("light")
        assert result == "hqdn3d=2:1.5:6:4.5"

    def test_medium_returns_correct_filter(self) -> None:
        """medium 강도 필터 문자열 반환."""
        result = create_video_denoise_filter("medium")
        assert result == "hqdn3d=4:3:9:6.75"

    def test_heavy_returns_correct_filter(self) -> None:
        """heavy 강도 필터 문자열 반환."""
        result = create_video_denoise_filter("heavy")
        assert result == "hqdn3d=6:4.5:12:9"

    def test_default_is_medium(self) -> None:
        """기본값은 medium."""
        assert create_video_denoise_filter() == create_video_denoise_filter("medium")

    def test_case_insensitive(self) -> None:
        """대소문자 무시."""
        assert create_video_denoise_filter("LIGHT") == create_video_denoise_filter("light")

    def test_invalid_level_raises_value_error(self) -> None:
        """잘못된 강도 → ValueError."""
        with pytest.raises(ValueError, match="Unsupported video denoise level"):
            create_video_denoise_filter("ultra")


class TestVideoDenoisePlacementInChain:
    """create_combined_filter()에서 hqdn3d 위치 검증."""

    def test_hqdn3d_present_when_video_denoise_enabled(self) -> None:
        """video_denoise=True 시 hqdn3d 필터 포함."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            video_denoise=True,
        )
        assert "hqdn3d" in video_filter

    def test_hqdn3d_absent_when_video_denoise_disabled(self) -> None:
        """video_denoise=False 시 hqdn3d 필터 없음."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            video_denoise=False,
        )
        assert "hqdn3d" not in video_filter

    def test_hqdn3d_before_hdr_in_landscape(self) -> None:
        """가로 영상: hqdn3d가 colorspace(HDR 변환) 앞에 위치."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            color_transfer="arib-std-b67",  # HDR 소스
            video_denoise=True,
        )
        hqdn3d_pos = video_filter.index("hqdn3d")
        colorspace_pos = video_filter.index("colorspace")
        assert hqdn3d_pos < colorspace_pos

    def test_hqdn3d_before_hdr_in_portrait(self) -> None:
        """세로 영상: hqdn3d가 colorspace(HDR 변환) 앞에 위치."""
        video_filter, _ = create_combined_filter(
            source_width=1080,
            source_height=1920,
            total_duration=60.0,
            is_portrait=True,
            color_transfer="arib-std-b67",
            video_denoise=True,
        )
        hqdn3d_pos = video_filter.index("hqdn3d")
        colorspace_pos = video_filter.index("colorspace")
        assert hqdn3d_pos < colorspace_pos

    def test_hqdn3d_before_hdr_even_with_lut_before_hdr(self) -> None:
        """lut_before_hdr=True여도 hqdn3d는 HDR 변환 전."""
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(suffix=".cube", delete=False) as f:
            tmp_lut = f.name
        try:
            video_filter, _ = create_combined_filter(
                source_width=3840,
                source_height=2160,
                total_duration=60.0,
                is_portrait=False,
                color_transfer="arib-std-b67",
                video_denoise=True,
                lut_path=tmp_lut,
                lut_before_hdr=True,
            )
            hqdn3d_pos = video_filter.index("hqdn3d")
            lut3d_pos = video_filter.index("lut3d")
            colorspace_pos = video_filter.index("colorspace")
            # hqdn3d → lut3d → colorspace 순서
            assert hqdn3d_pos < lut3d_pos < colorspace_pos
        finally:
            Path(tmp_lut).unlink()

    def test_wb_present_when_wb_kelvin_set(self) -> None:
        """wb_kelvin 설정 시 colortemperature 필터 포함."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            wb_kelvin=5500,
        )
        assert "colortemperature=temperature=5500" in video_filter

    def test_wb_absent_when_wb_kelvin_none(self) -> None:
        """wb_kelvin=None 시 colortemperature 없음."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            wb_kelvin=None,
        )
        assert "colortemperature" not in video_filter

    def test_wb_after_hdr_in_landscape(self) -> None:
        """가로 영상: colortemperature가 colorspace(HDR 변환) 뒤에 위치."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            color_transfer="arib-std-b67",
            wb_kelvin=5500,
        )
        colorspace_pos = video_filter.index("colorspace")
        wb_pos = video_filter.index("colortemperature")
        assert colorspace_pos < wb_pos
