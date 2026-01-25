"""메타데이터 기반 FFmpeg 인코딩 프로파일."""

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
            "-c:v",
            self.video_codec,
            "-b:v",
            self.video_bitrate,
            "-pix_fmt",
            self.pixel_format,
            "-r",
            self.frame_rate,
            "-c:a",
            self.audio_codec,
            "-b:a",
            self.audio_bitrate,
        ]

        if self.color_primaries:
            args.extend(["-color_primaries", self.color_primaries])
        if self.color_transfer:
            args.extend(["-color_trc", self.color_transfer])
        if self.color_space:
            args.extend(["-colorspace", self.color_space])

        args.extend(self.extra_args)
        return args


# =============================================================================
# 메타데이터 기반 프로파일 (디바이스 무관)
# =============================================================================

# SDR 프로파일 (BT.709)
PROFILE_SDR = EncodingProfile(
    name="SDR (BT.709)",
    video_codec="hevc_videotoolbox",
    video_bitrate="50M",
    pixel_format="p010le",
    audio_codec="aac",
    audio_bitrate="256k",
    color_primaries="bt709",
    color_transfer="bt709",
    color_space="bt709",
    extra_args=("-tag:v", "hvc1", "-color_range", "tv"),
)

# HDR HLG 프로파일 (BT.2020 + HLG)
PROFILE_HDR_HLG = EncodingProfile(
    name="HDR HLG (BT.2020)",
    video_codec="hevc_videotoolbox",
    video_bitrate="50M",
    pixel_format="p010le",
    audio_codec="aac",
    audio_bitrate="256k",
    color_primaries="bt2020",
    color_transfer="arib-std-b67",  # HLG
    color_space="bt2020nc",
    extra_args=("-tag:v", "hvc1", "-color_range", "tv"),
)

# HDR PQ 프로파일 (BT.2020 + PQ/HDR10)
PROFILE_HDR_PQ = EncodingProfile(
    name="HDR PQ (BT.2020)",
    video_codec="hevc_videotoolbox",
    video_bitrate="50M",
    pixel_format="p010le",
    audio_codec="aac",
    audio_bitrate="256k",
    color_primaries="bt2020",
    color_transfer="smpte2084",  # PQ
    color_space="bt2020nc",
    extra_args=("-tag:v", "hvc1", "-color_range", "tv"),
)

# libx265 폴백 (VideoToolbox 실패 시)
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
    color_transfer: str | None,
    color_space: str | None,
) -> EncodingProfile:
    """
    영상 메타데이터를 기반으로 프로파일 선택.

    디바이스에 상관없이 영상의 실제 색공간 특성에 맞는 프로파일을 반환합니다.

    Args:
        color_transfer: 컬러 전송 특성 (예: bt709, arib-std-b67, smpte2084)
        color_space: 컬러 스페이스 (예: bt709, bt2020nc)

    Returns:
        적합한 EncodingProfile
    """
    # HDR HLG 감지 (아이폰 HDR, 일부 카메라)
    if color_transfer == "arib-std-b67":
        return PROFILE_HDR_HLG

    # HDR PQ/HDR10 감지 (Nikon N-Log, Sony S-Log, 일부 HDR 카메라)
    if color_transfer == "smpte2084":
        return PROFILE_HDR_PQ

    # BT.2020 색공간이지만 transfer가 불명확한 경우 → HLG로 처리
    if color_space in ("bt2020nc", "bt2020c"):
        return PROFILE_HDR_HLG

    # 기본값: SDR (BT.709)
    return PROFILE_SDR


def get_fallback_profile() -> EncodingProfile:
    """VideoToolbox 실패 시 폴백 프로파일 반환."""
    return PROFILE_FALLBACK_LIBX265
