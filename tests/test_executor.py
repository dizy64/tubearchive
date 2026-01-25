"""FFmpeg 실행기 테스트."""


import pytest

from tubearchive.ffmpeg.executor import (
    FFmpegExecutor,
    parse_progress_line,
)


class TestParseProgressLine:
    """진행률 파싱 테스트."""

    def test_parse_time_from_progress_line(self) -> None:
        """time= 파싱."""
        line = "frame= 1234 fps= 60 q=28.0 size=  123456kB time=00:01:30.50 bitrate=50000.0kbits/s"
        result = parse_progress_line(line)

        assert result is not None
        assert result["time_seconds"] == pytest.approx(90.5, rel=0.01)

    def test_parse_frame_from_progress_line(self) -> None:
        """frame= 파싱."""
        line = "frame= 1234 fps= 60 q=28.0 size=  123456kB time=00:01:30.50"
        result = parse_progress_line(line)

        assert result is not None
        assert result["frame"] == 1234

    def test_parse_fps_from_progress_line(self) -> None:
        """fps= 파싱."""
        line = "frame= 1234 fps= 60.5 q=28.0 size=  123456kB time=00:01:30.50"
        result = parse_progress_line(line)

        assert result is not None
        assert result["fps"] == pytest.approx(60.5, rel=0.01)

    def test_parse_bitrate_from_progress_line(self) -> None:
        """bitrate= 파싱."""
        line = "frame= 1234 fps= 60 q=28.0 size=  123456kB time=00:01:30.50 bitrate=50000.0kbits/s"
        result = parse_progress_line(line)

        assert result is not None
        assert result["bitrate"] == pytest.approx(50000.0, rel=0.01)

    def test_returns_none_for_non_progress_line(self) -> None:
        """진행률 라인이 아니면 None."""
        line = "Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'input.mp4':"
        result = parse_progress_line(line)

        assert result is None

    def test_handles_na_time(self) -> None:
        """time=N/A 처리."""
        line = "frame=    0 fps=0.0 q=0.0 size=       0kB time=N/A"
        result = parse_progress_line(line)

        assert result is None or result.get("time_seconds") is None


class TestFFmpegExecutor:
    """FFmpegExecutor 테스트."""

    @pytest.fixture
    def executor(self) -> FFmpegExecutor:
        """FFmpegExecutor 인스턴스."""
        return FFmpegExecutor()

    def test_build_transcode_command(self, executor: FFmpegExecutor) -> None:
        """트랜스코딩 명령어 빌드."""
        from pathlib import Path

        from tubearchive.ffmpeg.profiles import PROFILE_4K_HEVC_VT

        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_4K_HEVC_VT,
            video_filter="scale=3840:2160",
            audio_filter="afade=t=in:st=0:d=0.5",
        )

        assert "ffmpeg" in cmd[0]
        assert "-i" in cmd
        assert "/input/video.mp4" in cmd
        assert "/output/video.mp4" in cmd
        assert "-c:v" in cmd
        assert "hevc_videotoolbox" in cmd
        assert "-vf" in cmd
        assert "scale=3840:2160" in cmd
        assert "-af" in cmd

    def test_build_command_with_filter_complex(self, executor: FFmpegExecutor) -> None:
        """filter_complex 사용 시."""
        from pathlib import Path

        from tubearchive.ffmpeg.profiles import PROFILE_4K_HEVC_VT

        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_4K_HEVC_VT,
            filter_complex="[0:v]split=2[bg][fg];...[v_out]",
            audio_filter="afade=t=in:st=0:d=0.5",
        )

        assert "-filter_complex" in cmd
        assert "-map" in cmd

    def test_build_command_overwrite(self, executor: FFmpegExecutor) -> None:
        """덮어쓰기 옵션."""
        from pathlib import Path

        from tubearchive.ffmpeg.profiles import PROFILE_4K_HEVC_VT

        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_4K_HEVC_VT,
            overwrite=True,
        )

        assert "-y" in cmd

    def test_calculate_progress_percent(self, executor: FFmpegExecutor) -> None:
        """진행률 계산."""
        percent = executor.calculate_progress_percent(
            current_time=60.0,
            total_duration=120.0,
        )

        assert percent == 50

    def test_calculate_progress_percent_clamps_to_100(self, executor: FFmpegExecutor) -> None:
        """100% 초과 방지."""
        percent = executor.calculate_progress_percent(
            current_time=130.0,
            total_duration=120.0,
        )

        assert percent == 100

    def test_calculate_progress_percent_zero_duration(self, executor: FFmpegExecutor) -> None:
        """0초 duration 처리."""
        percent = executor.calculate_progress_percent(
            current_time=10.0,
            total_duration=0.0,
        )

        assert percent == 0
