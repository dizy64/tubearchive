"""LUT 필터 및 auto-lut 매칭 테스트."""

from pathlib import Path

import pytest

from tubearchive.core.transcoder import _resolve_auto_lut
from tubearchive.ffmpeg.effects import LUT_SUPPORTED_EXTENSIONS, create_lut_filter


class TestCreateLutFilter:
    """create_lut_filter 함수 테스트."""

    def test_cube_extension(self, tmp_path: Path) -> None:
        """.cube 확장자 LUT 파일."""
        lut_file = tmp_path / "test.cube"
        lut_file.write_text("LUT_3D_SIZE 33\n")
        result = create_lut_filter(str(lut_file))
        assert result == f"lut3d=file={lut_file}"

    def test_3dl_extension(self, tmp_path: Path) -> None:
        """.3dl 확장자 LUT 파일."""
        lut_file = tmp_path / "test.3dl"
        lut_file.write_text("LUT data\n")
        result = create_lut_filter(str(lut_file))
        assert result == f"lut3d=file={lut_file}"

    def test_absolute_path(self, tmp_path: Path) -> None:
        """절대 경로로 변환."""
        lut_file = tmp_path / "sub" / "test.cube"
        lut_file.parent.mkdir(parents=True, exist_ok=True)
        lut_file.write_text("LUT data\n")
        result = create_lut_filter(str(lut_file))
        assert str(lut_file) in result

    def test_path_with_spaces(self, tmp_path: Path) -> None:
        """공백 포함 경로."""
        lut_dir = tmp_path / "my luts"
        lut_dir.mkdir()
        lut_file = lut_dir / "test lut.cube"
        lut_file.write_text("LUT data\n")
        result = create_lut_filter(str(lut_file))
        assert "my luts" in result
        assert "test lut.cube" in result

    def test_path_with_korean(self, tmp_path: Path) -> None:
        """한글 포함 경로."""
        lut_dir = tmp_path / "색보정"
        lut_dir.mkdir()
        lut_file = lut_dir / "니콘.cube"
        lut_file.write_text("LUT data\n")
        result = create_lut_filter(str(lut_file))
        assert "색보정" in result
        assert "니콘.cube" in result

    def test_nonexistent_file_raises(self) -> None:
        """존재하지 않는 파일 → FileNotFoundError."""
        with pytest.raises(FileNotFoundError, match="LUT file not found"):
            create_lut_filter("/nonexistent/path/test.cube")

    def test_invalid_extension_raises(self, tmp_path: Path) -> None:
        """잘못된 확장자 → ValueError."""
        lut_file = tmp_path / "test.png"
        lut_file.write_text("not a lut\n")
        with pytest.raises(ValueError, match="Unsupported LUT format"):
            create_lut_filter(str(lut_file))

    def test_txt_extension_raises(self, tmp_path: Path) -> None:
        """.txt 확장자 → ValueError."""
        lut_file = tmp_path / "test.txt"
        lut_file.write_text("not a lut\n")
        with pytest.raises(ValueError, match="Unsupported LUT format"):
            create_lut_filter(str(lut_file))

    def test_case_insensitive_extension(self, tmp_path: Path) -> None:
        """대소문자 무시 확장자."""
        lut_file = tmp_path / "test.CUBE"
        lut_file.write_text("LUT data\n")
        result = create_lut_filter(str(lut_file))
        assert "lut3d=file=" in result

    def test_path_with_single_quote(self, tmp_path: Path) -> None:
        """작은따옴표 포함 경로 — FFmpeg 필터 백슬래시 이스케이핑 검증."""
        lut_dir = tmp_path / "user's luts"
        lut_dir.mkdir()
        lut_file = lut_dir / "test.cube"
        lut_file.write_text("LUT data\n")
        result = create_lut_filter(str(lut_file))
        # 작은따옴표가 \' 로 이스케이프됨
        assert r"\'" in result
        assert "lut3d=file=" in result
        # 따옴표로 감싸지 않음 (backslash 이스케이핑 방식)
        assert "file='" not in result

    def test_supported_extensions_constant(self) -> None:
        """지원 확장자 상수 확인."""
        assert ".cube" in LUT_SUPPORTED_EXTENSIONS
        assert ".3dl" in LUT_SUPPORTED_EXTENSIONS


class TestResolveLutFilter:
    """_resolve_auto_lut 함수 테스트."""

    def test_nikon_match(self, tmp_path: Path) -> None:
        """nikon 키워드 매칭."""
        lut_file = tmp_path / "nikon.cube"
        lut_file.write_text("LUT data\n")
        device_luts = {"nikon": str(lut_file)}
        result = _resolve_auto_lut("NIKON Z6III", device_luts)
        assert result == str(lut_file)

    def test_gopro_match(self, tmp_path: Path) -> None:
        """gopro 키워드 매칭."""
        lut_file = tmp_path / "gopro.cube"
        lut_file.write_text("LUT data\n")
        device_luts = {"gopro": str(lut_file)}
        result = _resolve_auto_lut("GoPro HERO12 Black", device_luts)
        assert result == str(lut_file)

    def test_case_insensitive_match(self, tmp_path: Path) -> None:
        """대소문자 무시 매칭."""
        lut_file = tmp_path / "nikon.cube"
        lut_file.write_text("LUT data\n")
        device_luts = {"Nikon": str(lut_file)}
        result = _resolve_auto_lut("nikon z6iii", device_luts)
        assert result == str(lut_file)

    def test_longest_keyword_wins(self, tmp_path: Path) -> None:
        """다중 매칭 시 가장 긴 키워드 우선."""
        generic_lut = tmp_path / "generic.cube"
        generic_lut.write_text("LUT data\n")
        specific_lut = tmp_path / "specific.cube"
        specific_lut.write_text("LUT data\n")
        device_luts = {
            "nikon": str(generic_lut),
            "nikon z6": str(specific_lut),
        }
        result = _resolve_auto_lut("Nikon Z6III", device_luts)
        assert result == str(specific_lut)

    def test_no_match_returns_none(self) -> None:
        """매칭 실패 → None."""
        device_luts = {"nikon": "/path/to/nikon.cube"}
        result = _resolve_auto_lut("Canon EOS R5", device_luts)
        assert result is None

    def test_file_not_found_returns_none(self) -> None:
        """매칭되지만 파일 미존재 → warning + None."""
        device_luts = {"nikon": "/nonexistent/nikon.cube"}
        result = _resolve_auto_lut("Nikon Z6III", device_luts)
        assert result is None

    def test_empty_device_luts(self) -> None:
        """빈 device_luts → None."""
        result = _resolve_auto_lut("Nikon Z6III", {})
        assert result is None

    def test_empty_device_model(self, tmp_path: Path) -> None:
        """빈 device_model → None."""
        lut_file = tmp_path / "nikon.cube"
        lut_file.write_text("LUT data\n")
        device_luts = {"nikon": str(lut_file)}
        result = _resolve_auto_lut("", device_luts)
        assert result is None

    def test_empty_keyword_skipped(self, tmp_path: Path) -> None:
        """빈 문자열 키워드는 건너뛴다."""
        lut_file = tmp_path / "catch_all.cube"
        lut_file.write_text("LUT data\n")
        device_luts = {"": str(lut_file)}
        result = _resolve_auto_lut("Nikon Z6III", device_luts)
        assert result is None

    def test_tilde_path_expanduser(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """~ 경로가 expanduser로 확장된다."""
        lut_file = tmp_path / "nikon.cube"
        lut_file.write_text("LUT data\n")
        # ~ → tmp_path로 확장되도록 HOME 환경변수 변경
        monkeypatch.setenv("HOME", str(tmp_path))
        device_luts = {"nikon": "~/nikon.cube"}
        result = _resolve_auto_lut("NIKON Z6III", device_luts)
        assert result is not None
        assert "~" not in result  # 확장되어 절대경로
        assert str(tmp_path) in result
