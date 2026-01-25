"""입력 검증 테스트."""

from pathlib import Path
from unittest.mock import patch

import pytest

from tubearchive.utils.validators import (
    VIDEO_EXTENSIONS,
    ValidationError,
    check_disk_space,
    validate_ffmpeg_available,
    validate_output_path,
    validate_video_file,
)


class TestValidateVideoFile:
    """비디오 파일 검증 테스트."""

    def test_valid_video_file(self, tmp_path: Path) -> None:
        """유효한 비디오 파일."""
        video = tmp_path / "test.mp4"
        video.write_bytes(b"fake video content")

        result = validate_video_file(video)

        assert result == video

    def test_nonexistent_file_raises_error(self, tmp_path: Path) -> None:
        """존재하지 않는 파일."""
        video = tmp_path / "nonexistent.mp4"

        with pytest.raises(ValidationError, match="does not exist"):
            validate_video_file(video)

    def test_directory_raises_error(self, tmp_path: Path) -> None:
        """파일 대신 디렉토리."""
        with pytest.raises(ValidationError, match="not a file"):
            validate_video_file(tmp_path)

    def test_invalid_extension_raises_error(self, tmp_path: Path) -> None:
        """잘못된 확장자."""
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("hello")

        with pytest.raises(ValidationError, match="not a valid video"):
            validate_video_file(txt_file)

    def test_empty_file_raises_error(self, tmp_path: Path) -> None:
        """빈 파일."""
        video = tmp_path / "empty.mp4"
        video.touch()

        with pytest.raises(ValidationError, match="empty"):
            validate_video_file(video)

    def test_accepted_extensions(self) -> None:
        """지원되는 확장자 목록."""
        assert ".mp4" in VIDEO_EXTENSIONS
        assert ".mov" in VIDEO_EXTENSIONS
        assert ".mts" in VIDEO_EXTENSIONS
        assert ".m4v" in VIDEO_EXTENSIONS
        assert ".mkv" in VIDEO_EXTENSIONS
        assert ".avi" in VIDEO_EXTENSIONS


class TestValidateOutputPath:
    """출력 경로 검증 테스트."""

    def test_valid_output_path(self, tmp_path: Path) -> None:
        """유효한 출력 경로."""
        output = tmp_path / "output.mp4"

        result = validate_output_path(output)

        assert result == output

    def test_nonexistent_parent_raises_error(self, tmp_path: Path) -> None:
        """존재하지 않는 부모 디렉토리."""
        output = tmp_path / "nonexistent" / "output.mp4"

        with pytest.raises(ValidationError, match="directory does not exist"):
            validate_output_path(output)

    def test_overwrites_existing_with_flag(self, tmp_path: Path) -> None:
        """덮어쓰기 플래그로 기존 파일 허용."""
        output = tmp_path / "existing.mp4"
        output.touch()

        result = validate_output_path(output, allow_overwrite=True)

        assert result == output

    def test_existing_file_raises_error_by_default(self, tmp_path: Path) -> None:
        """기본값으로 기존 파일 에러."""
        output = tmp_path / "existing.mp4"
        output.touch()

        with pytest.raises(ValidationError, match="already exists"):
            validate_output_path(output, allow_overwrite=False)


class TestCheckDiskSpace:
    """디스크 공간 검사 테스트."""

    def test_sufficient_disk_space(self, tmp_path: Path) -> None:
        """충분한 디스크 공간."""
        # 1KB 필요
        required_bytes = 1024

        # 정상적으로 통과해야 함
        check_disk_space(tmp_path, required_bytes)

    @patch("tubearchive.utils.validators.shutil.disk_usage")
    def test_insufficient_disk_space_raises_error(
        self,
        mock_disk_usage: patch,
        tmp_path: Path,
    ) -> None:
        """디스크 공간 부족."""
        # 사용 가능 공간: 100MB (named tuple 형태로 반환)
        from collections import namedtuple

        DiskUsage = namedtuple("DiskUsage", ["total", "used", "free"])
        mock_disk_usage.return_value = DiskUsage(1000000000, 900000000, 100000000)

        # 200MB 필요
        required_bytes = 200 * 1024 * 1024

        with pytest.raises(ValidationError, match="Insufficient disk space"):
            check_disk_space(tmp_path, required_bytes)

    def test_estimates_required_space(self, tmp_path: Path) -> None:
        """필요 공간 추정."""
        video = tmp_path / "test.mp4"
        video.write_bytes(b"x" * 1000)  # 1KB

        # 1KB * 1.5 = 1.5KB 필요 (기본 배수)
        from tubearchive.utils.validators import estimate_required_space

        required = estimate_required_space([video])
        assert required >= 1500


class TestValidateFFmpegAvailable:
    """FFmpeg 가용성 검사 테스트."""

    @patch("shutil.which")
    def test_ffmpeg_available(self, mock_which: patch) -> None:
        """FFmpeg 사용 가능."""
        mock_which.return_value = "/usr/local/bin/ffmpeg"

        result = validate_ffmpeg_available()

        assert result == "/usr/local/bin/ffmpeg"

    @patch("shutil.which")
    def test_ffmpeg_not_available_raises_error(self, mock_which: patch) -> None:
        """FFmpeg 없음."""
        mock_which.return_value = None

        with pytest.raises(ValidationError, match="not found"):
            validate_ffmpeg_available()

    @patch("shutil.which")
    def test_ffprobe_available(self, mock_which: patch) -> None:
        """ffprobe 사용 가능."""
        mock_which.return_value = "/usr/local/bin/ffprobe"

        result = validate_ffmpeg_available("ffprobe")

        assert result == "/usr/local/bin/ffprobe"
