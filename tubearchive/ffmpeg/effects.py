"""FFmpeg 영상 효과 필터."""

from enum import Enum

# HDR 색공간 식별자
HDR_COLOR_TRANSFERS = {"arib-std-b67", "smpte2084"}


class StabilizeStrength(Enum):
    """영상 안정화 강도."""

    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


class StabilizeCrop(Enum):
    """영상 안정화 테두리 처리."""

    CROP = "crop"
    EXPAND = "expand"


# vidstab 강도별 파라미터 매핑
_VIDSTAB_PARAMS: dict[StabilizeStrength, dict[str, int]] = {
    StabilizeStrength.LIGHT: {"shakiness": 4, "accuracy": 9, "smoothing": 10},
    StabilizeStrength.MEDIUM: {"shakiness": 6, "accuracy": 12, "smoothing": 15},
    StabilizeStrength.HEAVY: {"shakiness": 8, "accuracy": 15, "smoothing": 30},
}

# vidstab crop 모드 매핑
_VIDSTAB_CROP: dict[StabilizeCrop, str] = {
    StabilizeCrop.CROP: "keep",
    StabilizeCrop.EXPAND: "black",
}


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


def _calculate_fade_params(
    total_duration: float,
    fade_duration: float = 0.5,
) -> tuple[float, float]:
    """
    Fade 파라미터 계산 (짧은 영상 처리).

    Args:
        total_duration: 영상 전체 길이 (초)
        fade_duration: 원하는 페이드 지속 시간 (기본: 0.5초)

    Returns:
        (effective_fade_duration, fade_out_start) 튜플
        - 영상이 충분히 길면: (fade_duration, total_duration - fade_duration)
        - 영상이 짧으면: fade를 비례 축소하거나 0으로 설정
    """
    # 최소 요구 길이: fade_in + fade_out (겹치지 않게)
    min_duration_for_full_fade = fade_duration * 2

    if total_duration >= min_duration_for_full_fade:
        # 충분히 긴 영상: 정상 fade 적용
        return fade_duration, total_duration - fade_duration
    elif total_duration > 0.1:  # 0.1초 이상이면 축소된 fade 적용
        # 짧은 영상: fade를 비례 축소 (겹치지 않도록)
        effective_fade = total_duration / 2
        return effective_fade, total_duration - effective_fade
    else:
        # 매우 짧은 영상 (0.1초 미만): fade 생략
        return 0.0, 0.0


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
    effective_fade, fade_out_start = _calculate_fade_params(total_duration, fade_duration)

    if effective_fade <= 0:
        # fade 생략 (매우 짧은 영상)
        return ""

    # fade=in:0:d=0.5,fade=out:st=119.5:d=0.5
    return f"fade=t=in:st=0:d={effective_fade},fade=t=out:st={fade_out_start}:d={effective_fade}"


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
    effective_fade, fade_out_start = _calculate_fade_params(total_duration, fade_duration)

    if effective_fade <= 0:
        # fade 생략 (매우 짧은 영상)
        return ""

    # afade=t=in:st=0:d=0.5,afade=t=out:st=119.5:d=0.5
    return f"afade=t=in:st=0:d={effective_fade},afade=t=out:st={fade_out_start}:d={effective_fade}"


def create_vidstab_detect_filter(
    strength: StabilizeStrength = StabilizeStrength.MEDIUM,
    trf_path: str = "",
) -> str:
    """
    vidstab 분석 패스 필터 생성 (Pass 1).

    Args:
        strength: 안정화 강도
        trf_path: transform 파일 저장 경로

    Returns:
        vidstabdetect 필터 문자열
    """
    params = _VIDSTAB_PARAMS[strength]
    return (
        f"vidstabdetect=shakiness={params['shakiness']}"
        f":accuracy={params['accuracy']}"
        f":result={trf_path}"
    )


def create_vidstab_transform_filter(
    strength: StabilizeStrength = StabilizeStrength.MEDIUM,
    crop: StabilizeCrop = StabilizeCrop.CROP,
    trf_path: str = "",
) -> str:
    """
    vidstab 변환 필터 생성 (Pass 2).

    Args:
        strength: 안정화 강도
        crop: 테두리 처리 방식
        trf_path: transform 파일 경로

    Returns:
        vidstabtransform 필터 문자열
    """
    params = _VIDSTAB_PARAMS[strength]
    crop_value = _VIDSTAB_CROP[crop]
    return f"vidstabtransform=input={trf_path}:smoothing={params['smoothing']}:crop={crop_value}"


def _build_portrait_video_filter(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    blur_radius: int,
    hdr_filter: str,
    fade_filters: str,
    stabilize_filter: str = "",
) -> str:
    """세로 영상용 filter_complex 문자열 생성 (stabilize → HDR → split → blur → overlay)."""
    fg_height = target_height
    fg_width = int(source_width * (fg_height / source_height))

    # 입력: (안정화) → (HDR 변환) → split
    # 안정화를 가장 먼저 적용하여 이후 처리가 안정된 프레임 위에서 수행되도록 함
    split_input = ",".join(filter(None, [stabilize_filter, hdr_filter, "split=2[bg][fg]"]))

    # 배경: 스케일 → crop → blur
    bg_chain = (
        f"[bg]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
        f"crop={target_width}:{target_height},"
        f"boxblur={blur_radius}:1[bg_blur]"
    )

    # 전경: 높이에 맞춰 스케일
    fg_chain = f"[fg]scale={fg_width}:{fg_height}[fg_scaled]"

    # 합성: 중앙 오버레이 + (fade)
    overlay = "[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2"
    merge_chain = ",".join(filter(None, [overlay, fade_filters]))

    return f"[0:v]{split_input};{bg_chain};{fg_chain};{merge_chain}[v_out]"


def _build_landscape_video_filter(
    target_width: int,
    target_height: int,
    hdr_filter: str,
    fade_filters: str,
    stabilize_filter: str = "",
) -> str:
    """가로 영상용 -vf 필터 문자열 생성 (stabilize → HDR → scale → pad → fade)."""
    scale_pad = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2"
    )

    # 안정화 → HDR 변환을 먼저 적용하여 이후 처리가 안정된 BT.709에서 수행되도록 함
    parts = [p for p in [stabilize_filter, hdr_filter, scale_pad, fade_filters] if p]
    return f"[0:v]{','.join(parts)}[v_out]"


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
    stabilize_filter: str = "",
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
        stabilize_filter: vidstabtransform 필터 문자열 (빈 문자열이면 미적용)

    Returns:
        (video_filter, audio_filter) 튜플
    """
    effective_fade, fade_out_start = _calculate_fade_params(total_duration, fade_duration)
    hdr_filter = create_hdr_to_sdr_filter(color_transfer)

    fade_filters = ""
    if effective_fade > 0:
        fade_filters = (
            f"fade=t=in:st=0:d={effective_fade},fade=t=out:st={fade_out_start}:d={effective_fade}"
        )

    if is_portrait:
        video_filter = _build_portrait_video_filter(
            source_width,
            source_height,
            target_width,
            target_height,
            blur_radius,
            hdr_filter,
            fade_filters,
            stabilize_filter,
        )
    else:
        video_filter = _build_landscape_video_filter(
            target_width,
            target_height,
            hdr_filter,
            fade_filters,
            stabilize_filter,
        )

    audio_filter = create_dip_to_black_audio_filter(total_duration, fade_duration)
    return video_filter, audio_filter
