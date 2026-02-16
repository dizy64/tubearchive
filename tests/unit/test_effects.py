"""FFmpeg 효과 테스트."""

from pathlib import Path

import pytest

from tubearchive.ffmpeg.effects import (
    TIMELAPSE_MAX_SPEED,
    TIMELAPSE_MIN_SPEED,
    LoudnormAnalysis,
    StabilizeCrop,
    StabilizeStrength,
    _calculate_fade_params,
    create_audio_filter_chain,
    create_combined_filter,
    create_denoise_audio_filter,
    create_dip_to_black_audio_filter,
    create_dip_to_black_video_filter,
    create_hdr_to_sdr_filter,
    create_loudnorm_analysis_filter,
    create_loudnorm_filter,
    create_portrait_layout_filter,
    create_silence_detect_filter,
    create_silence_remove_filter,
    create_timelapse_audio_filter,
    create_timelapse_video_filter,
    create_vidstab_detect_filter,
    create_vidstab_transform_filter,
    create_watermark_filter,
    parse_loudnorm_stats,
    parse_silence_segments,
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
            fade_in_duration=0.5,
            fade_out_duration=0.5,
        )

        assert "fade=in" in filter_str or "fade=t=in" in filter_str
        assert "fade=out" in filter_str or "fade=t=out" in filter_str

    def test_fade_duration_applied(self) -> None:
        """Fade 지속 시간 적용."""
        filter_str = create_dip_to_black_video_filter(
            total_duration=120.0,
            fade_in_duration=0.5,
            fade_out_duration=0.5,
        )

        # fade duration 파라미터
        assert "d=0.5" in filter_str

    def test_fade_out_starts_at_correct_position(self) -> None:
        """Fade Out 시작 위치 (duration - fade)."""
        filter_str = create_dip_to_black_video_filter(
            total_duration=120.0,
            fade_in_duration=0.5,
            fade_out_duration=0.5,
        )

        # fade out 시작: 120 - 0.5 = 119.5
        assert "st=119.5" in filter_str

    def test_custom_fade_duration(self) -> None:
        """커스텀 fade 지속 시간."""
        filter_str = create_dip_to_black_video_filter(
            total_duration=60.0,
            fade_in_duration=1.0,
            fade_out_duration=1.0,
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
            fade_in_duration=0.5,
            fade_out_duration=0.5,
        )

        assert "afade=t=in" in filter_str
        assert "afade=t=out" in filter_str

    def test_afade_duration_applied(self) -> None:
        """Audio Fade 지속 시간 적용."""
        filter_str = create_dip_to_black_audio_filter(
            total_duration=120.0,
            fade_in_duration=0.5,
            fade_out_duration=0.5,
        )

        assert "d=0.5" in filter_str

    def test_afade_out_starts_at_correct_position(self) -> None:
        """Audio Fade Out 시작 위치."""
        filter_str = create_dip_to_black_audio_filter(
            total_duration=120.0,
            fade_in_duration=0.5,
            fade_out_duration=0.5,
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


class TestDenoiseAudioFilter:
    """오디오 노이즈 제거 필터 테스트."""

    def test_creates_afftdn_with_level(self) -> None:
        """강도별 afftdn 파라미터."""
        assert create_denoise_audio_filter("light") == "afftdn=nr=6"
        assert create_denoise_audio_filter("medium") == "afftdn=nr=12"
        assert create_denoise_audio_filter("heavy") == "afftdn=nr=18"

    def test_audio_chain_orders_denoise_before_fade(self) -> None:
        """denoise -> fade 순서 확인."""
        chain = create_audio_filter_chain(
            total_duration=60.0,
            fade_duration=0.5,
            denoise=True,
            denoise_level="medium",
        )

        assert chain.startswith("afftdn=nr=12")
        assert "afade=t=in" in chain


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


class TestShortVideoDurationHandling:
    """짧은 영상 duration 처리 테스트 (GoPro SOS 등)."""

    def test_calculate_fade_params_normal_duration(self) -> None:
        """일반 영상: 정상 fade 적용."""
        effective_in, effective_out, fade_out_start = _calculate_fade_params(120.0, 0.5, 0.5)

        assert effective_in == 0.5
        assert effective_out == 0.5
        assert fade_out_start == 119.5

    def test_calculate_fade_params_short_video(self) -> None:
        """짧은 영상 (0.5초 미만): fade 축소."""
        # 0.3초 영상 → fade를 0.15초로 축소
        effective_in, effective_out, fade_out_start = _calculate_fade_params(0.3, 0.5, 0.5)

        assert effective_in == 0.15
        assert effective_out == 0.15
        assert fade_out_start == 0.15  # 0.3 - 0.15
        assert fade_out_start >= 0  # 음수 방지

    def test_calculate_fade_params_very_short_video(self) -> None:
        """매우 짧은 영상 (0.1초 미만): fade 생략."""
        effective_in, effective_out, fade_out_start = _calculate_fade_params(0.05, 0.5, 0.5)

        assert effective_in == 0.0
        assert effective_out == 0.0
        assert fade_out_start == 0.0

    def test_calculate_fade_params_gopro_sos_case(self) -> None:
        """GoPro SOS 파일 케이스: ~0.17초 영상."""
        # 실제 에러 케이스: duration=0.166833
        effective_in, effective_out, fade_out_start = _calculate_fade_params(0.166833, 0.5, 0.5)

        assert effective_in > 0  # fade 적용됨
        assert effective_out > 0
        assert fade_out_start >= 0  # 음수 아님
        # 비례 축소: 0.166833 / 2 ≈ 0.083
        assert abs(effective_in - 0.0834165) < 0.0001

    def test_video_filter_short_duration_no_negative_st(self) -> None:
        """짧은 영상에서 음수 st 값 방지."""
        # 0.2초 영상
        filter_str = create_dip_to_black_video_filter(0.2, 0.5, 0.5)

        # st=-X 패턴이 없어야 함
        assert "st=-" not in filter_str
        # st=0 이상 값만 존재
        if filter_str:  # 빈 문자열이 아니면
            assert "st=0" in filter_str or "st=0.1" in filter_str

    def test_audio_filter_short_duration_no_negative_st(self) -> None:
        """짧은 오디오에서 음수 st 값 방지."""
        filter_str = create_dip_to_black_audio_filter(0.2, 0.5, 0.5)

        assert "st=-" not in filter_str

    def test_combined_filter_short_video_no_crash(self) -> None:
        """짧은 영상에서 combined filter 생성 가능."""
        video_filter, audio_filter = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=0.17,  # GoPro SOS 케이스
            is_portrait=False,
        )

        # 음수 st 값 없음
        assert "st=-" not in video_filter
        assert "st=-" not in audio_filter
        # 필터가 유효함
        assert "[v_out]" in video_filter

    def test_combined_filter_very_short_video_no_fade(self) -> None:
        """매우 짧은 영상: fade 생략, scale만 적용."""
        video_filter, audio_filter = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=0.05,  # 50ms
            is_portrait=False,
        )

        # scale은 적용됨
        assert "scale=" in video_filter
        # fade는 생략됨
        assert "fade=" not in video_filter
        # audio도 fade 생략
        assert audio_filter == ""

    def test_combined_filter_portrait_short_video(self) -> None:
        """짧은 세로 영상: portrait 레이아웃 유지, fade 축소."""
        video_filter, _audio_filter = create_combined_filter(
            source_width=1080,
            source_height=1920,
            total_duration=0.3,  # 짧지만 fade 가능
            is_portrait=True,
        )

        # portrait 레이아웃 유지
        assert "overlay" in video_filter
        assert "boxblur" in video_filter
        # 음수 없음
        assert "st=-" not in video_filter


class TestVidstabDetectFilter:
    """vidstab 분석 패스 필터 테스트."""

    def test_light_strength(self) -> None:
        """light 강도: shakiness=4, accuracy=9."""
        result = create_vidstab_detect_filter(
            strength=StabilizeStrength.LIGHT,
            trf_path="transforms/stab.trf",
        )
        assert "shakiness=4" in result
        assert "accuracy=9" in result

    def test_medium_strength(self) -> None:
        """medium 강도: shakiness=6, accuracy=12."""
        result = create_vidstab_detect_filter(
            strength=StabilizeStrength.MEDIUM,
            trf_path="transforms/stab.trf",
        )
        assert "shakiness=6" in result
        assert "accuracy=12" in result

    def test_heavy_strength(self) -> None:
        """heavy 강도: shakiness=8, accuracy=15."""
        result = create_vidstab_detect_filter(
            strength=StabilizeStrength.HEAVY,
            trf_path="transforms/stab.trf",
        )
        assert "shakiness=8" in result
        assert "accuracy=15" in result

    def test_includes_trf_path(self) -> None:
        """result= 경로와 fileformat=ascii 포함."""
        result = create_vidstab_detect_filter(
            trf_path="transforms/my_video.trf",
        )
        assert "result=transforms/my_video.trf" in result
        assert "fileformat=ascii" in result

    def test_can_disable_fileformat(self) -> None:
        """옵션 비활성화 시 fileformat=ascii 미포함."""
        result = create_vidstab_detect_filter(
            trf_path="transforms/my_video.trf",
            include_fileformat=False,
        )
        assert "result=transforms/my_video.trf" in result
        assert "fileformat=ascii" not in result


class TestVidstabTransformFilter:
    """vidstab 변환 필터 테스트."""

    def test_light_smoothing(self) -> None:
        """light 강도: smoothing=10."""
        result = create_vidstab_transform_filter(
            strength=StabilizeStrength.LIGHT,
            trf_path="transforms/stab.trf",
        )
        assert "smoothing=10" in result

    def test_medium_smoothing(self) -> None:
        """medium 강도: smoothing=15."""
        result = create_vidstab_transform_filter(
            strength=StabilizeStrength.MEDIUM,
            trf_path="transforms/stab.trf",
        )
        assert "smoothing=15" in result

    def test_heavy_smoothing(self) -> None:
        """heavy 강도: smoothing=30."""
        result = create_vidstab_transform_filter(
            strength=StabilizeStrength.HEAVY,
            trf_path="transforms/stab.trf",
        )
        assert "smoothing=30" in result

    def test_crop_mode(self) -> None:
        """crop 모드: crop=keep."""
        result = create_vidstab_transform_filter(
            crop=StabilizeCrop.CROP,
            trf_path="transforms/stab.trf",
        )
        assert "crop=keep" in result

    def test_expand_mode(self) -> None:
        """expand 모드: crop=black."""
        result = create_vidstab_transform_filter(
            crop=StabilizeCrop.EXPAND,
            trf_path="transforms/stab.trf",
        )
        assert "crop=black" in result

    def test_includes_trf_path(self) -> None:
        """input= 경로 포함."""
        result = create_vidstab_transform_filter(
            trf_path="transforms/my_video.trf",
        )
        assert "input=transforms/my_video.trf" in result


class TestCombinedFilterWithStabilize:
    """안정화 필터 통합 테스트."""

    def test_landscape_with_stabilize(self) -> None:
        """가로 영상: vidstabtransform이 scale/pad 전에 위치."""
        stab_filter = "vidstabtransform=input=stab.trf:smoothing=15:crop=keep"
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            stabilize_filter=stab_filter,
        )
        assert "vidstabtransform" in video_filter
        # vidstabtransform이 scale 앞에 위치해야 함
        stab_pos = video_filter.index("vidstabtransform")
        scale_pos = video_filter.index("scale=")
        assert stab_pos < scale_pos

    def test_portrait_with_stabilize(self) -> None:
        """세로 영상: vidstabtransform이 split 전에 위치."""
        stab_filter = "vidstabtransform=input=stab.trf:smoothing=15:crop=keep"
        video_filter, _ = create_combined_filter(
            source_width=1080,
            source_height=1920,
            total_duration=60.0,
            is_portrait=True,
            stabilize_filter=stab_filter,
        )
        assert "vidstabtransform" in video_filter
        # vidstabtransform이 split 앞에 위치해야 함
        stab_pos = video_filter.index("vidstabtransform")
        split_pos = video_filter.index("split=2")
        assert stab_pos < split_pos

    def test_landscape_without_stabilize(self) -> None:
        """가로 영상: stabilize_filter="" 이면 vidstabtransform 미포함."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            stabilize_filter="",
        )
        assert "vidstabtransform" not in video_filter

    def test_portrait_without_stabilize(self) -> None:
        """세로 영상: stabilize_filter="" 이면 vidstabtransform 미포함."""
        video_filter, _ = create_combined_filter(
            source_width=1080,
            source_height=1920,
            total_duration=60.0,
            is_portrait=True,
            stabilize_filter="",
        )
        assert "vidstabtransform" not in video_filter


class TestLoudnormAnalysisFilter:
    """create_loudnorm_analysis_filter 테스트."""

    def test_default_parameters(self) -> None:
        """기본 파라미터: I=-14, TP=-1.5, LRA=11, print_format=json."""
        result = create_loudnorm_analysis_filter()
        assert "loudnorm" in result
        assert "I=-14" in result
        assert "TP=-1.5" in result
        assert "LRA=11" in result
        assert "print_format=json" in result

    def test_custom_parameters(self) -> None:
        """커스텀 파라미터 전달."""
        result = create_loudnorm_analysis_filter(target_i=-16.0, target_tp=-2.0, target_lra=7.0)
        assert "I=-16" in result
        assert "TP=-2" in result
        assert "LRA=7" in result


class TestLoudnormFilter:
    """create_loudnorm_filter 2nd pass 테스트."""

    def test_includes_measured_values(self) -> None:
        """measured_I/TP/LRA/thresh, offset, linear=true 포함."""
        analysis = LoudnormAnalysis(
            input_i=-23.5,
            input_tp=-7.2,
            input_lra=14.3,
            input_thresh=-34.5,
            target_offset=0.5,
        )
        result = create_loudnorm_filter(analysis)
        assert "measured_I=-23.5" in result
        assert "measured_TP=-7.2" in result
        assert "measured_LRA=14.3" in result
        assert "measured_thresh=-34.5" in result
        assert "offset=0.5" in result
        assert "linear=true" in result

    def test_default_targets(self) -> None:
        """기본 타겟: I=-14, TP=-1.5, LRA=11."""
        analysis = LoudnormAnalysis(
            input_i=-20.0,
            input_tp=-5.0,
            input_lra=10.0,
            input_thresh=-30.0,
            target_offset=0.0,
        )
        result = create_loudnorm_filter(analysis)
        assert "I=-14" in result
        assert "TP=-1.5" in result
        assert "LRA=11" in result


class TestParseLoudnormStats:
    """parse_loudnorm_stats 테스트."""

    def test_parse_valid_json(self) -> None:
        """정상 loudnorm JSON 파싱."""
        output = (
            "[Parsed_loudnorm_0 @ 0x...]\n"
            "{\n"
            '\t"input_i" : "-24.56",\n'
            '\t"input_tp" : "-7.23",\n'
            '\t"input_lra" : "14.30",\n'
            '\t"input_thresh" : "-35.12",\n'
            '\t"output_i" : "-14.02",\n'
            '\t"output_tp" : "-1.49",\n'
            '\t"output_lra" : "3.50",\n'
            '\t"output_thresh" : "-24.50",\n'
            '\t"normalization_type" : "dynamic",\n'
            '\t"target_offset" : "0.02"\n'
            "}"
        )
        result = parse_loudnorm_stats(output)
        assert result.input_i == -24.56
        assert result.input_tp == -7.23
        assert result.input_lra == 14.30
        assert result.input_thresh == -35.12
        assert result.target_offset == 0.02

    def test_parse_from_mixed_output(self) -> None:
        """ffmpeg 진행률 로그가 섞인 출력에서 추출."""
        output = (
            "frame=  100 fps=25.0 q=28.0 size=    1234kB time=00:00:04.00\n"
            "bitrate= 2528.0kbits/s speed=1.50x\n"
            "[Parsed_loudnorm_0 @ 0x7fa]\n"
            "{\n"
            '\t"input_i" : "-20.00",\n'
            '\t"input_tp" : "-5.00",\n'
            '\t"input_lra" : "10.00",\n'
            '\t"input_thresh" : "-30.00",\n'
            '\t"output_i" : "-14.00",\n'
            '\t"output_tp" : "-1.50",\n'
            '\t"output_lra" : "3.00",\n'
            '\t"output_thresh" : "-24.00",\n'
            '\t"normalization_type" : "dynamic",\n'
            '\t"target_offset" : "0.00"\n'
            "}\n"
            "video:0kB audio:123kB subtitle:0kB\n"
        )
        result = parse_loudnorm_stats(output)
        assert result.input_i == -20.0
        assert result.input_tp == -5.0

    def test_raises_on_missing_json(self) -> None:
        """JSON 블록 없으면 ValueError."""
        output = "frame=100 fps=25 q=28 size=1234kB time=00:00:04.00"
        with pytest.raises(ValueError, match="loudnorm JSON block not found"):
            parse_loudnorm_stats(output)

    def test_raises_on_invalid_values(self) -> None:
        """숫자 변환 불가능한 값이면 ValueError."""
        output = (
            "[Parsed_loudnorm_0 @ 0x000]\n"
            "{\n"
            '\t"input_i" : "abc",\n'
            '\t"input_tp" : "1",\n'
            '\t"input_lra" : "1",\n'
            '\t"input_thresh" : "1",\n'
            '\t"target_offset" : "1"\n'
            "}"
        )
        with pytest.raises(ValueError, match="Invalid loudnorm analysis data"):
            parse_loudnorm_stats(output)

    def test_raises_on_silent_audio_negative_inf(self) -> None:
        """완전히 무음인 오디오는 -inf → ValueError (loudnorm 범위 밖)."""
        output = (
            "[Parsed_loudnorm_0 @ 0x000]\n"
            "{\n"
            '\t"input_i" : "-inf",\n'
            '\t"input_tp" : "-inf",\n'
            '\t"input_lra" : "0.00",\n'
            '\t"input_thresh" : "-inf",\n'
            '\t"output_i" : "-inf",\n'
            '\t"output_tp" : "-inf",\n'
            '\t"output_lra" : "0.00",\n'
            '\t"output_thresh" : "-inf",\n'
            '\t"normalization_type" : "dynamic",\n'
            '\t"target_offset" : "inf"\n'
            "}"
        )
        with pytest.raises(ValueError, match="silent audio"):
            parse_loudnorm_stats(output)

    def test_raises_on_partial_inf_measured_i(self) -> None:
        """measured_I만 -inf인 경우에도 ValueError."""
        output = (
            "[Parsed_loudnorm_0 @ 0x000]\n"
            "{\n"
            '\t"input_i" : "-inf",\n'
            '\t"input_tp" : "-3.00",\n'
            '\t"input_lra" : "5.00",\n'
            '\t"input_thresh" : "-40.00",\n'
            '\t"output_i" : "-14.00",\n'
            '\t"output_tp" : "-1.50",\n'
            '\t"output_lra" : "3.00",\n'
            '\t"output_thresh" : "-24.00",\n'
            '\t"normalization_type" : "dynamic",\n'
            '\t"target_offset" : "0.00"\n'
            "}"
        )
        with pytest.raises(ValueError, match="silent audio"):
            parse_loudnorm_stats(output)


class TestAudioFilterChainWithLoudnorm:
    """create_audio_filter_chain + loudnorm 통합 테스트."""

    def test_loudnorm_appended_after_fade(self) -> None:
        """afade 뒤에 loudnorm이 위치."""
        analysis = LoudnormAnalysis(
            input_i=-20.0,
            input_tp=-5.0,
            input_lra=10.0,
            input_thresh=-30.0,
            target_offset=0.0,
        )
        result = create_audio_filter_chain(
            total_duration=120.0,
            loudnorm_analysis=analysis,
        )
        parts = result.split(",")
        afade_indices = [i for i, p in enumerate(parts) if "afade" in p]
        loudnorm_indices = [i for i, p in enumerate(parts) if "loudnorm" in p]
        assert afade_indices
        assert loudnorm_indices
        assert max(afade_indices) < min(loudnorm_indices)

    def test_no_loudnorm_when_none(self) -> None:
        """loudnorm_analysis=None이면 loudnorm 미적용."""
        result = create_audio_filter_chain(total_duration=120.0)
        assert "loudnorm" not in result

    def test_loudnorm_only_for_very_short_video(self) -> None:
        """매우 짧은 영상: fade 없이 loudnorm만 적용."""
        analysis = LoudnormAnalysis(
            input_i=-20.0,
            input_tp=-5.0,
            input_lra=10.0,
            input_thresh=-30.0,
            target_offset=0.0,
        )
        result = create_audio_filter_chain(
            total_duration=0.05,
            loudnorm_analysis=analysis,
        )
        assert "afade" not in result
        assert "loudnorm" in result


class TestCombinedFilterWithLoudnorm:
    """create_combined_filter + loudnorm 통합 테스트."""

    def test_loudnorm_in_audio_filter(self) -> None:
        """loudnorm_analysis 전달 시 오디오 필터에 loudnorm 포함."""
        analysis = LoudnormAnalysis(
            input_i=-20.0,
            input_tp=-5.0,
            input_lra=10.0,
            input_thresh=-30.0,
            target_offset=0.0,
        )
        _, audio_filter = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=120.0,
            is_portrait=False,
            loudnorm_analysis=analysis,
        )
        assert "loudnorm" in audio_filter

    def test_no_loudnorm_by_default(self) -> None:
        """기본값(None)이면 loudnorm 미적용."""
        _, audio_filter = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=120.0,
            is_portrait=False,
        )
        assert "loudnorm" not in audio_filter


class TestSilenceDetectFilter:
    """무음 감지 필터 테스트."""

    def test_creates_valid_filter_string(self) -> None:
        """유효한 필터 문자열 생성 확인."""
        filter_str = create_silence_detect_filter(
            threshold="-30dB",
            min_duration=2.0,
        )
        assert "silencedetect" in filter_str
        assert "noise=-30dB" in filter_str
        assert "d=2.0" in filter_str

    def test_custom_threshold(self) -> None:
        """커스텀 threshold 확인."""
        filter_str = create_silence_detect_filter(threshold="-40dB")
        assert "noise=-40dB" in filter_str

    def test_custom_duration(self) -> None:
        """커스텀 duration 확인."""
        filter_str = create_silence_detect_filter(min_duration=1.5)
        assert "d=1.5" in filter_str


class TestSilenceRemoveFilter:
    """무음 제거 필터 테스트."""

    def test_trim_both_start_and_end(self) -> None:
        """시작과 끝 모두 트림 확인."""
        filter_str = create_silence_remove_filter(
            threshold="-30dB",
            min_duration=2.0,
            trim_start=True,
            trim_end=True,
        )
        assert "silenceremove" in filter_str
        assert "start_periods=1" in filter_str
        assert "stop_periods=-1" in filter_str
        assert "start_threshold=-30dB" in filter_str
        assert "stop_threshold=-30dB" in filter_str

    def test_trim_start_only(self) -> None:
        """시작만 트림 확인."""
        filter_str = create_silence_remove_filter(
            trim_start=True,
            trim_end=False,
        )
        assert "start_periods=1" in filter_str
        assert "stop_periods=0" in filter_str

    def test_trim_end_only(self) -> None:
        """끝만 트림 확인."""
        filter_str = create_silence_remove_filter(
            trim_start=False,
            trim_end=True,
        )
        assert "start_periods=0" in filter_str
        assert "stop_periods=-1" in filter_str


class TestParseSilenceSegments:
    """무음 구간 파싱 테스트."""

    def test_parses_single_segment(self) -> None:
        """단일 세그먼트 파싱 확인."""
        stderr = """
[silencedetect @ 0x...] silence_start: 0
[silencedetect @ 0x...] silence_end: 2.5 | silence_duration: 2.5
"""
        segments = parse_silence_segments(stderr)
        assert len(segments) == 1
        assert segments[0].start == 0.0
        assert segments[0].end == 2.5
        assert segments[0].duration == 2.5

    def test_parses_multiple_segments(self) -> None:
        """복수 세그먼트 파싱 확인."""
        stderr = """
[silencedetect @ 0x...] silence_start: 0.0
[silencedetect @ 0x...] silence_end: 2.0 | silence_duration: 2.0
[silencedetect @ 0x...] silence_start: 10.5
[silencedetect @ 0x...] silence_end: 13.0 | silence_duration: 2.5
"""
        segments = parse_silence_segments(stderr)
        assert len(segments) == 2
        assert segments[0].start == 0.0
        assert segments[0].end == 2.0
        assert segments[1].start == 10.5
        assert segments[1].end == 13.0

    def test_empty_output_returns_empty_list(self) -> None:
        """빈 출력 시 빈 리스트 반환 확인."""
        segments = parse_silence_segments("")
        assert segments == []

    def test_ignores_incomplete_segments(self) -> None:
        """불완전한 세그먼트 무시 확인."""
        stderr = """
[silencedetect @ 0x...] silence_start: 0.0
"""
        segments = parse_silence_segments(stderr)
        assert segments == []


class TestCreateAudioFilterChainWithSilenceRemove:
    """오디오 필터 체인에 무음 제거 통합 테스트."""

    def test_includes_silence_remove_when_provided(self) -> None:
        """silence_remove 파라미터 제공 시 포함 확인."""
        silence_filter = create_silence_remove_filter()
        audio_filter = create_audio_filter_chain(
            total_duration=120.0,
            silence_remove=silence_filter,
        )
        assert "silenceremove" in audio_filter

    def test_correct_filter_order(self) -> None:
        """필터 순서 확인: denoise -> silence_remove -> fade -> loudnorm."""
        silence_filter = create_silence_remove_filter()
        loudnorm_analysis = LoudnormAnalysis(
            input_i=-20.0,
            input_tp=-1.0,
            input_lra=10.0,
            input_thresh=-30.0,
            target_offset=0.5,
        )
        audio_filter = create_audio_filter_chain(
            total_duration=120.0,
            denoise=True,
            silence_remove=silence_filter,
            loudnorm_analysis=loudnorm_analysis,
        )

        # 필터 순서 검증
        filters = audio_filter.split(",")
        assert any("afftdn" in f for f in filters)  # denoise
        assert any("silenceremove" in f for f in filters)  # silence_remove
        assert any("afade" in f for f in filters)  # fade
        assert any("loudnorm" in f for f in filters)  # loudnorm

        # denoise가 silence_remove보다 먼저 나와야 함
        denoise_idx = next(i for i, f in enumerate(filters) if "afftdn" in f)
        silence_idx = next(i for i, f in enumerate(filters) if "silenceremove" in f)
        assert denoise_idx < silence_idx


class TestTimelapseVideoFilter:
    """타임랩스 비디오 필터 테스트."""

    def test_creates_setpts_filter(self) -> None:
        """setpts 필터 생성 확인."""
        filter_str = create_timelapse_video_filter(speed=10)
        assert filter_str == "setpts=PTS/10"

    def test_minimum_speed(self) -> None:
        """최소 배속 (2x) 확인."""
        filter_str = create_timelapse_video_filter(speed=TIMELAPSE_MIN_SPEED)
        assert "setpts=PTS/2" in filter_str

    def test_maximum_speed(self) -> None:
        """최대 배속 (60x) 확인."""
        filter_str = create_timelapse_video_filter(speed=TIMELAPSE_MAX_SPEED)
        assert "setpts=PTS/60" in filter_str

    def test_raises_on_invalid_speed_too_low(self) -> None:
        """범위 미만 배속 시 에러."""
        with pytest.raises(ValueError, match="must be between"):
            create_timelapse_video_filter(speed=1)

    def test_raises_on_invalid_speed_too_high(self) -> None:
        """범위 초과 배속 시 에러."""
        with pytest.raises(ValueError, match="must be between"):
            create_timelapse_video_filter(speed=61)


class TestTimelapseAudioFilter:
    """타임랩스 오디오 필터 테스트."""

    def test_simple_speed_within_atempo_limit(self) -> None:
        """atempo 단일 필터로 처리 가능한 배속 (2x)."""
        filter_str = create_timelapse_audio_filter(speed=2)
        assert filter_str == "atempo=2.0"

    def test_chain_for_high_speed(self) -> None:
        """높은 배속은 atempo 체인 (10x = 2.0^3 * 1.25)."""
        filter_str = create_timelapse_audio_filter(speed=10)
        # 10 = 2.0 * 2.0 * 2.0 * 1.25 = 8 * 1.25
        filters = filter_str.split(",")
        assert len(filters) == 4
        assert filters[0] == "atempo=2.0"
        assert filters[1] == "atempo=2.0"
        assert filters[2] == "atempo=2.0"
        assert "atempo=1." in filters[3]  # 1.25

    def test_maximum_speed_chain(self) -> None:
        """최대 배속 (60x) 체인 확인."""
        filter_str = create_timelapse_audio_filter(speed=60)
        # 60 = 2.0^5 * 1.875 = 32 * 1.875
        assert "atempo=2.0" in filter_str
        assert filter_str.count("atempo=2.0") == 5  # 5번 체인

    def test_raises_on_invalid_speed(self) -> None:
        """잘못된 배속 시 에러."""
        with pytest.raises(ValueError, match="must be between"):
            create_timelapse_audio_filter(speed=1)

        with pytest.raises(ValueError, match="must be between"):
            create_timelapse_audio_filter(speed=61)


class TestCombinedFilterWithLut:
    """LUT 필터 통합 테스트."""

    def test_landscape_lut_default_position(self, tmp_path: Path) -> None:
        """가로 영상: LUT 기본 위치 (HDR 뒤, fade 앞)."""
        lut_file = tmp_path / "test.cube"
        lut_file.write_text("LUT data\n")
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            lut_path=str(lut_file),
        )
        assert "lut3d=" in video_filter
        # LUT는 scale+pad 뒤에 위치
        parts = video_filter.split(",")
        lut_idx = next(i for i, p in enumerate(parts) if "lut3d" in p)
        pad_idx = next(i for i, p in enumerate(parts) if "pad=" in p)
        assert lut_idx > pad_idx

    def test_landscape_lut_before_hdr(self, tmp_path: Path) -> None:
        """가로 영상: LUT before HDR 위치."""
        lut_file = tmp_path / "test.cube"
        lut_file.write_text("LUT data\n")
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            color_transfer="arib-std-b67",
            lut_path=str(lut_file),
            lut_before_hdr=True,
        )
        assert "lut3d=" in video_filter
        assert "colorspace=" in video_filter
        # LUT가 HDR 변환 앞에 위치
        lut_pos = video_filter.index("lut3d=")
        hdr_pos = video_filter.index("colorspace=")
        assert lut_pos < hdr_pos

    def test_portrait_lut_default_position(self, tmp_path: Path) -> None:
        """세로 영상: LUT 기본 위치 (overlay 뒤, fade 앞)."""
        lut_file = tmp_path / "test.cube"
        lut_file.write_text("LUT data\n")
        video_filter, _ = create_combined_filter(
            source_width=1080,
            source_height=1920,
            total_duration=60.0,
            is_portrait=True,
            lut_path=str(lut_file),
        )
        assert "lut3d=" in video_filter
        assert "overlay" in video_filter

    def test_portrait_lut_before_hdr(self, tmp_path: Path) -> None:
        """세로 영상: LUT before HDR 위치."""
        lut_file = tmp_path / "test.cube"
        lut_file.write_text("LUT data\n")
        video_filter, _ = create_combined_filter(
            source_width=1080,
            source_height=1920,
            total_duration=60.0,
            is_portrait=True,
            color_transfer="arib-std-b67",
            lut_path=str(lut_file),
            lut_before_hdr=True,
        )
        assert "lut3d=" in video_filter
        assert "colorspace=" in video_filter
        # LUT가 HDR 변환 앞에 위치
        lut_pos = video_filter.index("lut3d=")
        hdr_pos = video_filter.index("colorspace=")
        assert lut_pos < hdr_pos

    def test_no_lut_when_none(self) -> None:
        """lut_path=None일 때 LUT 필터 미포함."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            lut_path=None,
        )
        assert "lut3d=" not in video_filter

    def test_lut_with_sdr_source(self, tmp_path: Path) -> None:
        """SDR 소스에 LUT 적용."""
        lut_file = tmp_path / "test.cube"
        lut_file.write_text("LUT data\n")
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            color_transfer="bt709",
            lut_path=str(lut_file),
        )
        assert "lut3d=" in video_filter
        assert "colorspace=" not in video_filter

    def test_lut_with_stabilize(self, tmp_path: Path) -> None:
        """안정화 + LUT 조합."""
        lut_file = tmp_path / "test.cube"
        lut_file.write_text("LUT data\n")
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            stabilize_filter="vidstabtransform=input=/tmp/t.trf:smoothing=15:crop=keep",
            lut_path=str(lut_file),
        )
        assert "lut3d=" in video_filter
        assert "vidstabtransform=" in video_filter


class TestCombinedFilterWithWatermark:
    """워터마크 필터 통합 테스트."""

    def test_create_watermark_filter(self) -> None:
        """워터마크 필터 문자열 생성."""
        filter_str = create_watermark_filter(
            text="2025.01.02 | Seoul Downtown",
            position="top-left",
            font_size=32,
            color="yellow",
            alpha=0.7,
        )

        assert "drawtext=" in filter_str
        assert "text='2025.01.02 | Seoul Downtown'" in filter_str
        assert "x=24" in filter_str
        assert "fontsize=32" in filter_str
        assert "font='monospace'" in filter_str
        assert "fontcolor=yellow@0.70" in filter_str

    def test_create_watermark_filter_escapes_percent(self) -> None:
        """퍼센트 기호는 drawtext 파서에서 literal로 인식되도록 이스케이프된다."""
        filter_str = create_watermark_filter(
            text="100% safe",
            position="bottom-right",
            font_size=24,
            color="white",
            alpha=1.0,
        )

        assert "100%% safe" in filter_str

    def test_combined_filter_includes_watermark(self) -> None:
        """combined filter에서 watermark 텍스트가 적용되어야 함."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            watermark_text="2025.01.02 | Seoul Downtown",
            watermark_position="center",
            watermark_size=24,
            watermark_color="white",
            watermark_alpha=0.85,
        )

        assert "drawtext=" in video_filter
        assert "text='2025.01.02 | Seoul Downtown'" in video_filter
        assert "fontcolor=white@0.85" in video_filter

    def test_combined_filter_without_watermark_text(self) -> None:
        """워터마크 텍스트가 없으면 drawtext 미포함."""
        video_filter, _ = create_combined_filter(
            source_width=3840,
            source_height=2160,
            total_duration=60.0,
            is_portrait=False,
            watermark_text=None,
        )

        assert "drawtext=" not in video_filter
