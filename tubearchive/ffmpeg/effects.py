"""FFmpeg 영상 효과 필터."""

# HDR 색공간 식별자
HDR_COLOR_TRANSFERS = {"arib-std-b67", "smpte2084"}


def create_hdr_to_sdr_filter(color_transfer: str | None) -> str:
    """
    HDR→SDR 변환 필터 생성.

    BT.2020 색공간의 HDR 영상을 BT.709 SDR로 변환합니다.

    Args:
        color_transfer: 소스 영상의 color_transfer 메타데이터

    Returns:
        HDR인 경우 colorspace 필터 문자열, SDR이면 빈 문자열
    """
    if color_transfer is None or color_transfer not in HDR_COLOR_TRANSFERS:
        return ""

    # colorspace 필터: BT.2020 → BT.709 변환
    # all=bt709: 출력 색공간 (primaries, transfer, matrix 모두 bt709)
    # iall=bt2020: 입력 색공간 (BT.2020)
    # dither=fsb: Floyd-Steinberg 디더링 (색상 밴딩/노이즈 감소)
    # Note: format 옵션은 colorspace 필터에서 지원하지 않음 (별도 format 필터 사용)
    # Note: fast=1 제거 - 색상 정확도 우선 (iPhone 포트레이트 노이즈 방지)
    return "colorspace=all=bt709:iall=bt2020:dither=fsb,format=yuv420p10le"


def create_portrait_layout_filter(
    source_width: int,
    source_height: int,
    target_width: int = 3840,
    target_height: int = 2160,
    blur_radius: int = 20,
) -> str:
    """
    세로 영상을 가로 레이아웃으로 변환하는 필터.

    블러 배경 + 중앙 전경 레이아웃.

    Args:
        source_width: 원본 영상 너비
        source_height: 원본 영상 높이
        target_width: 타겟 너비 (기본: 3840)
        target_height: 타겟 높이 (기본: 2160)
        blur_radius: 배경 블러 반경 (기본: 20)

    Returns:
        FFmpeg filter_complex 문자열
    """
    # 전경 스케일: 타겟 높이에 맞춤 (비율 유지)
    fg_height = target_height
    fg_width = int(source_width * (fg_height / source_height))

    # 필터 체인 구성
    # 1. 스트림을 배경(bg)과 전경(fg)으로 분할
    # 2. 배경: 타겟 크기로 스케일 + crop + 블러
    # 3. 전경: 높이에 맞춰 스케일
    # 4. 배경 위에 전경을 중앙에 오버레이
    filter_parts = [
        "[0:v]split=2[bg][fg]",
        f"[bg]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
        f"crop={target_width}:{target_height},"
        f"boxblur={blur_radius}:1[bg_blur]",
        f"[fg]scale={fg_width}:{fg_height}[fg_scaled]",
        "[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2[v_out]",
    ]

    return ";".join(filter_parts)


def create_dip_to_black_video_filter(
    total_duration: float,
    fade_duration: float = 0.5,
) -> str:
    """
    Dip-to-Black 비디오 필터 (Fade In/Out).

    Args:
        total_duration: 영상 전체 길이 (초)
        fade_duration: 페이드 지속 시간 (기본: 0.5초)

    Returns:
        FFmpeg -vf 필터 문자열
    """
    fade_out_start = total_duration - fade_duration

    # fade=in:0:d=0.5,fade=out:st=119.5:d=0.5
    return f"fade=t=in:st=0:d={fade_duration},fade=t=out:st={fade_out_start}:d={fade_duration}"


def create_dip_to_black_audio_filter(
    total_duration: float,
    fade_duration: float = 0.5,
) -> str:
    """
    Dip-to-Black 오디오 필터 (Audio Fade In/Out).

    Args:
        total_duration: 영상 전체 길이 (초)
        fade_duration: 페이드 지속 시간 (기본: 0.5초)

    Returns:
        FFmpeg -af 필터 문자열
    """
    fade_out_start = total_duration - fade_duration

    # afade=t=in:st=0:d=0.5,afade=t=out:st=119.5:d=0.5
    return f"afade=t=in:st=0:d={fade_duration},afade=t=out:st={fade_out_start}:d={fade_duration}"


def create_combined_filter(
    source_width: int,
    source_height: int,
    total_duration: float,
    is_portrait: bool,
    target_width: int = 3840,
    target_height: int = 2160,
    fade_duration: float = 0.5,
    blur_radius: int = 20,
    color_transfer: str | None = None,
) -> tuple[str, str]:
    """
    모든 효과를 결합한 필터 생성.

    Args:
        source_width: 원본 영상 너비
        source_height: 원본 영상 높이
        total_duration: 영상 전체 길이 (초)
        is_portrait: 세로 영상 여부
        target_width: 타겟 너비 (기본: 3840)
        target_height: 타겟 높이 (기본: 2160)
        fade_duration: 페이드 지속 시간 (기본: 0.5초)
        blur_radius: 배경 블러 반경 (기본: 20)
        color_transfer: 소스 영상의 color_transfer (HDR 변환 판단용)

    Returns:
        (video_filter, audio_filter) 튜플
    """
    fade_out_start = total_duration - fade_duration

    # HDR→SDR 변환 필터 (필요시)
    hdr_filter = create_hdr_to_sdr_filter(color_transfer)

    if is_portrait:
        # 세로 영상: Portrait 레이아웃 + Fade
        fg_height = target_height
        fg_width = int(source_width * (fg_height / source_height))

        # HDR 변환: split 전에 적용하여 모든 후처리가 BT.709에서 수행되도록 함
        # (색상 일관성 유지, boxblur/overlay 후 변환 시 발생하는 색상 노이즈 방지)
        if hdr_filter:
            video_filter = (
                f"[0:v]{hdr_filter},split=2[bg][fg];"
                f"[bg]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                f"crop={target_width}:{target_height},"
                f"boxblur={blur_radius}:1[bg_blur];"
                f"[fg]scale={fg_width}:{fg_height}[fg_scaled];"
                f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2,"
                f"fade=t=in:st=0:d={fade_duration},"
                f"fade=t=out:st={fade_out_start}:d={fade_duration}[v_out]"
            )
        else:
            video_filter = (
                f"[0:v]split=2[bg][fg];"
                f"[bg]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
                f"crop={target_width}:{target_height},"
                f"boxblur={blur_radius}:1[bg_blur];"
                f"[fg]scale={fg_width}:{fg_height}[fg_scaled];"
                f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2,"
                f"fade=t=in:st=0:d={fade_duration},"
                f"fade=t=out:st={fade_out_start}:d={fade_duration}[v_out]"
            )
    else:
        # 가로 영상: (HDR 변환) + 스케일 + Fade
        # HDR 변환을 먼저 적용하여 이후 처리가 BT.709에서 수행되도록 함
        if hdr_filter:
            video_filter = (
                f"[0:v]{hdr_filter},"
                f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,"
                f"fade=t=in:st=0:d={fade_duration},"
                f"fade=t=out:st={fade_out_start}:d={fade_duration}[v_out]"
            )
        else:
            video_filter = (
                f"[0:v]scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,"
                f"fade=t=in:st=0:d={fade_duration},"
                f"fade=t=out:st={fade_out_start}:d={fade_duration}[v_out]"
            )

    audio_filter = create_dip_to_black_audio_filter(total_duration, fade_duration)

    return video_filter, audio_filter
