"""메타데이터 감지기 테스트."""

from pathlib import Path
from unittest.mock import patch

import pytest

from tubearchive.core.detector import detect_metadata


class TestDetector:
    """메타데이터 감지기 테스트."""

    @pytest.fixture
    def sample_ffprobe_output(self) -> dict:
        """샘플 ffprobe JSON 출력."""
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "pix_fmt": "yuv420p10le",
                    "r_frame_rate": "60/1",
                    "avg_frame_rate": "60/1",
                    "duration": "120.5",
                    "tags": {
                        "rotate": "0",
                    },
                }
            ],
            "format": {
                "duration": "120.5",
                "tags": {},
            },
        }

    @pytest.fixture
    def portrait_ffprobe_output(self) -> dict:
        """세로 영상 ffprobe 출력."""
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1080,
                    "height": 1920,
                    "pix_fmt": "yuv420p",
                    "r_frame_rate": "30/1",
                    "avg_frame_rate": "30/1",
                    "duration": "60.0",
                }
            ],
            "format": {
                "duration": "60.0",
                "tags": {
                    "com.apple.quicktime.model": "iPhone 14 Pro",
                },
            },
        }

    @pytest.fixture
    def vfr_ffprobe_output(self) -> dict:
        """VFR 영상 ffprobe 출력."""
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "pix_fmt": "yuv420p",
                    "r_frame_rate": "60/1",
                    "avg_frame_rate": "59/1",  # 다름 → VFR
                    "duration": "90.0",
                }
            ],
            "format": {
                "duration": "90.0",
                "tags": {},
            },
        }

    @pytest.fixture
    def nikon_nlog_output(self) -> dict:
        """Nikon N-Log 영상 ffprobe 출력."""
        return {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "pix_fmt": "yuv420p10le",
                    "r_frame_rate": "60/1",
                    "avg_frame_rate": "60/1",
                    "duration": "120.0",
                    "color_space": "bt2020nc",
                    "color_transfer": "smpte2084",
                    "color_primaries": "bt2020",
                }
            ],
            "format": {
                "duration": "120.0",
                "tags": {
                    "com.apple.quicktime.model": "NIKON Z 8",
                },
            },
        }

    def test_detect_landscape_video(self, sample_ffprobe_output: dict, tmp_path: Path) -> None:
        """가로 영상 메타데이터 감지."""
        video_file = tmp_path / "test.mp4"
        video_file.write_text("")

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = sample_ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.width == 3840
        assert metadata.height == 2160
        assert metadata.duration_seconds == 120.5
        assert metadata.fps == 60.0
        assert metadata.codec == "hevc"
        assert metadata.pixel_format == "yuv420p10le"
        assert metadata.is_portrait is False
        assert metadata.is_vfr is False

    def test_detect_portrait_video(self, portrait_ffprobe_output: dict, tmp_path: Path) -> None:
        """세로 영상 감지."""
        video_file = tmp_path / "test.mov"
        video_file.write_text("")

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = portrait_ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.width == 1080
        assert metadata.height == 1920
        assert metadata.is_portrait is True
        assert metadata.device_model == "iPhone 14 Pro"

    def test_detect_vfr(self, vfr_ffprobe_output: dict, tmp_path: Path) -> None:
        """VFR 감지."""
        video_file = tmp_path / "test.mp4"
        video_file.write_text("")

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = vfr_ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.is_vfr is True

    def test_detect_nikon_nlog(self, nikon_nlog_output: dict, tmp_path: Path) -> None:
        """Nikon N-Log 감지."""
        video_file = tmp_path / "test.mov"
        video_file.write_text("")

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = nikon_nlog_output

            metadata = detect_metadata(video_file)

        assert metadata.device_model == "NIKON Z 8"
        assert metadata.color_space == "bt2020nc"
        assert metadata.color_transfer == "smpte2084"
        assert metadata.color_primaries == "bt2020"

    def test_rotation_metadata(self, tmp_path: Path) -> None:
        """회전 메타데이터 처리."""
        video_file = tmp_path / "test.mov"
        video_file.write_text("")

        # 90도 회전된 영상 (가로로 촬영했지만 세로로 저장)
        ffprobe_output = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "pix_fmt": "yuv420p",
                    "r_frame_rate": "30/1",
                    "avg_frame_rate": "30/1",
                    "duration": "60.0",
                    "tags": {
                        "rotate": "90",
                    },
                }
            ],
            "format": {
                "duration": "60.0",
                "tags": {},
            },
        }

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = ffprobe_output

            metadata = detect_metadata(video_file)

        # 회전 고려하여 세로 영상으로 인식
        assert metadata.is_portrait is True

    def test_detect_has_audio_true(self, tmp_path: Path) -> None:
        """오디오 스트림이 있는 영상은 has_audio=True."""
        video_file = tmp_path / "test.mp4"
        video_file.write_text("")

        ffprobe_output = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "pix_fmt": "yuv420p10le",
                    "r_frame_rate": "30/1",
                    "avg_frame_rate": "30/1",
                    "duration": "60.0",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "sample_rate": "48000",
                    "channels": 2,
                },
            ],
            "format": {
                "duration": "60.0",
                "tags": {},
            },
        }

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.has_audio is True

    def test_detect_has_audio_false(self, tmp_path: Path) -> None:
        """오디오 스트림이 없는 DJI 영상은 has_audio=False."""
        video_file = tmp_path / "test.mp4"
        video_file.write_text("")

        # DJI 영상: 비디오 + data 스트림만, 오디오 없음
        ffprobe_output = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "hevc",
                    "width": 3840,
                    "height": 2160,
                    "pix_fmt": "yuv420p10le",
                    "r_frame_rate": "30/1",
                    "avg_frame_rate": "30/1",
                    "duration": "120.0",
                },
                {
                    "codec_type": "data",
                    "codec_name": "none",
                    "tags": {"handler_name": "CAM meta"},
                },
                {
                    "codec_type": "data",
                    "codec_name": "none",
                    "tags": {"handler_name": "CAM dbgi"},
                },
            ],
            "format": {
                "duration": "120.0",
                "tags": {},
            },
        }

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.has_audio is False

    def test_video_only_no_audio_default(self, sample_ffprobe_output: dict, tmp_path: Path) -> None:
        """비디오만 있고 오디오 스트림 없는 기본 fixture는 has_audio=False."""
        video_file = tmp_path / "test.mp4"
        video_file.write_text("")

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = sample_ffprobe_output

            metadata = detect_metadata(video_file)

        # sample_ffprobe_output에는 오디오 스트림이 없다
        assert metadata.has_audio is False

    def test_detects_location_from_iso6709_format_tag(
        self, sample_ffprobe_output: dict, tmp_path: Path
    ) -> None:
        """ISO6709 좌표 태그에서 위치를 추출."""
        video_file = tmp_path / "test.mp4"
        video_file.write_text("")

        sample_ffprobe_output["format"]["tags"] = {
            "com.apple.quicktime.location.ISO6709": "+37.566500+126.978000/"
        }

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = sample_ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.location == "37.566500, 126.978000"
        assert metadata.location_latitude == 37.5665
        assert metadata.location_longitude == 126.9780

    def test_detects_location_from_stream_nsew_tag(
        self, sample_ffprobe_output: dict, tmp_path: Path
    ) -> None:
        """스트림 태그의 N/S/E/W 형식 좌표를 추출."""
        video_file = tmp_path / "test.mp4"
        video_file.write_text("")

        sample_ffprobe_output["streams"][0]["tags"] = {
            "com.apple.quicktime.location": "N 37.5665, E 126.9780"
        }
        sample_ffprobe_output["format"]["tags"] = {}

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = sample_ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.location == "37.566500, 126.978000"
        assert metadata.location_latitude == 37.5665
        assert metadata.location_longitude == 126.9780

    def test_detects_non_coordinate_location_text(
        self, sample_ffprobe_output: dict, tmp_path: Path
    ) -> None:
        """좌표가 아닌 위치 텍스트는 원문을 반환."""
        video_file = tmp_path / "test.mp4"
        video_file.write_text("")

        sample_ffprobe_output["format"]["tags"] = {
            "location": "Seoul Downtown",
            "quicktime:location": "ignored",
        }

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = sample_ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.location == "Seoul Downtown"
        assert metadata.location_latitude is None
        assert metadata.location_longitude is None

    def test_detects_location_from_srt_sidecar(
        self, sample_ffprobe_output: dict, tmp_path: Path
    ) -> None:
        """SRT sidecar에서 좌표 문자열을 추출."""
        video_file = tmp_path / "test.mp4"
        video_file.write_text("")
        sidecar = tmp_path / "test.srt"
        sidecar.write_text("1\n00:00:00,000 --> 00:00:01,000\n+37.566500+126.978000/\n")

        sample_ffprobe_output["format"]["tags"] = {}
        sample_ffprobe_output["streams"][0]["tags"] = {}

        with patch("tubearchive.core.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = sample_ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.location == "37.566500, 126.978000"
        assert metadata.location_latitude == 37.5665
        assert metadata.location_longitude == 126.9780
