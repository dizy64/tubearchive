"""FFmpeg 실행기 테스트."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.ffmpeg.executor import (
    FFmpegError,
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

        from tubearchive.ffmpeg.profiles import PROFILE_SDR

        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_SDR,
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

        from tubearchive.ffmpeg.profiles import PROFILE_SDR

        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_SDR,
            filter_complex="[0:v]split=2[bg][fg];...[v_out]",
            audio_filter="afade=t=in:st=0:d=0.5",
        )

        assert "-filter_complex" in cmd
        assert "-map" in cmd

    def test_build_command_overwrite(self, executor: FFmpegExecutor) -> None:
        """덮어쓰기 옵션."""
        from pathlib import Path

        from tubearchive.ffmpeg.profiles import PROFILE_SDR

        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_SDR,
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


class TestBuildLoudnessAnalysisCommand:
    """build_loudness_analysis_command 테스트."""

    def test_command_structure(self) -> None:
        """명령어 기본 구조: -i, -af, -vn, -f null os.devnull."""
        import os
        from pathlib import Path

        executor = FFmpegExecutor()
        cmd = executor.build_loudness_analysis_command(
            input_path=Path("/test/input.mp4"),
            audio_filter="loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
        )
        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        assert "/test/input.mp4" in cmd
        assert "-af" in cmd
        assert "-vn" in cmd
        assert "-f" in cmd
        assert "null" in cmd
        assert os.devnull in cmd

    def test_audio_filter_placement(self) -> None:
        """-af 뒤에 필터 문자열 위치."""
        from pathlib import Path

        executor = FFmpegExecutor()
        audio_filter = "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json"
        cmd = executor.build_loudness_analysis_command(
            input_path=Path("/test/input.mp4"),
            audio_filter=audio_filter,
        )
        af_index = cmd.index("-af")
        assert cmd[af_index + 1] == audio_filter

    def test_no_video_output(self) -> None:
        """-vn으로 비디오 출력 없음."""
        import os
        from pathlib import Path

        executor = FFmpegExecutor()
        cmd = executor.build_loudness_analysis_command(
            input_path=Path("/test/input.mp4"),
            audio_filter="loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
        )
        assert "-vn" in cmd
        null_index = cmd.index("-f")
        assert cmd[null_index + 1] == "null"
        assert cmd[null_index + 2] == os.devnull

    def test_custom_ffmpeg_path(self) -> None:
        """커스텀 ffmpeg 경로."""
        from pathlib import Path

        executor = FFmpegExecutor(ffmpeg_path="/usr/local/bin/ffmpeg")
        cmd = executor.build_loudness_analysis_command(
            input_path=Path("/test/input.mp4"),
            audio_filter="loudnorm=I=-14:print_format=json",
        )
        assert cmd[0] == "/usr/local/bin/ffmpeg"


class TestRunAnalysis:
    """run_analysis 테스트."""

    def test_returns_stderr_on_success(self) -> None:
        """성공 시 stderr 전체 반환."""
        executor = FFmpegExecutor()
        expected_stderr = '{"input_i": "-20.0"}'

        with patch("tubearchive.ffmpeg.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stderr=expected_stderr,
                stdout="",
            )
            result = executor.run_analysis(["ffmpeg", "-i", "test.mp4"])
            assert result == expected_stderr

    def test_raises_on_failure(self) -> None:
        """실패 시 FFmpegError 발생."""
        executor = FFmpegExecutor()

        with patch("tubearchive.ffmpeg.executor.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stderr="Error: no audio stream",
                stdout="",
            )
            with pytest.raises(FFmpegError, match="exit code 1"):
                executor.run_analysis(["ffmpeg", "-i", "test.mp4"])


class TestBuildVidstabDetectCommand:
    """vidstab detect 명령 빌드 테스트."""

    def test_command_structure(self) -> None:
        """기본 명령 구조 검증 (-vf, -an, -f null)."""
        executor = FFmpegExecutor()
        cmd = executor.build_vidstab_detect_command(
            input_path=Path("/tmp/input.mp4"),
            video_filter="vidstabdetect=shakiness=5:accuracy=9:result=/tmp/test.trf",
        )

        assert cmd[0] == "ffmpeg"
        assert "-i" in cmd
        assert "/tmp/input.mp4" in cmd
        assert "-vf" in cmd
        assert "-an" in cmd
        assert "-f" in cmd
        assert "null" in cmd

    def test_video_filter_value(self) -> None:
        """비디오 필터 값이 올바르게 전달된다."""
        executor = FFmpegExecutor()
        vf = "vidstabdetect=shakiness=8:accuracy=15:result=/tmp/vid.trf"
        cmd = executor.build_vidstab_detect_command(
            input_path=Path("/tmp/test.mp4"),
            video_filter=vf,
        )

        vf_idx = cmd.index("-vf")
        assert cmd[vf_idx + 1] == vf

    def test_custom_ffmpeg_path(self) -> None:
        """커스텀 ffmpeg 경로 사용."""
        executor = FFmpegExecutor(ffmpeg_path="/usr/local/bin/ffmpeg")
        cmd = executor.build_vidstab_detect_command(
            input_path=Path("/tmp/input.mp4"),
            video_filter="vidstabdetect",
        )

        assert cmd[0] == "/usr/local/bin/ffmpeg"

    def test_no_audio_flag_present(self) -> None:
        """-an 플래그 존재 (오디오 무시)."""
        executor = FFmpegExecutor()
        cmd = executor.build_vidstab_detect_command(
            input_path=Path("/tmp/input.mp4"),
            video_filter="vidstabdetect",
        )

        assert "-an" in cmd
        assert "-vn" not in cmd  # 비디오 분석이므로 -vn은 없어야 함

    def test_trf_path_in_filter(self) -> None:
        """trf 경로가 필터 문자열에 포함된다."""
        executor = FFmpegExecutor()
        trf_path = "/tmp/vidstab_test.trf"
        vf = f"vidstabdetect=result={trf_path}"
        cmd = executor.build_vidstab_detect_command(
            input_path=Path("/tmp/input.mp4"),
            video_filter=vf,
        )

        vf_idx = cmd.index("-vf")
        assert trf_path in cmd[vf_idx + 1]


class TestBuildTranscodeNoAudio:
    """오디오 스트림이 없는 영상의 트랜스코딩 명령 빌드 테스트."""

    def test_no_audio_with_video_filter_generates_silent_audio(self) -> None:
        """has_audio=False + video_filter 시 anullsrc로 무음 생성."""
        from tubearchive.ffmpeg.profiles import PROFILE_SDR

        executor = FFmpegExecutor()
        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_SDR,
            video_filter="scale=3840:2160",
            has_audio=False,
        )

        # anullsrc 입력이 추가되어야 한다
        assert "anullsrc" in " ".join(cmd)
        # 0:a:0 매핑이 없어야 한다
        assert "0:a:0" not in cmd
        # 무음 입력에서 오디오를 매핑해야 한다
        assert "1:a:0" in cmd
        # -shortest 플래그로 비디오 길이에 맞춰야 한다
        assert "-shortest" in cmd

    def test_no_audio_with_filter_complex_generates_silent_audio(self) -> None:
        """has_audio=False + filter_complex 시 anullsrc로 무음 생성."""
        from tubearchive.ffmpeg.profiles import PROFILE_SDR

        executor = FFmpegExecutor()
        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_SDR,
            filter_complex="[0:v]split=2[bg][fg];...[v_out]",
            has_audio=False,
        )

        # anullsrc 입력이 추가되어야 한다
        assert "anullsrc" in " ".join(cmd)
        # 0:a:0 매핑이 없어야 한다
        assert "0:a:0" not in cmd
        # 무음 입력에서 오디오를 매핑해야 한다
        assert "1:a:0" in cmd
        # -shortest 플래그
        assert "-shortest" in cmd

    def test_has_audio_true_maps_input_audio(self) -> None:
        """has_audio=True (기본값) 시 기존 방식대로 0:a:0 매핑."""
        from tubearchive.ffmpeg.profiles import PROFILE_SDR

        executor = FFmpegExecutor()
        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_SDR,
            video_filter="scale=3840:2160",
            has_audio=True,
        )

        assert "0:a:0" in cmd
        assert "anullsrc" not in " ".join(cmd)
        assert "-shortest" not in cmd

    def test_default_has_audio_is_true(self) -> None:
        """has_audio 미지정 시 기본값 True (기존 동작 호환)."""
        from tubearchive.ffmpeg.profiles import PROFILE_SDR

        executor = FFmpegExecutor()
        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_SDR,
            video_filter="scale=3840:2160",
        )

        # 기존 동작: 0:a:0 매핑
        assert "0:a:0" in cmd

    def test_no_audio_no_audio_filter_applied(self) -> None:
        """has_audio=False 시 -af 오디오 필터가 적용되지 않아야 한다."""
        from tubearchive.ffmpeg.profiles import PROFILE_SDR

        executor = FFmpegExecutor()
        cmd = executor.build_transcode_command(
            input_path=Path("/input/video.mp4"),
            output_path=Path("/output/video.mp4"),
            profile=PROFILE_SDR,
            video_filter="scale=3840:2160",
            audio_filter="afade=t=in:st=0:d=0.5",
            has_audio=False,
        )

        # 오디오 필터가 무음에 적용될 필요 없으므로 -af 없어야 한다
        assert "-af" not in cmd


class TestBuildConcatCommand:
    """concat 명령어 빌드 테스트."""

    @pytest.fixture
    def executor(self) -> FFmpegExecutor:
        return FFmpegExecutor()

    def test_basic_structure(self, executor: FFmpegExecutor, tmp_path: Path) -> None:
        """기본 concat 명령어 구조."""
        concat_file = tmp_path / "concat.txt"
        output_path = tmp_path / "output.mp4"
        cmd = executor.build_concat_command(concat_file, output_path)

        assert "-f" in cmd
        assert "concat" in cmd
        assert "-safe" in cmd
        assert "0" in cmd
        assert "-c" in cmd
        assert "copy" in cmd
        assert str(concat_file) in cmd
        assert str(output_path) in cmd

    def test_with_overwrite(self, executor: FFmpegExecutor, tmp_path: Path) -> None:
        """덮어쓰기 옵션 포함."""
        cmd = executor.build_concat_command(
            tmp_path / "concat.txt", tmp_path / "output.mp4", overwrite=True
        )
        assert "-y" in cmd

    def test_without_overwrite(self, executor: FFmpegExecutor, tmp_path: Path) -> None:
        """덮어쓰기 옵션 미포함."""
        cmd = executor.build_concat_command(
            tmp_path / "concat.txt", tmp_path / "output.mp4", overwrite=False
        )
        assert "-y" not in cmd


class TestBuildSilenceDetectionCommand:
    """무음 감지 명령어 빌드 테스트."""

    @pytest.fixture
    def executor(self) -> FFmpegExecutor:
        return FFmpegExecutor()

    def test_basic_structure(self, executor: FFmpegExecutor, tmp_path: Path) -> None:
        """기본 무음 감지 명령어 구조."""
        input_path = tmp_path / "input.mp4"
        cmd = executor.build_silence_detection_command(input_path, "silencedetect=n=-30dB:d=2")

        assert "-af" in cmd
        assert "-vn" in cmd
        assert "-f" in cmd
        assert "null" in cmd

    def test_filter_value_position(self, executor: FFmpegExecutor, tmp_path: Path) -> None:
        """필터 문자열이 -af 뒤에 위치."""
        input_path = tmp_path / "input.mp4"
        filter_str = "silencedetect=n=-30dB:d=2"
        cmd = executor.build_silence_detection_command(input_path, filter_str)

        af_idx = cmd.index("-af")
        assert cmd[af_idx + 1] == filter_str


class TestFFmpegErrorStr:
    """FFmpegError 문자열 포맷 테스트."""

    def test_without_stderr(self) -> None:
        """stderr 없는 에러 메시지."""
        error = FFmpegError("FFmpeg failed with exit code 1")
        assert str(error) == "FFmpeg failed with exit code 1"

    def test_with_short_stderr(self) -> None:
        """짧은 stderr 포함."""
        stderr = "Error opening input\nNo such file"
        error = FFmpegError("FFmpeg failed", stderr=stderr)
        result = str(error)

        assert "FFmpeg failed" in result
        assert "Error opening input" in result
        assert "No such file" in result

    def test_truncates_long_stderr(self) -> None:
        """20줄 초과 stderr은 마지막 20줄만 표시."""
        lines = [f"line {i}" for i in range(30)]
        stderr = "\n".join(lines)
        error = FFmpegError("FFmpeg failed", stderr=stderr)
        result = str(error)

        assert "last 20 lines" in result
        assert "line 29" in result
        assert "line 10" in result
        # 처음 10줄은 포함되지 않아야 함
        assert "line 9" not in result
