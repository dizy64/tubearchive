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
