"""인코딩 프로파일 테스트."""

import pytest

from tubearchive.ffmpeg.profiles import (
    PROFILE_4K_HEVC_VT,
    PROFILE_DJI,
    PROFILE_FALLBACK_LIBX265,
    PROFILE_GOPRO,
    PROFILE_IPHONE,
    PROFILE_NIKON_NLOG,
    EncodingProfile,
    get_fallback_profile,
    select_profile,
)


class TestEncodingProfile:
    """EncodingProfile 테스트."""

    def test_to_ffmpeg_args_basic(self) -> None:
        """기본 FFmpeg 인자 생성."""
        profile = PROFILE_4K_HEVC_VT
        args = profile.to_ffmpeg_args()

        assert "-c:v" in args
        assert "hevc_videotoolbox" in args
        assert "-b:v" in args
        assert "50M" in args
        assert "-pix_fmt" in args
        assert "p010le" in args
        assert "-c:a" in args
        assert "aac" in args
        assert "-b:a" in args
        assert "256k" in args

    def test_to_ffmpeg_args_with_color(self) -> None:
        """컬러 정보 포함 FFmpeg 인자."""
        profile = PROFILE_NIKON_NLOG
        args = profile.to_ffmpeg_args()

        assert "-color_primaries" in args
        assert "bt2020" in args
        assert "-color_trc" in args
        assert "smpte2084" in args
        assert "-colorspace" in args
        assert "bt2020nc" in args

    def test_to_ffmpeg_args_with_extra_args(self) -> None:
        """추가 인자 포함."""
        profile = PROFILE_4K_HEVC_VT
        args = profile.to_ffmpeg_args()

        assert "-tag:v" in args
        assert "hvc1" in args


class TestSelectProfile:
    """프로파일 선택 테스트."""

    def test_nikon_nlog_with_hdr(self) -> None:
        """Nikon N-Log HDR 감지."""
        profile = select_profile(
            device_model="NIKON Z 8",
            color_transfer="smpte2084",
            color_space="bt2020nc",
        )

        assert profile == PROFILE_NIKON_NLOG
        assert profile.color_primaries == "bt2020"

    def test_nikon_without_nlog(self) -> None:
        """Nikon SDR (N-Log 아님)."""
        profile = select_profile(
            device_model="NIKON Z 8",
            color_transfer=None,
            color_space=None,
        )

        assert profile == PROFILE_4K_HEVC_VT

    def test_iphone_detection(self) -> None:
        """iPhone 감지."""
        profile = select_profile(
            device_model="iPhone 14 Pro",
            color_transfer=None,
            color_space=None,
        )

        assert profile == PROFILE_IPHONE

    def test_gopro_detection(self) -> None:
        """GoPro 감지."""
        profile = select_profile(
            device_model="GoPro HERO12",
            color_transfer=None,
            color_space=None,
        )

        assert profile == PROFILE_GOPRO

    def test_dji_detection(self) -> None:
        """DJI 감지."""
        profile = select_profile(
            device_model="DJI Mini 4 Pro",
            color_transfer=None,
            color_space=None,
        )

        assert profile == PROFILE_DJI

    def test_unknown_device_default(self) -> None:
        """알 수 없는 기기는 기본 프로파일."""
        profile = select_profile(
            device_model="Unknown Camera",
            color_transfer=None,
            color_space=None,
        )

        assert profile == PROFILE_4K_HEVC_VT

    def test_none_device_default(self) -> None:
        """기기 정보 없으면 기본 프로파일."""
        profile = select_profile(
            device_model=None,
            color_transfer=None,
            color_space=None,
        )

        assert profile == PROFILE_4K_HEVC_VT

    def test_case_insensitive_detection(self) -> None:
        """대소문자 구분 없이 감지."""
        profile_lower = select_profile("iphone 15", None, None)
        profile_upper = select_profile("IPHONE 15", None, None)
        profile_mixed = select_profile("iPhone 15", None, None)

        assert profile_lower == profile_upper == profile_mixed == PROFILE_IPHONE


class TestFallbackProfile:
    """폴백 프로파일 테스트."""

    def test_fallback_uses_libx265(self) -> None:
        """폴백은 libx265 사용."""
        profile = get_fallback_profile()

        assert profile == PROFILE_FALLBACK_LIBX265
        assert profile.video_codec == "libx265"

    def test_fallback_has_preset(self) -> None:
        """폴백에 프리셋 설정."""
        profile = get_fallback_profile()
        args = profile.to_ffmpeg_args()

        assert "-preset" in args
        assert "medium" in args
