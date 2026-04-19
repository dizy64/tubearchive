"""메타데이터 감지기 테스트."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tubearchive.domain.media.detector import (
    _build_device_name,
    _extract_device_model,
    _normalize_apple_model,
    detect_metadata,
)


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

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
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

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
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

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = vfr_ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.is_vfr is True

    def test_detect_nikon_nlog(self, nikon_nlog_output: dict, tmp_path: Path) -> None:
        """Nikon N-Log 감지."""
        video_file = tmp_path / "test.mov"
        video_file.write_text("")

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
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

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
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

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
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

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.has_audio is False

    def test_video_only_no_audio_default(self, sample_ffprobe_output: dict, tmp_path: Path) -> None:
        """비디오만 있고 오디오 스트림 없는 기본 fixture는 has_audio=False."""
        video_file = tmp_path / "test.mp4"
        video_file.write_text("")

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
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

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
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

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
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

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
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

        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe:
            mock_ffprobe.return_value = sample_ffprobe_output

            metadata = detect_metadata(video_file)

        assert metadata.location == "37.566500, 126.978000"
        assert metadata.location_latitude == 37.5665
        assert metadata.location_longitude == 126.9780


class TestExtractDeviceModel:
    """_extract_device_model 다중 소스 기기 감지 테스트."""

    def _probe(
        self,
        format_tags: dict | None = None,
        video_handler: str | None = None,
    ) -> dict:
        stream_tags = {}
        if video_handler is not None:
            stream_tags["handler_name"] = video_handler
        return {
            "streams": [{"codec_type": "video", "tags": stream_tags}],
            "format": {"tags": format_tags or {}},
        }

    def test_apple_quicktime_model(self) -> None:
        probe = self._probe({"com.apple.quicktime.model": "iPhone 17 Pro"})
        assert _extract_device_model(probe, Path("IMG_0001.MOV")) == "iPhone 17 Pro"

    def test_apple_model_strips_whitespace(self) -> None:
        probe = self._probe({"com.apple.quicktime.model": "  iPhone 14 Pro  "})
        assert _extract_device_model(probe, Path("IMG_0001.MOV")) == "iPhone 14 Pro"

    def test_dji_encoder_tag(self) -> None:
        probe = self._probe({"encoder": "DJI OsmoPocket3"})
        assert _extract_device_model(probe, Path("DJI_20250118.MP4")) == "DJI OsmoPocket3"

    def test_dji_case_insensitive(self) -> None:
        probe = self._probe({"encoder": "dji Mini 4 Pro"})
        assert _extract_device_model(probe, Path("DJI_0001.MP4")) == "dji Mini 4 Pro"

    def test_gopro_firmware_hero9(self) -> None:
        probe = self._probe({"firmware": "HD9.01.01.72.00"})
        assert _extract_device_model(probe, Path("GH010001.MP4")) == "GoPro HERO 9"

    def test_gopro_firmware_hero8(self) -> None:
        probe = self._probe({"firmware": "HD8.01.02.50.00"})
        assert _extract_device_model(probe, Path("GH010001.MP4")) == "GoPro HERO 8"

    def test_gopro_handler_fallback(self) -> None:
        probe = self._probe(video_handler="GoPro AVC  ")
        assert _extract_device_model(probe, Path("GH010001.MP4")) == "GoPro"

    def test_gopro_handler_case_insensitive(self) -> None:
        probe = self._probe(video_handler="gopro H.265")
        assert _extract_device_model(probe, Path("GX010001.MP4")) == "GoPro"

    def test_apple_prefix_stripped(self) -> None:
        """Blackmagic Camera 앱 촬영 파일: 'Apple ' 접두사 제거."""
        probe = self._probe({"com.apple.quicktime.model": "Apple iPhone 17 Pro Max 100mm"})
        assert _extract_device_model(probe, Path("A001_C001.mov")) == "iPhone 17 Pro Max"

    def test_apple_lens_suffix_stripped(self) -> None:
        probe = self._probe({"com.apple.quicktime.model": "Apple iPhone 17 Pro Max 24mm"})
        assert _extract_device_model(probe, Path("A001_C002.mov")) == "iPhone 17 Pro Max"

    def test_nikon_via_exiftool(self) -> None:
        """Nikon DSC_ 파일은 exiftool로 감지한다 (파일명 휴리스틱 제거됨)."""
        probe = self._probe()
        exif_result = [{"SourceFile": "...", "Make": "NIKON CORPORATION", "Model": "NIKON Z 8"}]
        with patch("tubearchive.domain.media.detector.subprocess.run") as mock_run:
            mock_run.return_value.stdout = json.dumps(exif_result)
            mock_run.return_value.returncode = 0
            result = _extract_device_model(probe, Path("DSC_4885.MOV"))
        assert result == "Nikon Z 8"

    def test_unknown_returns_none(self) -> None:
        """exiftool도 Make/Model 없으면 None."""
        probe = self._probe()
        exif_result = [{"SourceFile": "..."}]
        with patch("tubearchive.domain.media.detector.subprocess.run") as mock_run:
            mock_run.return_value.stdout = json.dumps(exif_result)
            mock_run.return_value.returncode = 0
            result = _extract_device_model(probe, Path("unknown.mp4"))
        assert result is None

    def test_apple_priority_over_dji(self) -> None:
        probe = self._probe(
            {
                "com.apple.quicktime.model": "iPhone 17 Pro",
                "encoder": "DJI OsmoPocket3",
            }
        )
        assert _extract_device_model(probe, Path("IMG_0001.MOV")) == "iPhone 17 Pro"

    def test_dji_priority_over_gopro_firmware(self) -> None:
        probe = self._probe({"encoder": "DJI OsmoPocket3", "firmware": "HD9.01.01.72.00"})
        assert _extract_device_model(probe, Path("test.MP4")) == "DJI OsmoPocket3"

    def test_gopro_firmware_priority_over_handler(self) -> None:
        probe = self._probe(
            format_tags={"firmware": "HD9.01.01.72.00"},
            video_handler="GoPro AVC  ",
        )
        assert _extract_device_model(probe, Path("GH010001.MP4")) == "GoPro HERO 9"

    def test_detect_metadata_gopro(self, tmp_path: Path) -> None:
        video_file = tmp_path / "GH010001.MP4"
        video_file.write_text("")
        probe_data = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 3840,
                    "height": 2160,
                    "pix_fmt": "yuv420p",
                    "r_frame_rate": "60/1",
                    "avg_frame_rate": "60/1",
                    "duration": "30.0",
                    "tags": {"handler_name": "GoPro AVC  "},
                }
            ],
            "format": {
                "duration": "30.0",
                "tags": {"firmware": "HD9.01.01.72.00"},
            },
        }
        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock:
            mock.return_value = probe_data
            metadata = detect_metadata(video_file)
        assert metadata.device_model == "GoPro HERO 9"

    def test_detect_metadata_dji(self, tmp_path: Path) -> None:
        video_file = tmp_path / "DJI_20250118.MP4"
        video_file.write_text("")
        probe_data = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "pix_fmt": "yuv420p",
                    "r_frame_rate": "30/1",
                    "avg_frame_rate": "30/1",
                    "duration": "20.0",
                    "tags": {},
                }
            ],
            "format": {
                "duration": "20.0",
                "tags": {"encoder": "DJI OsmoPocket3"},
            },
        }
        with patch("tubearchive.domain.media.detector._run_ffprobe") as mock:
            mock.return_value = probe_data
            metadata = detect_metadata(video_file)
        assert metadata.device_model == "DJI OsmoPocket3"

    def test_detect_metadata_nikon_via_exiftool(self, tmp_path: Path) -> None:
        """detect_metadata 통합: exiftool 기반 Nikon Z 8 감지."""
        video_file = tmp_path / "DSC_4885.MOV"
        video_file.write_text("")
        probe_data = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 3840,
                    "height": 2160,
                    "pix_fmt": "yuv420p",
                    "r_frame_rate": "60/1",
                    "avg_frame_rate": "60/1",
                    "duration": "45.0",
                    "tags": {},
                }
            ],
            "format": {
                "duration": "45.0",
                "tags": {},
            },
        }
        exif_result = [
            {"SourceFile": str(video_file), "Make": "NIKON CORPORATION", "Model": "NIKON Z 8"}
        ]
        with (
            patch("tubearchive.domain.media.detector._run_ffprobe") as mock_ffprobe,
            patch("tubearchive.domain.media.detector.subprocess.run") as mock_exif,
        ):
            mock_ffprobe.return_value = probe_data
            mock_exif.return_value.stdout = json.dumps(exif_result)
            mock_exif.return_value.returncode = 0
            metadata = detect_metadata(video_file)
        assert metadata.device_model == "Nikon Z 8"


class TestNormalizeAppleModel:
    """_normalize_apple_model 정규화 테스트."""

    def test_plain_iphone(self) -> None:
        assert _normalize_apple_model("iPhone 17 Pro") == "iPhone 17 Pro"

    def test_strips_apple_prefix(self) -> None:
        assert _normalize_apple_model("Apple iPhone 17 Pro Max") == "iPhone 17 Pro Max"

    def test_strips_apple_prefix_and_lens(self) -> None:
        assert _normalize_apple_model("Apple iPhone 17 Pro Max 100mm") == "iPhone 17 Pro Max"

    def test_strips_apple_prefix_24mm(self) -> None:
        assert _normalize_apple_model("Apple iPhone 17 Pro Max 24mm") == "iPhone 17 Pro Max"

    def test_no_change_without_prefix(self) -> None:
        assert _normalize_apple_model("iPhone 14 Pro") == "iPhone 14 Pro"

    def test_strips_whitespace(self) -> None:
        assert _normalize_apple_model("  iPhone 17 Pro  ") == "iPhone 17 Pro"


class TestBuildDeviceName:
    """_build_device_name Make+Model 결합 테스트."""

    def test_nikon(self) -> None:
        assert _build_device_name("NIKON CORPORATION", "NIKON Z 8") == "Nikon Z 8"

    def test_nikon_zfc(self) -> None:
        assert _build_device_name("NIKON CORPORATION", "NIKON Z fc") == "Nikon Z fc"

    def test_canon(self) -> None:
        assert _build_device_name("Canon", "Canon EOS R5") == "Canon EOS R5"

    def test_sony(self) -> None:
        # Sony model is internal code — keep as-is with normalized make
        result = _build_device_name("SONY", "ILCE-7M4")
        assert result == "Sony ILCE-7M4"

    def test_fujifilm(self) -> None:
        assert _build_device_name("FUJIFILM", "X-T5") == "Fujifilm X-T5"

    def test_gopro_no_make(self) -> None:
        """GoPro exiftool: Make 없이 Model만 "HERO9 Black"."""
        assert _build_device_name(None, "HERO9 Black") == "GoPro HERO9 Black"

    def test_none_model_returns_none(self) -> None:
        assert _build_device_name("NIKON CORPORATION", None) is None

    def test_both_none_returns_none(self) -> None:
        assert _build_device_name(None, None) is None

    def test_unknown_make_title_case(self) -> None:
        result = _build_device_name("ACME CORP", "Model X")
        assert result == "Acme Model X"
