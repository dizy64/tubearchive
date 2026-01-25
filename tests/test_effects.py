"""FFmpeg 효과 테스트."""


from tubearchive.ffmpeg.effects import (
    create_combined_filter,
    create_dip_to_black_audio_filter,
    create_dip_to_black_video_filter,
    create_hdr_to_sdr_filter,
    create_portrait_layout_filter,
)


class TestPortraitLayoutFilter:
    """세로 영상 레이아웃 필터 테스트."""

    def test_creates_filter_with_correct_structure(self) -> None:
        """필터 구조 검증."""
        filter_str = create_portrait_layout_filter(
            source_width=1080,
            source_height=1920,
            target_width=3840,
            target_height=2160,
        )

        # 필수 요소 포함 확인
        assert "split=2" in filter_str  # 스트림 분할
        assert "boxblur" in filter_str  # 배경 블러
        assert "overlay" in filter_str  # 오버레이
        assert "scale=" in filter_str  # 스케일링

    def test_filter_contains_blur_parameters(self) -> None:
        """블러 파라미터 포함 확인."""
        filter_str = create_portrait_layout_filter(
            source_width=1080,
            source_height=1920,
            target_width=3840,
            target_height=2160,
        )

        # boxblur 설정 확인 (기본값: 20)
        assert "boxblur=20" in filter_str

    def test_filter_centers_foreground(self) -> None:
        """전경 중앙 정렬 확인."""
        filter_str = create_portrait_layout_filter(
            source_width=1080,
            source_height=1920,
            target_width=3840,
            target_height=2160,
        )

        # overlay 중앙 정렬 수식
        assert "(W-w)/2" in filter_str
        assert "(H-h)/2" in filter_str

    def test_custom_blur_radius(self) -> None:
        """커스텀 블러 반경."""
        filter_str = create_portrait_layout_filter(
            source_width=1080,
            source_height=1920,
            target_width=3840,
            target_height=2160,
            blur_radius=30,
        )

        assert "boxblur=30" in filter_str

    def test_target_resolution_in_scale(self) -> None:
        """타겟 해상도가 스케일에 적용되는지."""
        filter_str = create_portrait_layout_filter(
            source_width=1080,
            source_height=1920,
            target_width=3840,
            target_height=2160,
        )

        # 배경 스케일: 3840x2160
        assert "3840:2160" in filter_str


class TestDipToBlackVideoFilter:
    """Dip-to-Black 비디오 필터 테스트."""

    def test_creates_fade_in_and_out(self) -> None:
        """Fade In/Out 포함 확인."""
        filter_str = create_dip_to_black_video_filter(
            total_duration=120.0,
            fade_duration=0.5,
        )

        assert "fade=in" in filter_str or "fade=t=in" in filter_str
        assert "fade=out" in filter_str or "fade=t=out" in filter_str

    def test_fade_duration_applied(self) -> None:
        """Fade 지속 시간 적용."""
        filter_str = create_dip_to_black_video_filter(
            total_duration=120.0,
            fade_duration=0.5,
        )

        # fade duration 파라미터
        assert "d=0.5" in filter_str

    def test_fade_out_starts_at_correct_position(self) -> None:
        """Fade Out 시작 위치 (duration - fade)."""
        filter_str = create_dip_to_black_video_filter(
            total_duration=120.0,
            fade_duration=0.5,
        )

        # fade out 시작: 120 - 0.5 = 119.5
        assert "st=119.5" in filter_str

    def test_custom_fade_duration(self) -> None:
        """커스텀 fade 지속 시간."""
        filter_str = create_dip_to_black_video_filter(
            total_duration=60.0,
            fade_duration=1.0,
        )

        assert "d=1.0" in filter_str
        # fade out 시작: 60 - 1 = 59.0
        assert "st=59.0" in filter_str


class TestDipToBlackAudioFilter:
    """Dip-to-Black 오디오 필터 테스트."""

    def test_creates_afade_in_and_out(self) -> None:
        """Audio Fade In/Out 포함 확인."""
        filter_str = create_dip_to_black_audio_filter(
            total_duration=120.0,
            fade_duration=0.5,
        )

        assert "afade=t=in" in filter_str
        assert "afade=t=out" in filter_str

    def test_afade_duration_applied(self) -> None:
        """Audio Fade 지속 시간 적용."""
        filter_str = create_dip_to_black_audio_filter(
            total_duration=120.0,
            fade_duration=0.5,
        )

        assert "d=0.5" in filter_str

    def test_afade_out_starts_at_correct_position(self) -> None:
        """Audio Fade Out 시작 위치."""
        filter_str = create_dip_to_black_audio_filter(
            total_duration=120.0,
            fade_duration=0.5,
        )

        # afade out 시작: 120 - 0.5 = 119.5
        assert "st=119.5" in filter_str

    def test_matches_video_timing(self) -> None:
        """비디오 타이밍과 일치."""
        video_filter = create_dip_to_black_video_filter(60.0, 0.5)
        audio_filter = create_dip_to_black_audio_filter(60.0, 0.5)

        # 둘 다 같은 fade out 시작점 (59.5초)
        # 필터 형식: fade=t=out:st=59.5:d=0.5
        video_fadeout_st = video_filter.split("fade=t=out:st=")[1].split(":")[0]
        audio_fadeout_st = audio_filter.split("afade=t=out:st=")[1].split(":")[0]

        assert video_fadeout_st == audio_fadeout_st == "59.5"


class TestHdrToSdrFilter:
    """HDR→SDR 변환 필터 테스트."""

    def test_creates_colorspace_filter_for_hlg(self) -> None:
        """HLG HDR 변환 필터 생성."""
        filter_str = create_hdr_to_sdr_filter(color_transfer="arib-std-b67")

        assert "colorspace=" in filter_str
        assert "bt709" in filter_str

    def test_creates_colorspace_filter_for_pq(self) -> None:
        """PQ/HDR10 변환 필터 생성."""
        filter_str = create_hdr_to_sdr_filter(color_transfer="smpte2084")

        assert "colorspace=" in filter_str
        assert "bt709" in filter_str

    def test_returns_empty_for_sdr(self) -> None:
        """SDR 소스는 빈 필터 반환."""
        filter_str = create_hdr_to_sdr_filter(color_transfer="bt709")

        assert filter_str == ""

    def test_returns_empty_for_none(self) -> None:
        """메타데이터 없으면 빈 필터 반환."""
        filter_str = create_hdr_to_sdr_filter(color_transfer=None)

        assert filter_str == ""


class TestCombinedFilterWithHdr:
    """HDR 소스에 대한 결합 필터 테스트."""

    def test_includes_hdr_conversion_for_hlg_source(self) -> None:
        """HLG HDR 소스 시 변환 필터 포함."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            color_transfer="arib-std-b67",
        )

        assert "colorspace=" in video_filter
        assert "bt709" in video_filter

    def test_includes_hdr_conversion_for_pq_source(self) -> None:
        """PQ HDR 소스 시 변환 필터 포함."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            color_transfer="smpte2084",
        )

        assert "colorspace=" in video_filter
        assert "bt709" in video_filter

    def test_no_hdr_conversion_for_sdr_source(self) -> None:
        """SDR 소스는 변환 없음."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            color_transfer="bt709",
        )

        assert "colorspace=" not in video_filter

    def test_hdr_conversion_with_portrait_layout(self) -> None:
        """세로 영상 + HDR 변환 결합."""
        video_filter, _ = create_combined_filter(
            source_width=1080,
            source_height=1920,
            total_duration=60.0,
            is_portrait=True,
            color_transfer="arib-std-b67",
        )

        # 세로 레이아웃 필터와 HDR 변환 모두 포함
        assert "overlay" in video_filter
        assert "colorspace=" in video_filter
