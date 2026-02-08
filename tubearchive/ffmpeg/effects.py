"""FFmpeg 필터 체인 생성기.

영상·오디오 효과에 필요한 FFmpeg ``-filter_complex`` 문자열을 구성한다.

지원 효과:
    - **세로 영상 레이아웃**: 블러 배경 위에 원본 오버레이 (3840x2160)
    - **HDR → SDR**: BT.2020 → BT.709 색공간 변환
    - **Dip-to-Black**: 클립 시작/끝 페이드 인·아웃
    - **오디오 노이즈 제거**: afftdn 기반 (light/medium/heavy)
    - **무음 구간 감지/제거**: silencedetect, silenceremove 필터
    - **라우드니스 정규화**: EBU R128 loudnorm 2-pass
    - **영상 안정화**: vidstab detect + transform
    - **타임랩스**: setpts 기반 배속 조절 (2x ~ 60x)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NamedTuple

# HDR 색공간 식별자 (FFmpeg color_transfer 값)
HDR_TRANSFER_HLG = "arib-std-b67"
HDR_TRANSFER_PQ = "smpte2084"
HDR_COLOR_TRANSFERS = {HDR_TRANSFER_HLG, HDR_TRANSFER_PQ}

# HDR → SDR 변환 필터 (BT.2020 → BT.709)
HDR_TO_SDR_FILTER = "colorspace=all=bt709:iall=bt2020:dither=fsb,format=yuv420p10le"

# 오디오 노이즈 제거 강도 (afftdn 기준)
DENOISE_AFFTDN_LEVELS = {
    "light": 6,
    "medium": 12,
    "heavy": 18,
}

# EBU R128 라우드니스 정규화 기본 타겟값
LOUDNORM_TARGET_I = -14.0
"""통합 라우드니스 (Integrated Loudness) 목표 (LUFS)."""
LOUDNORM_TARGET_TP = -1.5
"""트루피크 (True Peak) 상한 (dBTP)."""
LOUDNORM_TARGET_LRA = 11.0
"""라우드니스 범위 (Loudness Range) 목표 (LU)."""

# 세로 영상 배경 블러 반경
PORTRAIT_BLUR_RADIUS = 20

# 타임랩스 배속 범위
TIMELAPSE_MIN_SPEED = 2
"""타임랩스 최소 배속."""
TIMELAPSE_MAX_SPEED = 60
"""타임랩스 최대 배속."""
ATEMPO_MAX = 2.0
"""FFmpeg atempo 필터 최대 배속 (단일 필터 제약)."""

# FFmpeg lut3d는 .cube/.3dl 외에도 .dat 등을 지원하지만,
# 실무에서 거의 쓰이지 않으므로 이 두 형식만 허용한다.
LUT_SUPPORTED_EXTENSIONS = {".cube", ".3dl"}


class StabilizeStrength(Enum):
    """vidstab 영상 안정화 강도.

    ``shakiness`` (흔들림 감지 민감도)와 ``smoothing`` (보정 윈도우 크기)
    파라미터를 함께 결정한다. 강도가 높을수록 안정적이지만 크롭 영역이 넓어진다.

    Values:
        LIGHT: shakiness=4, smoothing=10 — 가벼운 손떨림 보정
        MEDIUM: shakiness=6, smoothing=15 — 일반적인 핸드헬드 촬영 보정
        HEAVY: shakiness=8, smoothing=30 — 심한 흔들림·이동 촬영 보정
    """

    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"


class StabilizeCrop(Enum):
    """vidstab 안정화 후 프레임 테두리 처리 방식.

    안정화로 인해 빈 영역이 발생할 때 해당 부분을 어떻게 처리할지 결정한다.

    Values:
        CROP: 빈 영역을 잘라내어 유효 영역만 유지 (``keep``)
        EXPAND: 빈 영역을 검은색으로 채움 (``black``)
    """

    CROP = "crop"
    EXPAND = "expand"


class VidstabParams(NamedTuple):
    """vidstab 필터 파라미터.

    Attributes:
        shakiness: 흔들림 감지 민감도 (1-10, 높을수록 민감)
        accuracy: 모션 벡터 추정 정확도 (1-15, 높을수록 정확)
        smoothing: 보정 윈도우 프레임 수 (높을수록 부드러움)
    """

    shakiness: int
    accuracy: int
    smoothing: int


# vidstab 강도별 파라미터 매핑
_VIDSTAB_PARAMS: dict[StabilizeStrength, VidstabParams] = {
    StabilizeStrength.LIGHT: VidstabParams(shakiness=4, accuracy=9, smoothing=10),
    StabilizeStrength.MEDIUM: VidstabParams(shakiness=6, accuracy=12, smoothing=15),
    StabilizeStrength.HEAVY: VidstabParams(shakiness=8, accuracy=15, smoothing=30),
}

# vidstab crop 모드 매핑
_VIDSTAB_CROP: dict[StabilizeCrop, str] = {
    StabilizeCrop.CROP: "keep",
    StabilizeCrop.EXPAND: "black",
}


def create_lut_filter(lut_path: str) -> str:
    """
    LUT(Look-Up Table) 적용 필터 생성.

    .cube 또는 .3dl 형식의 LUT 파일을 FFmpeg lut3d 필터로 적용한다.

    Args:
        lut_path: LUT 파일 경로

    Returns:
        FFmpeg lut3d 필터 문자열

    Raises:
        FileNotFoundError: 파일이 존재하지 않는 경우
        ValueError: 지원하지 않는 확장자인 경우
    """
    path = Path(lut_path).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"LUT file not found: {lut_path}")

    ext = path.suffix.lower()
    if ext not in LUT_SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(LUT_SUPPORTED_EXTENSIONS))
        raise ValueError(f"Unsupported LUT format: {ext} (supported: {supported})")

    # FFmpeg 필터 파서의 특수문자를 백슬래시로 이스케이프한다.
    # subprocess list 호출 시 shell 이스케이핑은 불필요하며,
    # FFmpeg 내부 파서가 \, ', :, ; 를 특수문자로 취급한다.
    escaped = str(path)
    for ch in ("\\", "'", ":", ";"):
        escaped = escaped.replace(ch, f"\\{ch}")
    return f"lut3d=file={escaped}"


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
    return HDR_TO_SDR_FILTER


def create_portrait_layout_filter(
    source_width: int,
    source_height: int,
    target_width: int = 3840,
    target_height: int = 2160,
    blur_radius: int = PORTRAIT_BLUR_RADIUS,
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
    fade_in_duration: float = 0.5,
    fade_out_duration: float = 0.5,
) -> tuple[float, float, float]:
    """
    Fade 파라미터 계산 (짧은 영상 처리).

    Args:
        total_duration: 영상 전체 길이 (초)
        fade_in_duration: Fade In 지속 시간 (기본: 0.5초)
        fade_out_duration: Fade Out 지속 시간 (기본: 0.5초)

    Returns:
        (effective_fade_in, effective_fade_out, fade_out_start) 튜플
    """
    fade_in = max(fade_in_duration, 0.0)
    fade_out = max(fade_out_duration, 0.0)
    total_fade = fade_in + fade_out

    if total_duration <= 0.1 or total_fade <= 0:
        return 0.0, 0.0, 0.0

    if total_duration >= total_fade:
        effective_in = fade_in
        effective_out = fade_out
    else:
        scale = total_duration / total_fade
        effective_in = fade_in * scale
        effective_out = fade_out * scale

    fade_out_start = max(total_duration - effective_out, 0.0)
    return effective_in, effective_out, fade_out_start


def create_dip_to_black_video_filter(
    total_duration: float,
    fade_in_duration: float = 0.5,
    fade_out_duration: float = 0.5,
) -> str:
    """
    Dip-to-Black 비디오 필터 (Fade In/Out).

    Args:
        total_duration: 영상 전체 길이 (초)
        fade_in_duration: Fade In 지속 시간 (기본: 0.5초)
        fade_out_duration: Fade Out 지속 시간 (기본: 0.5초)

    Returns:
        FFmpeg -vf 필터 문자열
    """
    effective_in, effective_out, fade_out_start = _calculate_fade_params(
        total_duration,
        fade_in_duration,
        fade_out_duration,
    )

    filters: list[str] = []
    if effective_in > 0:
        filters.append(f"fade=t=in:st=0:d={effective_in}")
    if effective_out > 0:
        filters.append(f"fade=t=out:st={fade_out_start}:d={effective_out}")

    return ",".join(filters)


def create_dip_to_black_audio_filter(
    total_duration: float,
    fade_in_duration: float = 0.5,
    fade_out_duration: float = 0.5,
) -> str:
    """
    Dip-to-Black 오디오 필터 (Audio Fade In/Out).

    Args:
        total_duration: 영상 전체 길이 (초)
        fade_in_duration: Fade In 지속 시간 (기본: 0.5초)
        fade_out_duration: Fade Out 지속 시간 (기본: 0.5초)

    Returns:
        FFmpeg -af 필터 문자열
    """
    effective_in, effective_out, fade_out_start = _calculate_fade_params(
        total_duration,
        fade_in_duration,
        fade_out_duration,
    )

    filters: list[str] = []
    if effective_in > 0:
        filters.append(f"afade=t=in:st=0:d={effective_in}")
    if effective_out > 0:
        filters.append(f"afade=t=out:st={fade_out_start}:d={effective_out}")

    return ",".join(filters)


def create_denoise_audio_filter(level: str = "medium") -> str:
    """
    오디오 노이즈 제거 필터 생성 (afftdn).

    Args:
        level: 강도 (light/medium/heavy)

    Returns:
        FFmpeg -af 필터 문자열
    """
    key = level.lower()
    if key not in DENOISE_AFFTDN_LEVELS:
        raise ValueError(f"Unsupported denoise level: {level}")
    nr = DENOISE_AFFTDN_LEVELS[key]
    return f"afftdn=nr={nr}"


def create_silence_detect_filter(
    threshold: str = "-30dB",
    min_duration: float = 2.0,
) -> str:
    """
    무음 구간 감지 필터 생성 (silencedetect).

    Args:
        threshold: 무음 기준 데시벨 (예: "-30dB")
        min_duration: 최소 무음 길이 (초)

    Returns:
        FFmpeg -af 필터 문자열
    """
    return f"silencedetect=noise={threshold}:d={min_duration}"


def create_silence_remove_filter(
    threshold: str = "-30dB",
    min_duration: float = 2.0,
    trim_start: bool = True,
    trim_end: bool = True,
) -> str:
    """
    무음 구간 제거 필터 생성 (silenceremove).

    Args:
        threshold: 무음 기준 데시벨 (예: "-30dB")
        min_duration: 최소 무음 길이 (초)
        trim_start: 시작 무음 제거 여부
        trim_end: 끝 무음 제거 여부

    Returns:
        FFmpeg -af 필터 문자열
    """
    return (
        f"silenceremove="
        f"start_periods={1 if trim_start else 0}:"
        f"start_threshold={threshold}:"
        f"start_duration={min_duration}:"
        f"stop_periods={-1 if trim_end else 0}:"
        f"stop_threshold={threshold}:"
        f"stop_duration={min_duration}"
    )


@dataclass(frozen=True)
class LoudnormAnalysis:
    """EBU R128 loudnorm 1st pass 분석 결과."""

    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float


def create_loudnorm_analysis_filter(
    target_i: float = LOUDNORM_TARGET_I,
    target_tp: float = LOUDNORM_TARGET_TP,
    target_lra: float = LOUDNORM_TARGET_LRA,
) -> str:
    """1st pass: loudnorm 분석용 필터 문자열 생성."""
    return f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}:print_format=json"


def create_loudnorm_filter(
    analysis: LoudnormAnalysis,
    target_i: float = LOUDNORM_TARGET_I,
    target_tp: float = LOUDNORM_TARGET_TP,
    target_lra: float = LOUDNORM_TARGET_LRA,
) -> str:
    """2nd pass: loudnorm 적용 필터 문자열 생성 (measured 값 포함)."""
    return (
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}"
        f":measured_I={analysis.input_i}"
        f":measured_TP={analysis.input_tp}"
        f":measured_LRA={analysis.input_lra}"
        f":measured_thresh={analysis.input_thresh}"
        f":offset={analysis.target_offset}"
        f":linear=true"
    )


_LOUDNORM_JSON_PATTERN = re.compile(
    r"\[Parsed_loudnorm.*?\].*?(\{.*?\})",
    re.DOTALL,
)

# 무음 구간 감지를 위한 정규표현식 패턴
_SILENCE_START_PATTERN = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_PATTERN = re.compile(r"silence_end:\s*([0-9.]+)")


@dataclass(frozen=True)
class SilenceSegment:
    """무음 구간 정보."""

    start: float
    """무음 구간 시작 시간 (초)."""
    end: float
    """무음 구간 종료 시간 (초)."""
    duration: float
    """무음 구간 길이 (초)."""


def parse_loudnorm_stats(ffmpeg_output: str) -> LoudnormAnalysis:
    """FFmpeg stderr에서 loudnorm JSON 블록을 추출하여 파싱한다.

    Args:
        ffmpeg_output: FFmpeg 프로세스의 stderr 전체 출력

    Returns:
        LoudnormAnalysis 분석 결과

    Raises:
        ValueError: JSON 블록을 찾을 수 없거나 파싱 실패
    """
    match = _LOUDNORM_JSON_PATTERN.search(ffmpeg_output)
    if not match:
        raise ValueError("loudnorm JSON block not found in FFmpeg output")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse loudnorm JSON: {e}") from e

    try:
        return LoudnormAnalysis(
            input_i=float(data["input_i"]),
            input_tp=float(data["input_tp"]),
            input_lra=float(data["input_lra"]),
            input_thresh=float(data["input_thresh"]),
            target_offset=float(data["target_offset"]),
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Invalid loudnorm analysis data: {e}") from e


def parse_silence_segments(ffmpeg_output: str) -> list[SilenceSegment]:
    """FFmpeg stderr에서 silencedetect 로그를 파싱하여 무음 구간 리스트를 반환한다.

    Args:
        ffmpeg_output: FFmpeg 프로세스의 stderr 전체 출력

    Returns:
        무음 구간 리스트 (SilenceSegment)
    """
    segments: list[SilenceSegment] = []
    current_start: float | None = None

    for line in ffmpeg_output.split("\n"):
        # silence_start 매칭
        start_match = _SILENCE_START_PATTERN.search(line)
        if start_match:
            current_start = float(start_match.group(1))
            continue

        # silence_end 매칭
        end_match = _SILENCE_END_PATTERN.search(line)
        if end_match and current_start is not None:
            end = float(end_match.group(1))
            duration = end - current_start
            segments.append(SilenceSegment(start=current_start, end=end, duration=duration))
            current_start = None

    return segments


def create_bgm_filter(
    bgm_duration: float,
    video_duration: float,
    bgm_volume: float = 0.2,
    bgm_loop: bool = False,
    fade_out_duration: float = 3.0,
    has_audio: bool = True,
) -> str:
    """
    배경음악(BGM) 믹싱 필터 생성.

    Args:
        bgm_duration: BGM 파일의 길이 (초, > 0)
        video_duration: 영상 파일의 길이 (초, > 0)
        bgm_volume: BGM 상대 볼륨 (0.0~1.0, 기본: 0.2)
        bgm_loop: BGM 루프 재생 여부 (기본: False)
        fade_out_duration: 자동 페이드 아웃 시간 (초, 기본: 3.0)
        has_audio: 영상에 오디오 트랙이 있는지 여부 (기본: True)

    Returns:
        filter_complex 문자열 (BGM 입력은 [1:a], 원본 오디오는 [0:a])

    Raises:
        ValueError: bgm_duration 또는 video_duration이 0 이하인 경우

    Examples:
        BGM 길이 < 영상: 루프 재생
        BGM 길이 > 영상: 마지막 3초 페이드 아웃
        BGM 길이 = 영상: 그대로 믹싱
    """
    if bgm_duration <= 0:
        raise ValueError(f"BGM duration must be > 0, got: {bgm_duration}")
    if video_duration <= 0:
        raise ValueError(f"Video duration must be > 0, got: {video_duration}")

    bgm_filters: list[str] = []

    # BGM 길이가 영상보다 짧고 루프가 활성화된 경우
    # loop=-1 (무한 루프) + atrim으로 정확한 길이 보장
    if bgm_duration < video_duration and bgm_loop:
        bgm_filters.append("aloop=loop=-1:size=2000000000")
        bgm_filters.append(f"atrim=end={video_duration}")

    # BGM 길이가 영상보다 긴 경우: 페이드 아웃
    elif bgm_duration > video_duration:
        bgm_filters.append(f"atrim=end={video_duration}")
        # 짧은 영상: fade_out_duration을 video_duration에 맞춤
        effective_fade = min(fade_out_duration, video_duration)
        fade_start = video_duration - effective_fade
        if effective_fade > 0:
            bgm_filters.append(f"afade=t=out:st={fade_start}:d={effective_fade}")

    # 볼륨 조절
    bgm_filters.append(f"volume={bgm_volume}")

    bgm_chain = ",".join(bgm_filters)

    if not has_audio:
        # 오디오 트랙 없는 영상: BGM만 단독 출력
        return f"[1:a]{bgm_chain}[a_out]"

    # amix로 원본 오디오와 BGM 믹싱
    # weights: 원본=1, BGM=bgm_volume (amix 정규화 상쇄)
    amix = f"amix=inputs=2:duration=first:dropout_transition=0:weights=1 {bgm_volume}"
    return f"[1:a]{bgm_chain}[bgm_out];[0:a][bgm_out]{amix}[a_out]"


def create_audio_filter_chain(
    total_duration: float,
    fade_duration: float = 0.5,
    fade_in_duration: float | None = None,
    fade_out_duration: float | None = None,
    denoise: bool = False,
    denoise_level: str = "medium",
    silence_remove: str = "",
    loudnorm_analysis: LoudnormAnalysis | None = None,
) -> str:
    """
    오디오 필터 체인 생성.

    순서: denoise -> silence_remove -> fade -> loudnorm
    """
    filters: list[str] = []
    if denoise:
        filters.append(create_denoise_audio_filter(denoise_level))

    if silence_remove:
        filters.append(silence_remove)

    effective_fade_in = fade_duration if fade_in_duration is None else fade_in_duration
    effective_fade_out = fade_duration if fade_out_duration is None else fade_out_duration
    fade_filter = create_dip_to_black_audio_filter(
        total_duration,
        effective_fade_in,
        effective_fade_out,
    )
    if fade_filter:
        filters.append(fade_filter)

    if loudnorm_analysis is not None:
        filters.append(create_loudnorm_filter(loudnorm_analysis))

    return ",".join(filters)


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
        f"vidstabdetect=shakiness={params.shakiness}:accuracy={params.accuracy}:result={trf_path}"
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
    return f"vidstabtransform=input={trf_path}:smoothing={params.smoothing}:crop={crop_value}"


def create_timelapse_video_filter(speed: int) -> str:
    """
    타임랩스 비디오 필터 생성 (setpts 방식).

    프레임 타임스탬프를 조정하여 재생 속도를 변경합니다.
    예: 10배속 → setpts=PTS/10

    Args:
        speed: 배속 (2-60 범위)

    Returns:
        FFmpeg -vf 필터 문자열

    Raises:
        ValueError: speed가 범위를 벗어난 경우
    """
    if speed < TIMELAPSE_MIN_SPEED or speed > TIMELAPSE_MAX_SPEED:
        raise ValueError(
            f"Speed must be between {TIMELAPSE_MIN_SPEED} and {TIMELAPSE_MAX_SPEED}, got {speed}"
        )
    return f"setpts=PTS/{speed}"


def create_timelapse_audio_filter(speed: int) -> str:
    """
    타임랩스 오디오 필터 생성 (atempo 체인).

    FFmpeg atempo 필터는 0.5-2.0 범위만 지원하므로,
    높은 배속은 여러 atempo를 체인으로 연결하여 구현합니다.
    예: 10배속 → atempo=2.0,atempo=2.0,atempo=2.5

    Args:
        speed: 배속 (2-60 범위)

    Returns:
        FFmpeg -af 필터 문자열

    Raises:
        ValueError: speed가 범위를 벗어난 경우
    """
    if speed < TIMELAPSE_MIN_SPEED or speed > TIMELAPSE_MAX_SPEED:
        raise ValueError(
            f"Speed must be between {TIMELAPSE_MIN_SPEED} and {TIMELAPSE_MAX_SPEED}, got {speed}"
        )

    # atempo는 0.5-2.0 범위만 지원, 체인으로 높은 배속 구현
    filters: list[str] = []
    remaining = float(speed)

    while remaining > ATEMPO_MAX:
        filters.append(f"atempo={ATEMPO_MAX}")
        remaining /= ATEMPO_MAX

    if remaining > 1.0:
        filters.append(f"atempo={remaining:.1f}")

    return ",".join(filters)


def _build_portrait_video_filter(
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int,
    blur_radius: int,
    hdr_filter: str,
    fade_filters: str,
    stabilize_filter: str = "",
    lut_filter: str = "",
    lut_before_hdr: bool = False,
) -> str:
    """세로 영상용 filter_complex 문자열 생성.

    기본: stabilize → HDR → split → blur → overlay → LUT → fade
    before: stabilize → LUT → HDR → split → blur → overlay → fade
    """
    fg_height = target_height
    fg_width = int(source_width * (fg_height / source_height))

    # 입력 체인 구성. filter(None, [...])로 빈 문자열("")을 제거하여
    # 선택적 필터(stabilize, lut, hdr)가 없을 때 쉼표가 남지 않게 한다.
    if lut_before_hdr:
        split_input = ",".join(
            filter(None, [stabilize_filter, lut_filter, hdr_filter, "split=2[bg][fg]"])
        )
    else:
        split_input = ",".join(filter(None, [stabilize_filter, hdr_filter, "split=2[bg][fg]"]))

    # 배경: 스케일 → crop → blur
    bg_chain = (
        f"[bg]scale={target_width}:{target_height}:force_original_aspect_ratio=increase,"
        f"crop={target_width}:{target_height},"
        f"boxblur={blur_radius}:1[bg_blur]"
    )

    # 전경: 높이에 맞춰 스케일
    fg_chain = f"[fg]scale={fg_width}:{fg_height}[fg_scaled]"

    # 합성: 중앙 오버레이 + (LUT after) + (fade)
    overlay = "[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2"
    if not lut_before_hdr:
        merge_chain = ",".join(filter(None, [overlay, lut_filter, fade_filters]))
    else:
        merge_chain = ",".join(filter(None, [overlay, fade_filters]))

    return f"[0:v]{split_input};{bg_chain};{fg_chain};{merge_chain}[v_out]"


def _build_landscape_video_filter(
    target_width: int,
    target_height: int,
    hdr_filter: str,
    fade_filters: str,
    stabilize_filter: str = "",
    lut_filter: str = "",
    lut_before_hdr: bool = False,
) -> str:
    """가로 영상용 -vf 필터 문자열 생성.

    기본: stabilize → HDR → scale+pad → LUT → fade
    before: stabilize → LUT → HDR → scale+pad → fade
    """
    scale_pad = (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=decrease,"
        f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2"
    )

    if lut_before_hdr:
        chain = [stabilize_filter, lut_filter, hdr_filter, scale_pad, fade_filters]
    else:
        chain = [stabilize_filter, hdr_filter, scale_pad, lut_filter, fade_filters]
    parts = [p for p in chain if p]
    return f"[0:v]{','.join(parts)}[v_out]"


def create_combined_filter(
    source_width: int,
    source_height: int,
    total_duration: float,
    is_portrait: bool,
    target_width: int = 3840,
    target_height: int = 2160,
    fade_duration: float = 0.5,
    fade_in_duration: float | None = None,
    fade_out_duration: float | None = None,
    blur_radius: int = PORTRAIT_BLUR_RADIUS,
    color_transfer: str | None = None,
    stabilize_filter: str = "",
    denoise: bool = False,
    denoise_level: str = "medium",
    silence_remove: str = "",
    loudnorm_analysis: LoudnormAnalysis | None = None,
    lut_path: str | None = None,
    lut_before_hdr: bool = False,
) -> tuple[str, str]:
    """영상·오디오 필터를 결합하여 최종 FFmpeg ``-filter_complex`` 문자열을 생성한다.

    **비디오 필터 체인 흐름**::

        입력 → [stabilize] → [HDR→SDR] → [세로: split→blur+overlay / 가로: scale+pad]
        → [LUT] → [fade] → 출력

    ``lut_before_hdr=True`` 일 때::

        입력 → [stabilize] → [LUT] → [HDR→SDR] → [...] → [fade] → 출력

    **오디오 필터 체인 흐름** (``create_audio_filter_chain`` 참조)::

        입력 → [denoise(afftdn)] → [fade in/out] → [loudnorm 2nd pass] → 출력

    Args:
        source_width: 원본 영상 너비
        source_height: 원본 영상 높이
        total_duration: 영상 전체 길이 (초)
        is_portrait: 세로 영상 여부
        target_width: 타겟 너비 (기본: 3840)
        target_height: 타겟 높이 (기본: 2160)
        fade_duration: 페이드 기본 지속 시간 (기본: 0.5초)
        fade_in_duration: Fade In 지속 시간 (None이면 fade_duration 사용)
        fade_out_duration: Fade Out 지속 시간 (None이면 fade_duration 사용)
        blur_radius: 배경 블러 반경 (기본: 20)
        color_transfer: 소스 영상의 color_transfer (HDR 변환 판단용)
        stabilize_filter: vidstabtransform 필터 문자열 (빈 문자열이면 미적용)
        denoise: 오디오 노이즈 제거 활성화 여부
        denoise_level: 노이즈 제거 강도 (light/medium/heavy)
        silence_remove: 무음 구간 제거 필터 문자열
        loudnorm_analysis: EBU R128 loudnorm 분석 결과 (None이면 미적용)
        lut_path: LUT 파일 경로 (None이면 미적용)
        lut_before_hdr: LUT를 HDR 변환 앞에 적용할지 여부

    Returns:
        (video_filter, audio_filter) 튜플
    """
    effective_fade_in = fade_duration if fade_in_duration is None else fade_in_duration
    effective_fade_out = fade_duration if fade_out_duration is None else fade_out_duration
    effective_fade_in, effective_fade_out, fade_out_start = _calculate_fade_params(
        total_duration,
        effective_fade_in,
        effective_fade_out,
    )
    hdr_filter = create_hdr_to_sdr_filter(color_transfer)

    # LUT 필터 생성
    lut_filter_str = ""
    if lut_path:
        lut_filter_str = create_lut_filter(lut_path)

    fade_filters = ""
    if effective_fade_in > 0:
        fade_filters = f"fade=t=in:st=0:d={effective_fade_in}"
    if effective_fade_out > 0:
        fade_out_filter = f"fade=t=out:st={fade_out_start}:d={effective_fade_out}"
        fade_filters = ",".join(filter(None, [fade_filters, fade_out_filter]))

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
            lut_filter=lut_filter_str,
            lut_before_hdr=lut_before_hdr,
        )
    else:
        video_filter = _build_landscape_video_filter(
            target_width,
            target_height,
            hdr_filter,
            fade_filters,
            stabilize_filter,
            lut_filter=lut_filter_str,
            lut_before_hdr=lut_before_hdr,
        )

    audio_filter = create_audio_filter_chain(
        total_duration=total_duration,
        fade_duration=fade_duration,
        fade_in_duration=fade_in_duration,
        fade_out_duration=fade_out_duration,
        denoise=denoise,
        denoise_level=denoise_level,
        silence_remove=silence_remove,
        loudnorm_analysis=loudnorm_analysis,
    )
    return video_filter, audio_filter
