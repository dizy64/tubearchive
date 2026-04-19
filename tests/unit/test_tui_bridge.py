"""TUI bridge 단위 테스트.

TuiOptionState → ValidatedArgs 변환 정확성과 기본값 일치를 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tubearchive.app.cli.main import ValidatedArgs
from tubearchive.app.tui.bridge import _to_path_or_none, build_validated_args
from tubearchive.app.tui.models import TuiOptionState, default_state

# ---------------------------------------------------------------------------
# _to_path_or_none 헬퍼 테스트
# ---------------------------------------------------------------------------


def test_to_path_or_none_empty() -> None:
    assert _to_path_or_none("") is None
    assert _to_path_or_none("   ") is None


def test_to_path_or_none_valid() -> None:
    p = _to_path_or_none("/tmp/test")
    assert p == Path("/tmp/test")


def test_to_path_or_none_tilde() -> None:
    p = _to_path_or_none("~/Videos")
    assert p is not None
    assert str(p).startswith("/")
    assert "Videos" in str(p)


# ---------------------------------------------------------------------------
# build_validated_args 기본 변환
# ---------------------------------------------------------------------------


def test_build_with_defaults() -> None:
    """기본 TuiOptionState가 ValidatedArgs 기본값과 일치하는지 확인한다."""
    targets = [Path("/tmp/test.mp4")]
    state = default_state()
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.targets == targets
    assert result.output is None
    assert result.output_dir is None
    assert result.dry_run is False
    assert result.no_resume is False
    assert result.keep_temp is False
    assert result.normalize_audio is False
    assert result.denoise is False
    assert result.denoise_level == "medium"
    assert result.group_sequences is True
    assert result.fade_duration == 0.5
    assert result.stabilize is False
    assert result.stabilize_strength == "medium"
    assert result.parallel == 1


def test_build_audio_flags() -> None:
    """오디오 관련 옵션이 올바르게 변환되는지 확인한다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(normalize_audio=True, denoise=True, denoise_level="heavy")
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.normalize_audio is True
    assert result.denoise is True
    assert result.denoise_level == "heavy"


def test_build_bgm_options() -> None:
    """BGM 옵션이 Path로 변환되는지 확인한다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(bgm_path="/tmp/bgm.mp3", bgm_volume=0.3, bgm_loop=True)
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.bgm_path == Path("/tmp/bgm.mp3")
    assert result.bgm_volume == 0.3
    assert result.bgm_loop is True


def test_build_bgm_empty_path() -> None:
    """BGM 경로가 빈 문자열이면 None으로 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(bgm_path="")
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.bgm_path is None


def test_build_stabilize_options() -> None:
    """영상 안정화 옵션이 올바르게 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(stabilize=True, stabilize_strength="heavy", stabilize_crop="expand")
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.stabilize is True
    assert result.stabilize_strength == "heavy"
    assert result.stabilize_crop == "expand"


def test_build_timelapse_valid_speed() -> None:
    """타임랩스 배속이 정수로 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(timelapse_speed="10", timelapse_audio=True, timelapse_resolution="1080p")
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.timelapse_speed == 10
    assert result.timelapse_audio is True
    assert result.timelapse_resolution == "1080p"


def test_build_timelapse_empty_speed() -> None:
    """타임랩스 배속이 빈 문자열이면 None으로 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(timelapse_speed="")
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.timelapse_speed is None


def test_build_timelapse_invalid_speed() -> None:
    """타임랩스 배속에 정수가 아닌 값이 입력되면 ValueError를 발생시킨다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(timelapse_speed="abc")
    with pytest.raises(ValueError, match="정수"):
        build_validated_args(targets, state)


def test_build_exclude_patterns() -> None:
    """쉼표 구분 패턴이 list로 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(exclude_patterns="*.tmp, test_*, .DS_Store")
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.exclude_patterns == ["*.tmp", "test_*", ".DS_Store"]


def test_build_empty_patterns() -> None:
    """빈 패턴 문자열은 None으로 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(exclude_patterns="", include_only_patterns="")
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.exclude_patterns is None
    assert result.include_only_patterns is None


def test_build_split_duration() -> None:
    """split_duration이 ValidatedArgs에 그대로 전달된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(split_duration="1h")
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.split_duration == "1h"


def test_build_empty_split() -> None:
    """빈 split 옵션은 None으로 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(split_duration="", split_size="")
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.split_duration is None
    assert result.split_size is None


def test_build_project_and_upload() -> None:
    """프로젝트명과 업로드 옵션이 올바르게 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(project="제주도 여행", upload=True)
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.project == "제주도 여행"
    assert result.upload is True


def test_build_empty_project() -> None:
    """빈 프로젝트명은 None으로 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(project="")
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.project is None


def test_build_empty_targets_raises() -> None:
    """targets가 비어있으면 ValueError를 발생시킨다."""
    state = default_state()
    with pytest.raises(ValueError, match="파일"):
        build_validated_args([], state)


def test_build_watermark_options() -> None:
    """워터마크 옵션이 올바르게 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(
        watermark=True,
        watermark_pos="top-right",
        watermark_size=64,
        watermark_color="black",
        watermark_alpha=0.5,
    )
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.watermark is True
    assert result.watermark_pos == "top-right"
    assert result.watermark_size == 64
    assert result.watermark_color == "black"
    assert result.watermark_alpha == 0.5


def test_build_lut_path() -> None:
    """LUT 경로가 Path로 변환된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(lut_path="/tmp/nikon.cube", auto_lut=True, lut_before_hdr=True)
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.lut_path == Path("/tmp/nikon.cube")
    assert result.auto_lut is True
    assert result.lut_before_hdr is True


def test_build_dry_run() -> None:
    """dry_run 모드가 올바르게 전달된다."""
    targets = [Path("/tmp/test")]
    state = TuiOptionState(dry_run=True)
    result = build_validated_args(targets, state)

    assert isinstance(result, ValidatedArgs)
    assert result.dry_run is True
