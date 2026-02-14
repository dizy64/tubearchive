"""썸네일 추출 모듈 테스트."""

import logging
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.ffmpeg.thumbnail import (
    _build_thumbnail_prepare_command,
    _probe_image_size,
    build_thumbnail_command,
    calculate_thumbnail_timestamps,
    extract_thumbnails,
    generate_thumbnail_paths,
    parse_timestamp,
    prepare_thumbnail_for_youtube,
)


class TestCalculateThumbnailTimestamps:
    """영상 길이 기반 타임스탬프 계산 테스트."""

    def test_default_three_timestamps(self) -> None:
        """기본 3장 (10%, 33%, 50%)."""
        result = calculate_thumbnail_timestamps(100.0)

        assert len(result) == 3
        assert result == [10.0, 33.0, 50.0]

    def test_precise_ratios(self) -> None:
        """비율이 정확히 적용되는지."""
        result = calculate_thumbnail_timestamps(300.0)

        assert result[0] == pytest.approx(30.0)  # 10%
        assert result[1] == pytest.approx(99.0)  # 33%
        assert result[2] == pytest.approx(150.0)  # 50%

    def test_zero_duration_returns_empty(self) -> None:
        """duration=0이면 빈 리스트."""
        result = calculate_thumbnail_timestamps(0.0)

        assert result == []

    def test_negative_duration_returns_empty(self) -> None:
        """duration<0이면 빈 리스트."""
        result = calculate_thumbnail_timestamps(-10.0)

        assert result == []

    def test_short_video(self) -> None:
        """짧은 영상 (1초)."""
        result = calculate_thumbnail_timestamps(1.0)

        assert len(result) == 3
        assert result[0] == pytest.approx(0.1)
        assert result[1] == pytest.approx(0.33)
        assert result[2] == pytest.approx(0.5)

    def test_custom_percentages(self) -> None:
        """커스텀 비율."""
        result = calculate_thumbnail_timestamps(200.0, percentages=(0.25, 0.75))

        assert len(result) == 2
        assert result[0] == pytest.approx(50.0)
        assert result[1] == pytest.approx(150.0)


class TestParseTimestamp:
    """타임스탬프 파싱 테스트."""

    def test_hh_mm_ss_format(self) -> None:
        """HH:MM:SS 형식."""
        assert parse_timestamp("01:30:00") == pytest.approx(5400.0)

    def test_mm_ss_format(self) -> None:
        """MM:SS 형식."""
        assert parse_timestamp("02:30") == pytest.approx(150.0)

    def test_seconds_only(self) -> None:
        """SS 형식."""
        assert parse_timestamp("90") == pytest.approx(90.0)

    def test_seconds_with_milliseconds(self) -> None:
        """SS.ms 형식."""
        assert parse_timestamp("90.5") == pytest.approx(90.5)

    def test_hh_mm_ss_with_milliseconds(self) -> None:
        """HH:MM:SS.ms 형식."""
        assert parse_timestamp("01:00:30.5") == pytest.approx(3630.5)

    def test_invalid_format_raises(self) -> None:
        """잘못된 형식은 ValueError."""
        with pytest.raises(ValueError, match="Invalid timestamp"):
            parse_timestamp("abc")

    def test_empty_string_raises(self) -> None:
        """빈 문자열은 ValueError."""
        with pytest.raises(ValueError, match="Invalid timestamp"):
            parse_timestamp("")

    def test_fractional_hours_raises(self) -> None:
        """소수점 시간은 ValueError."""
        with pytest.raises(ValueError, match="Invalid timestamp"):
            parse_timestamp("1.5:30:00")

    def test_fractional_minutes_raises(self) -> None:
        """소수점 분은 ValueError."""
        with pytest.raises(ValueError, match="Invalid timestamp"):
            parse_timestamp("01:30.5:00")

    def test_fractional_minutes_in_mm_ss_raises(self) -> None:
        """MM:SS 형식에서 소수점 분은 ValueError."""
        with pytest.raises(ValueError, match="Invalid timestamp"):
            parse_timestamp("1.5:30")

    def test_negative_result_raises(self) -> None:
        """음수 결과는 ValueError."""
        with pytest.raises(ValueError, match="negative"):
            parse_timestamp("-10")


class TestGenerateThumbnailPaths:
    """썸네일 경로 생성 테스트."""

    def test_numbered_paths(self, tmp_path: Path) -> None:
        """번호 매긴 경로 생성."""
        video = tmp_path / "my_video.mp4"
        result = generate_thumbnail_paths(video, count=3)

        assert len(result) == 3
        assert result[0].name == "my_video_thumb_01.jpg"
        assert result[1].name == "my_video_thumb_02.jpg"
        assert result[2].name == "my_video_thumb_03.jpg"

    def test_default_directory_is_video_parent(self, tmp_path: Path) -> None:
        """기본 디렉토리는 영상 디렉토리."""
        video = tmp_path / "videos" / "clip.mp4"
        result = generate_thumbnail_paths(video, count=1)

        assert result[0].parent == tmp_path / "videos"

    def test_custom_output_dir(self, tmp_path: Path) -> None:
        """커스텀 디렉토리."""
        video = tmp_path / "clip.mp4"
        out_dir = tmp_path / "thumbs"
        result = generate_thumbnail_paths(video, count=2, output_dir=out_dir)

        assert all(p.parent == out_dir for p in result)

    def test_zero_count_returns_empty(self, tmp_path: Path) -> None:
        """0개 요청 시 빈 리스트."""
        video = tmp_path / "clip.mp4"
        result = generate_thumbnail_paths(video, count=0)

        assert result == []


class TestBuildThumbnailCommand:
    """FFmpeg 썸네일 명령 생성 테스트."""

    def test_command_structure(self, tmp_path: Path) -> None:
        """기본 명령 구조."""
        inp = tmp_path / "input.mp4"
        out = tmp_path / "thumb.jpg"
        cmd = build_thumbnail_command(inp, out, timestamp=30.0)

        assert cmd[0] == "ffmpeg"
        assert "-y" in cmd
        assert "-vframes" in cmd
        assert "1" in cmd

    def test_ss_before_input(self, tmp_path: Path) -> None:
        """-ss가 -i 앞에 위치 (fast seek)."""
        inp = tmp_path / "input.mp4"
        out = tmp_path / "thumb.jpg"
        cmd = build_thumbnail_command(inp, out, timestamp=30.0)

        ss_idx = cmd.index("-ss")
        i_idx = cmd.index("-i")
        assert ss_idx < i_idx

    def test_quality_parameter(self, tmp_path: Path) -> None:
        """품질 파라미터 적용."""
        inp = tmp_path / "input.mp4"
        out = tmp_path / "thumb.jpg"
        cmd = build_thumbnail_command(inp, out, timestamp=10.0, quality=5)

        qv_idx = cmd.index("-q:v")
        assert cmd[qv_idx + 1] == "5"

    def test_overwrite_flag(self, tmp_path: Path) -> None:
        """-y 플래그 포함."""
        inp = tmp_path / "input.mp4"
        out = tmp_path / "thumb.jpg"
        cmd = build_thumbnail_command(inp, out, timestamp=0.0)

        assert "-y" in cmd

    def test_custom_ffmpeg_path(self, tmp_path: Path) -> None:
        """커스텀 ffmpeg 경로."""
        inp = tmp_path / "input.mp4"
        out = tmp_path / "thumb.jpg"
        cmd = build_thumbnail_command(inp, out, timestamp=0.0, ffmpeg_path="/usr/local/bin/ffmpeg")

        assert cmd[0] == "/usr/local/bin/ffmpeg"


class TestExtractThumbnails:
    """썸네일 추출 통합 테스트 (mock)."""

    @patch("tubearchive.ffmpeg.thumbnail.subprocess.run")
    @patch("tubearchive.ffmpeg.thumbnail.detect_metadata")
    def test_default_three_thumbnails(
        self,
        mock_detect: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """기본 3장 추출 (duration 자동 감지)."""
        video = tmp_path / "video.mp4"
        video.touch()

        mock_meta = MagicMock()
        mock_meta.duration_seconds = 100.0
        mock_detect.return_value = mock_meta
        mock_run.return_value = MagicMock(returncode=0)

        # 출력 파일을 미리 생성해서 성공 시뮬레이션
        for i in range(1, 4):
            (tmp_path / f"video_thumb_{i:02d}.jpg").touch()

        result = extract_thumbnails(video)

        assert len(result) == 3
        assert mock_run.call_count == 3

    @patch("tubearchive.ffmpeg.thumbnail.subprocess.run")
    def test_custom_timestamps(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """커스텀 타임스탬프 지정."""
        video = tmp_path / "video.mp4"
        video.touch()

        mock_run.return_value = MagicMock(returncode=0)

        for i in range(1, 3):
            (tmp_path / f"video_thumb_{i:02d}.jpg").touch()

        result = extract_thumbnails(video, timestamps=[10.0, 60.0])

        assert len(result) == 2
        assert mock_run.call_count == 2

    @patch("tubearchive.ffmpeg.thumbnail.subprocess.run")
    def test_ffmpeg_failure_skips(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ffmpeg 실패 시 해당 프레임 건너뛰기."""
        video = tmp_path / "video.mp4"
        video.touch()

        # 첫번째 성공, 나머지 실패
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=1),
        ]
        (tmp_path / "video_thumb_01.jpg").touch()

        result = extract_thumbnails(video, timestamps=[10.0, 60.0])

        assert len(result) == 1

    @patch("tubearchive.ffmpeg.thumbnail.subprocess.run")
    def test_ffmpeg_timeout_skips(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ffmpeg timeout 시 해당 프레임 건너뛰기."""
        video = tmp_path / "video.mp4"
        video.touch()

        mock_run.side_effect = [
            MagicMock(returncode=0),
            subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=30),
        ]
        (tmp_path / "video_thumb_01.jpg").touch()

        result = extract_thumbnails(video, timestamps=[10.0, 60.0])

        assert len(result) == 1

    @patch("tubearchive.ffmpeg.thumbnail.subprocess.run")
    def test_ffmpeg_failure_logs_stderr(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """ffmpeg 실패 시 stderr가 로그에 포함."""
        video = tmp_path / "video.mp4"
        video.touch()

        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="codec not found",
        )

        with caplog.at_level(logging.WARNING):
            result = extract_thumbnails(video, timestamps=[10.0])

        assert result == []
        assert "codec not found" in caplog.text

    def test_nonexistent_file_returns_empty(self, tmp_path: Path) -> None:
        """미존재 파일은 빈 리스트."""
        video = tmp_path / "nonexistent.mp4"

        result = extract_thumbnails(video, timestamps=[10.0])

        assert result == []


class TestPrepareThumbnailForYoutube:
    """YouTube 썸네일 정규화 유틸."""

    def test_build_thumbnail_prepare_command(self, tmp_path: Path) -> None:
        """썸네일 변환 명령 생성."""
        src = tmp_path / "thumb.jpg"
        dst = tmp_path / "thumb_youtube.jpg"
        cmd = _build_thumbnail_prepare_command(src, dst)

        assert cmd[0] == "ffmpeg"
        assert cmd[1] == "-y"
        assert "-vf" in cmd
        assert "scale=1280:720" in cmd

    @patch("tubearchive.ffmpeg.thumbnail.subprocess.run")
    def test_probe_image_size(self, mock_run: MagicMock, tmp_path: Path) -> None:
        """ffprobe 결과에서 width/height 추출."""
        image = tmp_path / "image.jpg"
        image.touch()

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"streams":[{"codec_type":"video","width":1920,"height":1080}]}',
        )

        width, height = _probe_image_size(image)

        assert width == 1920
        assert height == 1080
        assert mock_run.call_count == 1

    @patch("tubearchive.ffmpeg.thumbnail._probe_image_size")
    @patch("tubearchive.ffmpeg.thumbnail.subprocess.run")
    def test_prepare_thumbnail_returns_original_when_compatible(
        self,
        mock_run: MagicMock,
        mock_probe: MagicMock,
        tmp_path: Path,
    ) -> None:
        """이미지 조건 충족 시 원본 경로 반환."""
        image = tmp_path / "image.jpg"
        image.write_bytes(b"x" * 1024)
        mock_probe.return_value = (1920, 1080)

        result = prepare_thumbnail_for_youtube(image)

        assert result == image
        mock_run.assert_not_called()

    @patch("tubearchive.ffmpeg.thumbnail._probe_image_size")
    @patch("tubearchive.ffmpeg.thumbnail.subprocess.run")
    def test_prepare_thumbnail_reencodes_when_too_small(
        self,
        mock_run: MagicMock,
        mock_probe: MagicMock,
        tmp_path: Path,
    ) -> None:
        """작은 이미지면 ffmpeg로 변환."""
        image = tmp_path / "image.jpg"
        image.write_bytes(b"x")
        output = tmp_path / "image_youtube.jpg"
        output.write_bytes(b"prepared")

        mock_probe.return_value = (640, 360)
        mock_run.return_value = MagicMock(returncode=0)

        result = prepare_thumbnail_for_youtube(image)

        assert result == output
        command = mock_run.call_args[0][0]
        assert command[0] == "ffmpeg"
        assert "-vf" in command
        assert "scale=1280:720" in command

    @patch("tubearchive.ffmpeg.thumbnail._probe_image_size", return_value=(640, 360))
    @patch("tubearchive.ffmpeg.thumbnail.subprocess.run")
    def test_prepare_thumbnail_raises_on_ffmpeg_error(
        self,
        mock_run: MagicMock,
        _mock_probe: MagicMock,
        tmp_path: Path,
    ) -> None:
        """ffmpeg 실패 시 RuntimeError."""
        image = tmp_path / "image.jpg"
        image.write_bytes(b"x")

        mock_run.return_value = MagicMock(returncode=1, stderr="ffmpeg failed")

        with pytest.raises(RuntimeError, match="Failed to prepare thumbnail"):
            prepare_thumbnail_for_youtube(image)

    def test_prepare_thumbnail_rejects_unsupported_format(self, tmp_path: Path) -> None:
        """지원하지 않는 썸네일 확장자."""
        image = tmp_path / "image.gif"
        image.write_text("gif")

        with pytest.raises(ValueError, match="Unsupported thumbnail format"):
            prepare_thumbnail_for_youtube(image)
