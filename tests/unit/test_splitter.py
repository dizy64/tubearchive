"""영상 분할 모듈 테스트."""

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.core.splitter import SplitOptions, VideoSplitter, probe_duration


class TestSplitOptions:
    """SplitOptions 데이터클래스 테스트."""

    def test_default_values(self) -> None:
        """기본값 테스트."""
        options = SplitOptions()
        assert options.duration is None
        assert options.size is None

    def test_duration_only(self) -> None:
        """시간 기준만 설정."""
        options = SplitOptions(duration=3600)
        assert options.duration == 3600
        assert options.size is None

    def test_size_only(self) -> None:
        """크기 기준만 설정."""
        options = SplitOptions(size=10 * 1024**3)
        assert options.size == 10 * 1024**3
        assert options.duration is None

    def test_both_criteria(self) -> None:
        """둘 다 설정 가능 (우선순위는 splitter가 결정)."""
        options = SplitOptions(duration=3600, size=1024**3)
        assert options.duration == 3600
        assert options.size == 1024**3


class TestVideoSplitterParsing:
    """VideoSplitter 파싱 메서드 테스트."""

    def setup_method(self) -> None:
        """각 테스트 전 초기화."""
        self.splitter = VideoSplitter()

    # --- 시간 파싱 테스트 ---

    def test_parse_duration_hours(self) -> None:
        """시간 단위 파싱."""
        assert self.splitter.parse_duration("1h") == 3600
        assert self.splitter.parse_duration("2h") == 7200
        assert self.splitter.parse_duration("12h") == 43200

    def test_parse_duration_minutes(self) -> None:
        """분 단위 파싱."""
        assert self.splitter.parse_duration("1m") == 60
        assert self.splitter.parse_duration("30m") == 1800
        assert self.splitter.parse_duration("90m") == 5400

    def test_parse_duration_seconds(self) -> None:
        """초 단위 파싱."""
        assert self.splitter.parse_duration("1s") == 1
        assert self.splitter.parse_duration("30s") == 30
        assert self.splitter.parse_duration("90s") == 90

    def test_parse_duration_combined(self) -> None:
        """복합 시간 파싱."""
        assert self.splitter.parse_duration("1h30m") == 5400  # 1시간 30분
        assert self.splitter.parse_duration("2h15m30s") == 8130  # 2시간 15분 30초
        assert self.splitter.parse_duration("1h0m30s") == 3630

    def test_parse_duration_no_unit(self) -> None:
        """단위 없는 숫자는 초로 해석."""
        assert self.splitter.parse_duration("60") == 60
        assert self.splitter.parse_duration("3600") == 3600

    def test_parse_duration_invalid_format(self) -> None:
        """잘못된 형식."""
        with pytest.raises(ValueError, match="Invalid duration format"):
            self.splitter.parse_duration("invalid")

        with pytest.raises(ValueError, match="Invalid duration format"):
            self.splitter.parse_duration("1x")

        with pytest.raises(ValueError, match="Invalid duration format"):
            self.splitter.parse_duration("")

    def test_parse_duration_negative(self) -> None:
        """음수 불가."""
        with pytest.raises(ValueError, match="Duration must be positive"):
            self.splitter.parse_duration("-1h")

    # --- 크기 파싱 테스트 ---

    def test_parse_size_gigabytes(self) -> None:
        """기가바이트 단위 파싱."""
        assert self.splitter.parse_size("1G") == 1024**3
        assert self.splitter.parse_size("10G") == 10 * 1024**3
        assert self.splitter.parse_size("256G") == 256 * 1024**3

    def test_parse_size_megabytes(self) -> None:
        """메가바이트 단위 파싱."""
        assert self.splitter.parse_size("1M") == 1024**2
        assert self.splitter.parse_size("256M") == 256 * 1024**2
        assert self.splitter.parse_size("1024M") == 1024**3

    def test_parse_size_kilobytes(self) -> None:
        """킬로바이트 단위 파싱."""
        assert self.splitter.parse_size("1K") == 1024
        assert self.splitter.parse_size("1024K") == 1024**2

    def test_parse_size_bytes(self) -> None:
        """바이트 단위 파싱."""
        assert self.splitter.parse_size("1024B") == 1024
        assert self.splitter.parse_size("1024") == 1024  # 단위 없으면 바이트

    def test_parse_size_decimal(self) -> None:
        """소수점 지원."""
        assert self.splitter.parse_size("1.5G") == int(1.5 * 1024**3)
        assert self.splitter.parse_size("0.5M") == int(0.5 * 1024**2)
        assert self.splitter.parse_size("2.5K") == int(2.5 * 1024)

    def test_parse_size_case_insensitive(self) -> None:
        """대소문자 구분 없음."""
        assert self.splitter.parse_size("1g") == 1024**3
        assert self.splitter.parse_size("256m") == 256 * 1024**2
        assert self.splitter.parse_size("1k") == 1024

    def test_parse_size_invalid_format(self) -> None:
        """잘못된 형식."""
        with pytest.raises(ValueError, match="Invalid size format"):
            self.splitter.parse_size("invalid")

        with pytest.raises(ValueError, match="Invalid size format"):
            self.splitter.parse_size("1X")

        with pytest.raises(ValueError, match="Invalid size format"):
            self.splitter.parse_size("")

    def test_parse_size_negative(self) -> None:
        """음수 불가."""
        with pytest.raises(ValueError, match="Size must be positive"):
            self.splitter.parse_size("-10G")

    def test_parse_size_zero(self) -> None:
        """0 불가."""
        with pytest.raises(ValueError, match="Size must be positive"):
            self.splitter.parse_size("0G")


class TestVideoSplitterFilename:
    """분할 파일명 생성 테스트."""

    def setup_method(self) -> None:
        """각 테스트 전 초기화."""
        self.splitter = VideoSplitter()

    def test_generate_split_filenames_simple(self) -> None:
        """기본 파일명 생성."""
        base_path = Path("/tmp/video.mp4")
        filenames = self.splitter.generate_split_filenames(base_path, count=3)

        assert len(filenames) == 3
        assert filenames[0] == Path("/tmp/video_001.mp4")
        assert filenames[1] == Path("/tmp/video_002.mp4")
        assert filenames[2] == Path("/tmp/video_003.mp4")

    def test_generate_split_filenames_large_count(self) -> None:
        """많은 분할 파일."""
        base_path = Path("/output/test.mp4")
        filenames = self.splitter.generate_split_filenames(base_path, count=100)

        assert len(filenames) == 100
        assert filenames[0] == Path("/output/test_001.mp4")
        assert filenames[9] == Path("/output/test_010.mp4")
        assert filenames[99] == Path("/output/test_100.mp4")

    def test_generate_split_filenames_different_extension(self) -> None:
        """다양한 확장자."""
        filenames = self.splitter.generate_split_filenames(Path("video.mov"), count=2)
        assert filenames[0] == Path("video_001.mov")
        assert filenames[1] == Path("video_002.mov")

    def test_generate_split_filenames_no_extension(self) -> None:
        """확장자 없는 경우."""
        filenames = self.splitter.generate_split_filenames(Path("video"), count=2)
        assert filenames[0] == Path("video_001")
        assert filenames[1] == Path("video_002")

    def test_generate_split_filenames_invalid_count(self) -> None:
        """잘못된 개수."""
        with pytest.raises(ValueError, match="Count must be at least 2"):
            self.splitter.generate_split_filenames(Path("video.mp4"), count=1)

        with pytest.raises(ValueError, match="Count must be at least 2"):
            self.splitter.generate_split_filenames(Path("video.mp4"), count=0)


class TestVideoSplitterCommand:
    """FFmpeg 명령어 생성 테스트."""

    def setup_method(self) -> None:
        """각 테스트 전 초기화."""
        self.splitter = VideoSplitter()

    def test_build_ffmpeg_command_duration(self) -> None:
        """시간 기준 분할 명령어."""
        input_path = Path("/input/video.mp4")
        output_pattern = Path("/output/video_%03d.mp4")
        options = SplitOptions(duration=3600)

        cmd = self.splitter.build_ffmpeg_command(input_path, output_pattern, options)

        assert "ffmpeg" in cmd
        assert "-i" in cmd
        assert str(input_path) in cmd
        assert "-f" in cmd
        assert "segment" in cmd
        assert "-segment_time" in cmd
        assert "3600" in cmd
        assert "-c" in cmd
        assert "copy" in cmd
        assert str(output_pattern) in cmd

    def test_build_ffmpeg_command_size(self) -> None:
        """크기 기준 분할 명령어."""
        input_path = Path("/input/video.mp4")
        output_pattern = Path("/output/video_%03d.mp4")
        options = SplitOptions(size=10 * 1024**3)

        cmd = self.splitter.build_ffmpeg_command(input_path, output_pattern, options)

        assert "ffmpeg" in cmd
        assert "-f" in cmd
        assert "segment" in cmd
        assert "-segment_list_size" in cmd or "-fs" in cmd  # 크기 제한 옵션
        assert "-c" in cmd
        assert "copy" in cmd

    def test_build_ffmpeg_command_no_criteria(self) -> None:
        """분할 기준 없음 → 에러."""
        options = SplitOptions()
        with pytest.raises(ValueError, match="At least one split criterion"):
            self.splitter.build_ffmpeg_command(Path("input.mp4"), Path("output_%03d.mp4"), options)


class TestVideoSplitterIntegration:
    """VideoSplitter 통합 테스트 (mock 사용)."""

    def setup_method(self) -> None:
        """각 테스트 전 초기화."""
        self.splitter = VideoSplitter()

    @patch("tubearchive.core.splitter.subprocess.run")
    @patch("tubearchive.core.splitter.Path.exists")
    def test_split_video_duration_success(
        self, mock_exists: MagicMock, mock_run: MagicMock
    ) -> None:
        """시간 기준 분할 성공."""
        # Mock 설정
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.mp4"
            input_path.write_text("fake video")
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()

            options = SplitOptions(duration=3600)
            result = self.splitter.split_video(input_path, output_dir, options)

            # subprocess.run이 호출되었는지 확인
            assert mock_run.called
            # 반환값은 분할된 파일 목록
            assert isinstance(result, list)
            assert all(isinstance(p, Path) for p in result)

    @patch("tubearchive.core.splitter.subprocess.run")
    def test_split_video_ffmpeg_failure(self, mock_run: MagicMock) -> None:
        """FFmpeg 실패 시 에러."""
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="FFmpeg error")

        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.mp4"
            input_path.write_text("fake video")
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()

            options = SplitOptions(duration=3600)

            with pytest.raises(RuntimeError, match="FFmpeg failed"):
                self.splitter.split_video(input_path, output_dir, options)

    def test_split_video_invalid_input(self) -> None:
        """존재하지 않는 입력 파일."""
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "nonexistent.mp4"
            output_dir = Path(tmpdir) / "output"
            output_dir.mkdir()

            options = SplitOptions(duration=3600)

            with pytest.raises(FileNotFoundError, match="Input file not found"):
                self.splitter.split_video(input_path, output_dir, options)

    def test_split_video_invalid_output_dir(self) -> None:
        """존재하지 않는 출력 디렉토리."""
        with TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "input.mp4"
            input_path.write_text("fake video")
            output_dir = Path(tmpdir) / "nonexistent_output"

            options = SplitOptions(duration=3600)

            with pytest.raises(NotADirectoryError, match="Output directory not found"):
                self.splitter.split_video(input_path, output_dir, options)


class TestProbeDuration:
    """probe_duration 단위 테스트."""

    @patch("tubearchive.core.splitter.subprocess.run")
    def test_returns_duration_on_success(self, mock_run: MagicMock) -> None:
        """ffprobe 성공 시 올바른 duration을 반환한다."""
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"format": {"duration": "123.456"}}),
        )
        result = probe_duration(Path("/fake/video.mp4"))
        assert result == pytest.approx(123.456)
        mock_run.assert_called_once()

    @patch("tubearchive.core.splitter.subprocess.run")
    def test_returns_zero_on_ffprobe_failure(self, mock_run: MagicMock) -> None:
        """ffprobe 실패 시 0.0을 반환한다."""
        mock_run.side_effect = subprocess.CalledProcessError(1, "ffprobe")
        result = probe_duration(Path("/fake/video.mp4"))
        assert result == 0.0

    @patch("tubearchive.core.splitter.subprocess.run")
    def test_returns_zero_on_invalid_json(self, mock_run: MagicMock) -> None:
        """유효하지 않은 JSON 출력 시 0.0을 반환한다."""
        mock_run.return_value = MagicMock(stdout="not json")
        result = probe_duration(Path("/fake/video.mp4"))
        assert result == 0.0

    @patch("tubearchive.core.splitter.subprocess.run")
    def test_returns_zero_on_missing_duration(self, mock_run: MagicMock) -> None:
        """duration 필드가 없으면 0.0을 반환한다."""
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"format": {"filename": "test.mp4"}}),
        )
        result = probe_duration(Path("/fake/video.mp4"))
        assert result == 0.0

    @patch("tubearchive.core.splitter.subprocess.run")
    def test_returns_zero_on_non_dict_response(self, mock_run: MagicMock) -> None:
        """ffprobe 응답이 dict가 아니면 0.0을 반환한다."""
        mock_run.return_value = MagicMock(stdout=json.dumps([1, 2, 3]))
        assert probe_duration(Path("/fake/video.mp4")) == 0.0

    @patch("tubearchive.core.splitter.subprocess.run")
    def test_returns_zero_on_null_duration(self, mock_run: MagicMock) -> None:
        """duration이 null이면 0.0을 반환한다."""
        mock_run.return_value = MagicMock(
            stdout=json.dumps({"format": {"duration": None}}),
        )
        assert probe_duration(Path("/fake/video.mp4")) == 0.0

    @patch("tubearchive.core.splitter.subprocess.run")
    def test_returns_zero_on_empty_stdout(self, mock_run: MagicMock) -> None:
        """빈 stdout이면 0.0을 반환한다."""
        mock_run.return_value = MagicMock(stdout="")
        assert probe_duration(Path("/fake/video.mp4")) == 0.0
