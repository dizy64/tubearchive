"""영상 노이즈 제거 필터 단위 테스트."""

from __future__ import annotations

import pytest

from tubearchive.infra.ffmpeg.effects import create_video_denoise_filter


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
