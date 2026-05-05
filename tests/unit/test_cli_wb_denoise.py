"""CLI 화이트밸런스 / 영상 노이즈 제거 옵션 검증 테스트."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch

import pytest

from tubearchive.app.cli.validators import validate_args
from tubearchive.infra.ffmpeg.constants import WB_PRESETS


def _make_args(**kwargs: object) -> argparse.Namespace:
    """최소 필수 필드만 포함한 Namespace를 만든다.
    나머지는 validate_args 내부의 getattr 기본값에 맡긴다.
    """
    defaults: dict[str, object] = {
        "targets": [],
        "output": None,
        "output_dir": None,
        "parallel": None,
        "no_resume": False,
        "keep_temp": False,
        "dry_run": False,
        # WB / video_denoise 관련 기본값
        "video_denoise": False,
        "video_denoise_level": None,
        "wb_preset": None,
        "wb_kelvin": None,
        "auto_white_balance": None,
        "no_auto_white_balance": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


@pytest.fixture()
def valid_args(tmp_path: Path) -> argparse.Namespace:
    target = tmp_path / "video.mp4"
    target.write_bytes(b"fake")
    return _make_args(targets=[str(target)])


class TestVideoDenoise:
    """--video-denoise / --video-denoise-level 검증."""

    def test_video_denoise_default_off(self, valid_args: argparse.Namespace) -> None:
        result = validate_args(valid_args)
        assert result.video_denoise is False
        assert result.video_denoise_strength == "medium"

    def test_video_denoise_flag_enables(self, valid_args: argparse.Namespace) -> None:
        valid_args.video_denoise = True
        result = validate_args(valid_args)
        assert result.video_denoise is True

    def test_video_denoise_level_implies_enable(self, valid_args: argparse.Namespace) -> None:
        valid_args.video_denoise_level = "heavy"
        result = validate_args(valid_args)
        assert result.video_denoise is True
        assert result.video_denoise_strength == "heavy"

    def test_video_denoise_level_light(self, valid_args: argparse.Namespace) -> None:
        valid_args.video_denoise = True
        valid_args.video_denoise_level = "light"
        result = validate_args(valid_args)
        assert result.video_denoise_strength == "light"

    @patch("tubearchive.app.cli.validators.get_default_video_denoise", return_value=True)
    @patch("tubearchive.app.cli.validators.get_default_video_denoise_level", return_value="heavy")
    def test_env_fallback(
        self, _mock_level: object, _mock_flag: object, valid_args: argparse.Namespace
    ) -> None:
        result = validate_args(valid_args)
        assert result.video_denoise is True
        assert result.video_denoise_strength == "heavy"


class TestWbPreset:
    """--wb-preset 검증."""

    def test_wb_preset_daylight(self, valid_args: argparse.Namespace) -> None:
        valid_args.wb_preset = "daylight"
        result = validate_args(valid_args)
        assert result.wb_kelvin == WB_PRESETS["daylight"]

    def test_wb_preset_cloudy(self, valid_args: argparse.Namespace) -> None:
        valid_args.wb_preset = "cloudy"
        result = validate_args(valid_args)
        assert result.wb_kelvin == WB_PRESETS["cloudy"]

    def test_invalid_preset_raises(self, valid_args: argparse.Namespace) -> None:
        valid_args.wb_preset = "invalid_preset"
        with pytest.raises(ValueError, match="wb-preset"):
            validate_args(valid_args)

    def test_no_wb_default_none(self, valid_args: argparse.Namespace) -> None:
        result = validate_args(valid_args)
        assert result.wb_kelvin is None


class TestWbKelvin:
    """--wb-kelvin 검증."""

    def test_wb_kelvin_valid(self, valid_args: argparse.Namespace) -> None:
        valid_args.wb_kelvin = 5500
        result = validate_args(valid_args)
        assert result.wb_kelvin == 5500

    def test_wb_kelvin_overrides_preset(self, valid_args: argparse.Namespace) -> None:
        valid_args.wb_preset = "daylight"
        valid_args.wb_kelvin = 4000
        result = validate_args(valid_args)
        assert result.wb_kelvin == 4000

    def test_wb_kelvin_out_of_range_raises(self, valid_args: argparse.Namespace) -> None:
        valid_args.wb_kelvin = 999
        with pytest.raises(ValueError, match="wb-kelvin"):
            validate_args(valid_args)

    def test_wb_kelvin_upper_bound_raises(self, valid_args: argparse.Namespace) -> None:
        valid_args.wb_kelvin = 40001
        with pytest.raises(ValueError, match="wb-kelvin"):
            validate_args(valid_args)

    def test_wb_kelvin_boundary_valid(self, valid_args: argparse.Namespace) -> None:
        valid_args.wb_kelvin = 1000
        result = validate_args(valid_args)
        assert result.wb_kelvin == 1000

        valid_args.wb_kelvin = 40000
        result = validate_args(valid_args)
        assert result.wb_kelvin == 40000


class TestAutoWhiteBalance:
    """--auto-wb / --no-auto-wb 검증."""

    def test_auto_wb_default_off(self, valid_args: argparse.Namespace) -> None:
        result = validate_args(valid_args)
        assert result.auto_white_balance is False

    def test_auto_wb_flag(self, valid_args: argparse.Namespace) -> None:
        valid_args.auto_white_balance = True
        result = validate_args(valid_args)
        assert result.auto_white_balance is True

    def test_no_auto_wb_overrides_env(self, valid_args: argparse.Namespace) -> None:
        valid_args.no_auto_white_balance = True
        with patch(
            "tubearchive.app.cli.validators.get_default_auto_white_balance", return_value=True
        ):
            result = validate_args(valid_args)
        assert result.auto_white_balance is False

    @patch("tubearchive.app.cli.validators.get_default_auto_white_balance", return_value=True)
    def test_env_auto_wb_fallback(self, _mock: object, valid_args: argparse.Namespace) -> None:
        result = validate_args(valid_args)
        assert result.auto_white_balance is True


class TestResolveAutoWb:
    """_resolve_auto_wb() 단위 테스트."""

    def test_matches_device_model(self) -> None:
        from tubearchive.domain.media.transcoder import _resolve_auto_wb

        result = _resolve_auto_wb("GoPro HERO12", {"gopro": "cloudy"})
        assert result == WB_PRESETS["cloudy"]

    def test_case_insensitive(self) -> None:
        from tubearchive.domain.media.transcoder import _resolve_auto_wb

        result = _resolve_auto_wb("NIKON Z6III", {"nikon": "daylight"})
        assert result == WB_PRESETS["daylight"]

    def test_longest_keyword_wins(self) -> None:
        from tubearchive.domain.media.transcoder import _resolve_auto_wb

        result = _resolve_auto_wb(
            "GoPro HERO12",
            {"go": "tungsten", "gopro": "cloudy"},
        )
        assert result == WB_PRESETS["cloudy"]

    def test_no_match_returns_none(self) -> None:
        from tubearchive.domain.media.transcoder import _resolve_auto_wb

        result = _resolve_auto_wb("Sony A7IV", {"nikon": "daylight"})
        assert result is None

    def test_empty_device_model_returns_none(self) -> None:
        from tubearchive.domain.media.transcoder import _resolve_auto_wb

        result = _resolve_auto_wb("", {"gopro": "cloudy"})
        assert result is None

    def test_empty_device_wb_returns_none(self) -> None:
        from tubearchive.domain.media.transcoder import _resolve_auto_wb

        result = _resolve_auto_wb("GoPro HERO12", {})
        assert result is None
