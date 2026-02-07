"""타임랩스 생성기 테스트."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.core.timelapse import RESOLUTION_PRESETS, TimelapseGenerator


class TestTimelapseGenerator:
    """TimelapseGenerator 클래스 테스트."""

    def test_init_creates_executor(self) -> None:
        """초기화 시 FFmpegExecutor 생성 확인."""
        generator = TimelapseGenerator()
        assert generator.executor is not None

    @patch("tubearchive.core.timelapse.detect_metadata")
    @patch("tubearchive.core.timelapse.FFmpegExecutor")
    def test_generate_calls_ffmpeg_with_correct_args(
        self,
        mock_executor_class: MagicMock,
        mock_detect_metadata: MagicMock,
        tmp_path: Path,
    ) -> None:
        """FFmpeg 명령이 올바른 인자로 호출되는지 확인."""
        # 준비
        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output_timelapse.mp4"
        input_path.write_text("dummy")

        mock_metadata = MagicMock()
        mock_metadata.duration_seconds = 120.0
        mock_detect_metadata.return_value = mock_metadata

        mock_executor = MagicMock()
        mock_executor_class.return_value = mock_executor
        output_path.write_text("dummy")  # FFmpegExecutor가 파일을 생성했다고 가정

        # 실행
        generator = TimelapseGenerator()
        result = generator.generate(
            input_path=input_path,
            output_path=output_path,
            speed=10,
            keep_audio=False,
        )

        # 검증
        assert result == output_path
        mock_executor.run.assert_called_once()
        cmd_args = mock_executor.run.call_args[1]["cmd"]

        # 비디오 필터 확인
        assert "-filter:v" in cmd_args
        filter_v_idx = cmd_args.index("-filter:v")
        assert "setpts=PTS/10" in cmd_args[filter_v_idx + 1]

        # 오디오 제거 확인 (keep_audio=False)
        assert "-an" in cmd_args

    @patch("tubearchive.core.timelapse.detect_metadata")
    @patch("tubearchive.core.timelapse.FFmpegExecutor")
    def test_generate_with_audio(
        self,
        mock_executor_class: MagicMock,
        mock_detect_metadata: MagicMock,
        tmp_path: Path,
    ) -> None:
        """오디오 유지 옵션 확인."""
        # 준비
        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output_timelapse.mp4"
        input_path.write_text("dummy")

        mock_metadata = MagicMock()
        mock_metadata.duration_seconds = 120.0
        mock_detect_metadata.return_value = mock_metadata

        mock_executor = MagicMock()
        mock_executor_class.return_value = mock_executor
        output_path.write_text("dummy")

        # 실행
        generator = TimelapseGenerator()
        generator.generate(
            input_path=input_path,
            output_path=output_path,
            speed=10,
            keep_audio=True,
        )

        # 검증
        cmd_args = mock_executor.run.call_args[1]["cmd"]

        # 오디오 필터 확인
        assert "-filter:a" in cmd_args
        # 오디오 코덱 확인
        assert "-c:a" in cmd_args
        codec_idx = cmd_args.index("-c:a")
        assert cmd_args[codec_idx + 1] == "aac"

    @patch("tubearchive.core.timelapse.detect_metadata")
    @patch("tubearchive.core.timelapse.FFmpegExecutor")
    def test_generate_with_resolution(
        self,
        mock_executor_class: MagicMock,
        mock_detect_metadata: MagicMock,
        tmp_path: Path,
    ) -> None:
        """해상도 변환 옵션 확인."""
        # 준비
        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output_timelapse.mp4"
        input_path.write_text("dummy")

        mock_metadata = MagicMock()
        mock_metadata.duration_seconds = 120.0
        mock_detect_metadata.return_value = mock_metadata

        mock_executor = MagicMock()
        mock_executor_class.return_value = mock_executor
        output_path.write_text("dummy")

        # 실행
        generator = TimelapseGenerator()
        generator.generate(
            input_path=input_path,
            output_path=output_path,
            speed=10,
            keep_audio=False,
            resolution="1080p",
        )

        # 검증
        cmd_args = mock_executor.run.call_args[1]["cmd"]
        filter_v_idx = cmd_args.index("-filter:v")
        video_filter = cmd_args[filter_v_idx + 1]

        # 스케일 필터 포함 확인
        assert "scale=1920:1080" in video_filter
        assert "pad=1920:1080" in video_filter

    def test_raises_on_nonexistent_input(self, tmp_path: Path) -> None:
        """존재하지 않는 입력 파일 시 에러."""
        generator = TimelapseGenerator()
        input_path = tmp_path / "nonexistent.mp4"
        output_path = tmp_path / "output.mp4"

        with pytest.raises(ValueError, match="not found"):
            generator.generate(
                input_path=input_path,
                output_path=output_path,
                speed=10,
            )

    def test_raises_on_invalid_speed(self, tmp_path: Path) -> None:
        """잘못된 배속 시 에러."""
        generator = TimelapseGenerator()
        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output.mp4"
        input_path.write_text("dummy")

        with pytest.raises(ValueError, match="must be between"):
            generator.generate(
                input_path=input_path,
                output_path=output_path,
                speed=1,
            )

        with pytest.raises(ValueError, match="must be between"):
            generator.generate(
                input_path=input_path,
                output_path=output_path,
                speed=61,
            )

    @patch("tubearchive.core.timelapse.detect_metadata")
    def test_warns_on_short_video(
        self,
        mock_detect_metadata: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """짧은 영상 시 경고."""
        input_path = tmp_path / "input.mp4"
        output_path = tmp_path / "output.mp4"
        input_path.write_text("dummy")

        mock_metadata = MagicMock()
        mock_metadata.duration_seconds = 3.0  # 5초 미만
        mock_detect_metadata.return_value = mock_metadata

        generator = TimelapseGenerator()

        with patch.object(generator.executor, "run"):
            output_path.write_text("dummy")
            generator.generate(
                input_path=input_path,
                output_path=output_path,
                speed=10,
            )

        # 경고 로그 확인
        assert "Short video" in caplog.text


class TestParseResolution:
    """해상도 파싱 테스트."""

    def test_parses_preset_4k(self) -> None:
        """4k 프리셋 파싱."""
        generator = TimelapseGenerator()
        width, height = generator._parse_resolution("4k")
        assert (width, height) == RESOLUTION_PRESETS["4k"]

    def test_parses_preset_1080p(self) -> None:
        """1080p 프리셋 파싱."""
        generator = TimelapseGenerator()
        width, height = generator._parse_resolution("1080p")
        assert (width, height) == (1920, 1080)

    def test_parses_preset_case_insensitive(self) -> None:
        """대소문자 무시."""
        generator = TimelapseGenerator()
        width, height = generator._parse_resolution("1080P")
        assert (width, height) == (1920, 1080)

    def test_parses_custom_resolution(self) -> None:
        """커스텀 해상도 (WIDTHxHEIGHT) 파싱."""
        generator = TimelapseGenerator()
        width, height = generator._parse_resolution("1280x720")
        assert (width, height) == (1280, 720)

    def test_parses_custom_resolution_case_insensitive(self) -> None:
        """커스텀 해상도 대소문자 무시."""
        generator = TimelapseGenerator()
        width, height = generator._parse_resolution("1280X720")
        assert (width, height) == (1280, 720)

    def test_raises_on_invalid_format(self) -> None:
        """잘못된 형식 시 에러."""
        generator = TimelapseGenerator()

        with pytest.raises(ValueError, match="Unsupported resolution"):
            generator._parse_resolution("invalid")

    def test_raises_on_negative_dimension(self) -> None:
        """음수 차원 시 에러."""
        generator = TimelapseGenerator()

        with pytest.raises(ValueError, match="Invalid resolution format"):
            generator._parse_resolution("-1920x1080")

    def test_raises_on_zero_dimension(self) -> None:
        """0 차원 시 에러."""
        generator = TimelapseGenerator()

        with pytest.raises(ValueError, match="Invalid resolution format"):
            generator._parse_resolution("0x1080")
