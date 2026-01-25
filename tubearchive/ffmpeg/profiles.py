"""기기별 FFmpeg 인코딩 프로파일."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EncodingProfile:
    """인코딩 프로파일."""

    name: str
    video_codec: str
    video_bitrate: str
    pixel_format: str
    audio_codec: str
    audio_bitrate: str
    frame_rate: str = "30000/1001"  # 29.97fps (NTSC 표준, concat 호환성)
    color_primaries: str | None = None
    color_transfer: str | None = None
    color_space: str | None = None
    extra_args: tuple[str, ...] = ()

    def to_ffmpeg_args(self) -> list[str]:
        """FFmpeg 인자 목록 생성."""
        args = [
            "-c:v", self.video_codec,
            "-b:v", self.video_bitrate,
            "-pix_fmt", self.pixel_format,
            "-r", self.frame_rate,
            "-c:a", self.audio_codec,
            "-b:a", self.audio_bitrate,
        ]

        if self.color_primaries:
            args.extend(["-color_primaries", self.color_primaries])
        if self.color_transfer:
            args.extend(["-color_trc", self.color_transfer])
        if self.color_space:
            args.extend(["-colorspace", self.color_space])

        args.extend(self.extra_args)
        return args


# 기본 4K HEVC VideoToolbox 프로파일 (10-bit 통일)
PROFILE_4K_HEVC_VT = EncodingProfile(
    name="4K HEVC VideoToolbox",
    video_codec="hevc_videotoolbox",
    video_bitrate="50M",
    pixel_format="p010le",
    audio_codec="aac",
    audio_bitrate="256k",
    extra_args=("-tag:v", "hvc1", "-color_range", "tv"),
)

# Nikon N-Log (Rec.2020, HDR, 10-bit)
PROFILE_NIKON_NLOG = EncodingProfile(
    name="Nikon N-Log",
    video_codec="hevc_videotoolbox",
    video_bitrate="50M",
    pixel_format="p010le",
    audio_codec="aac",
    audio_bitrate="256k",
    color_primaries="bt2020",
    color_transfer="smpte2084",
    color_space="bt2020nc",
    extra_args=("-tag:v", "hvc1", "-color_range", "tv"),
)

# iPhone (SDR, Rec.709, 10-bit로 변환하여 통일)
PROFILE_IPHONE = EncodingProfile(
    name="iPhone",
    video_codec="hevc_videotoolbox",
    video_bitrate="40M",
    pixel_format="p010le",
    audio_codec="aac",
    audio_bitrate="256k",
    color_primaries="bt709",
    color_transfer="bt709",
    color_space="bt709",
    extra_args=("-tag:v", "hvc1", "-color_range", "tv"),
)

# GoPro (SDR, Rec.709, 10-bit로 변환하여 통일)
PROFILE_GOPRO = EncodingProfile(
    name="GoPro",
    video_codec="hevc_videotoolbox",
    video_bitrate="50M",
    pixel_format="p010le",
    audio_codec="aac",
    audio_bitrate="256k",
    extra_args=("-tag:v", "hvc1", "-color_range", "tv"),
)

# DJI (SDR, Rec.709, 10-bit로 변환하여 통일)
PROFILE_DJI = EncodingProfile(
    name="DJI",
    video_codec="hevc_videotoolbox",
    video_bitrate="50M",
    pixel_format="p010le",
    audio_codec="aac",
    audio_bitrate="256k",
    extra_args=("-tag:v", "hvc1", "-color_range", "tv"),
)

# libx265 폴백 (VideoToolbox 실패 시, 10-bit)
PROFILE_FALLBACK_LIBX265 = EncodingProfile(
    name="libx265 Fallback",
    video_codec="libx265",
    video_bitrate="50M",
    pixel_format="yuv420p10le",
    audio_codec="aac",
    audio_bitrate="256k",
    extra_args=("-preset", "medium", "-tag:v", "hvc1", "-color_range", "tv"),
)


def select_profile(
    device_model: str | None,
    color_transfer: str | None,
    color_space: str | None,
) -> EncodingProfile:
    """
    기기 모델과 컬러 정보를 기반으로 프로파일 선택.

    Args:
        device_model: 기기 모델명
        color_transfer: 컬러 전송 특성
        color_space: 컬러 스페이스

    Returns:
        적합한 EncodingProfile
    """
    device_upper = (device_model or "").upper()

    # Nikon N-Log 감지 (HDR)
    if "NIKON" in device_upper:
        if color_transfer == "smpte2084" or color_space == "bt2020nc":
            return PROFILE_NIKON_NLOG
        return PROFILE_4K_HEVC_VT

    # iPhone 감지
    if "IPHONE" in device_upper:
        return PROFILE_IPHONE

    # GoPro 감지
    if "GOPRO" in device_upper:
        return PROFILE_GOPRO

    # DJI 감지
    if "DJI" in device_upper:
        return PROFILE_DJI

    # 기본 프로파일
    return PROFILE_4K_HEVC_VT


def get_fallback_profile() -> EncodingProfile:
    """VideoToolbox 실패 시 폴백 프로파일 반환."""
    return PROFILE_FALLBACK_LIBX265
