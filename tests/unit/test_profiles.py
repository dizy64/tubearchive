"""인코딩 프로파일 테스트."""

from tubearchive.ffmpeg.profiles import (
    PROFILE_FALLBACK_LIBX265,
    PROFILE_HDR_HLG,
    PROFILE_HDR_PQ,
    PROFILE_SDR,
    get_fallback_profile,
    select_profile,
)


class TestEncodingProfile:
    """EncodingProfile 테스트."""

    def test_to_ffmpeg_args_basic(self) -> None:
        """기본 FFmpeg 인자 생성."""
        profile = PROFILE_SDR
        args = profile.to_ffmpeg_args()

        assert "-c:v" in args
        assert "hevc_videotoolbox" in args
        assert "-b:v" in args
        assert "50M" in args
        assert "-pix_fmt" in args
        assert "p010le" in args
        assert "-r" in args
        assert "30000/1001" in args
        assert "-c:a" in args
        assert "aac" in args
        assert "-b:a" in args
        assert "256k" in args

    def test_to_ffmpeg_args_sdr_color(self) -> None:
        """SDR 프로파일 컬러 정보."""
        profile = PROFILE_SDR
        args = profile.to_ffmpeg_args()

        assert "-color_primaries" in args
        assert "bt709" in args
        assert "-color_trc" in args
        assert "-colorspace" in args

    def test_to_ffmpeg_args_hdr_pq_color(self) -> None:
        """HDR PQ 프로파일 컬러 정보."""
        profile = PROFILE_HDR_PQ
        args = profile.to_ffmpeg_args()

        assert "-color_primaries" in args
        assert "bt2020" in args
        assert "-color_trc" in args
        assert "smpte2084" in args
        assert "-colorspace" in args
        assert "bt2020nc" in args

    def test_to_ffmpeg_args_hdr_hlg_color(self) -> None:
        """HDR HLG 프로파일 컬러 정보."""
        profile = PROFILE_HDR_HLG
        args = profile.to_ffmpeg_args()

        assert "-color_primaries" in args
        assert "bt2020" in args
        assert "-color_trc" in args
        assert "arib-std-b67" in args
        assert "-colorspace" in args
        assert "bt2020nc" in args

    def test_to_ffmpeg_args_with_extra_args(self) -> None:
        """추가 인자 포함."""
        profile = PROFILE_SDR
        args = profile.to_ffmpeg_args()

        assert "-tag:v" in args
        assert "hvc1" in args
        assert "-color_range" in args
        assert "tv" in args


class TestSelectProfile:
    """메타데이터 기반 프로파일 선택 테스트."""

    def test_hdr_hlg_detection(self) -> None:
        """HDR HLG 감지 (아이폰 HDR 등)."""
        profile = select_profile(
            color_transfer="arib-std-b67",
            color_space="bt2020nc",
        )

        assert profile == PROFILE_HDR_HLG
        assert profile.color_primaries == "bt2020"
        assert profile.color_transfer == "arib-std-b67"

    def test_hdr_pq_detection(self) -> None:
        """HDR PQ/HDR10 감지 (Nikon N-Log 등)."""
        profile = select_profile(
            color_transfer="smpte2084",
            color_space="bt2020nc",
        )

        assert profile == PROFILE_HDR_PQ
        assert profile.color_primaries == "bt2020"
        assert profile.color_transfer == "smpte2084"

    def test_bt2020_without_transfer_uses_hlg(self) -> None:
        """BT.2020 색공간이지만 transfer 불명확 시 HLG 사용."""
        profile = select_profile(
            color_transfer=None,
            color_space="bt2020nc",
        )

        assert profile == PROFILE_HDR_HLG

    def test_bt2020c_uses_hlg(self) -> None:
        """BT.2020 constant luminance도 HLG 사용."""
        profile = select_profile(
            color_transfer=None,
            color_space="bt2020c",
        )

        assert profile == PROFILE_HDR_HLG

    def test_sdr_bt709(self) -> None:
        """SDR BT.709 감지."""
        profile = select_profile(
            color_transfer="bt709",
            color_space="bt709",
        )

        assert profile == PROFILE_SDR
        assert profile.color_primaries == "bt709"

    def test_sdr_default_no_metadata(self) -> None:
        """메타데이터 없으면 SDR 기본값."""
        profile = select_profile(
            color_transfer=None,
            color_space=None,
        )

        assert profile == PROFILE_SDR

    def test_sdr_unknown_transfer(self) -> None:
        """알 수 없는 transfer는 SDR."""
        profile = select_profile(
            color_transfer="smpte170m",
            color_space="bt709",
        )

        assert profile == PROFILE_SDR

    def test_hlg_transfer_priority_over_colorspace(self) -> None:
        """HLG transfer는 colorspace보다 우선."""
        profile = select_profile(
            color_transfer="arib-std-b67",
            color_space="bt709",  # 잘못된 조합이지만 transfer 우선
        )

        assert profile == PROFILE_HDR_HLG

    def test_pq_transfer_priority_over_colorspace(self) -> None:
        """PQ transfer는 colorspace보다 우선."""
        profile = select_profile(
            color_transfer="smpte2084",
            color_space="bt709",  # 잘못된 조합이지만 transfer 우선
        )

        assert profile == PROFILE_HDR_PQ


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

    def test_fallback_10bit(self) -> None:
        """폴백도 10-bit."""
        profile = get_fallback_profile()

        assert profile.pixel_format == "yuv420p10le"
