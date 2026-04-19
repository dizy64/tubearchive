"""영상 병합기 테스트."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.domain.media.merger import (
    Merger,
    create_concat_file,
    probe_audio_sample_rate,
    probe_stream_durations,
)


class TestCreateConcatFile:
    """concat 파일 생성 테스트."""

    def test_creates_concat_file_with_paths(self, tmp_path: Path) -> None:
        """파일 경로 목록으로 concat 파일 생성."""
        video_paths = [
            tmp_path / "video1.mp4",
            tmp_path / "video2.mp4",
            tmp_path / "video3.mp4",
        ]
        for p in video_paths:
            p.touch()

        concat_file = create_concat_file(video_paths, tmp_path)

        assert concat_file.exists()
        content = concat_file.read_text()
        assert f"file '{video_paths[0]}'" in content
        assert f"file '{video_paths[1]}'" in content
        assert f"file '{video_paths[2]}'" in content

    def test_concat_file_preserves_order(self, tmp_path: Path) -> None:
        """파일 순서 보존."""
        video_paths = [
            tmp_path / "first.mp4",
            tmp_path / "second.mp4",
            tmp_path / "third.mp4",
        ]
        for p in video_paths:
            p.touch()

        concat_file = create_concat_file(video_paths, tmp_path)

        lines = concat_file.read_text().strip().split("\n")
        assert "first.mp4" in lines[0]
        assert "second.mp4" in lines[1]
        assert "third.mp4" in lines[2]

    def test_handles_paths_with_spaces(self, tmp_path: Path) -> None:
        """공백이 포함된 경로 처리."""
        video_path = tmp_path / "my video file.mp4"
        video_path.touch()

        concat_file = create_concat_file([video_path], tmp_path)

        content = concat_file.read_text()
        assert "my video file.mp4" in content

    def test_empty_list_raises_error(self, tmp_path: Path) -> None:
        """빈 리스트는 에러."""
        with pytest.raises(ValueError, match="No video files"):
            create_concat_file([], tmp_path)


class TestProbeAudioSampleRate:
    """probe_audio_sample_rate 테스트."""

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_returns_sample_rate_on_success(self, mock_run: MagicMock) -> None:
        """정상 조회 시 샘플레이트 반환."""
        mock_run.return_value = MagicMock(returncode=0, stdout="48000\n")
        result = probe_audio_sample_rate(Path("/fake/video.mp4"))
        assert result == 48000

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_returns_96000_for_loudnorm_upsampled(self, mock_run: MagicMock) -> None:
        """loudnorm 업샘플링된 파일의 96000 Hz 반환."""
        mock_run.return_value = MagicMock(returncode=0, stdout="96000\n")
        result = probe_audio_sample_rate(Path("/fake/video.mp4"))
        assert result == 96000

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_returns_none_on_nonzero_returncode(self, mock_run: MagicMock) -> None:
        """ffprobe 실패 시 None 반환."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = probe_audio_sample_rate(Path("/fake/video.mp4"))
        assert result is None

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_returns_none_on_empty_stdout(self, mock_run: MagicMock) -> None:
        """오디오 스트림 없어 빈 출력 시 None 반환."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        result = probe_audio_sample_rate(Path("/fake/video.mp4"))
        assert result is None

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_returns_none_on_whitespace_only_stdout(self, mock_run: MagicMock) -> None:
        """공백만 있는 출력 시 None 반환."""
        mock_run.return_value = MagicMock(returncode=0, stdout="   \n")
        result = probe_audio_sample_rate(Path("/fake/video.mp4"))
        assert result is None

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_returns_none_on_invalid_output(self, mock_run: MagicMock) -> None:
        """파싱 불가 출력 시 None 반환."""
        mock_run.return_value = MagicMock(returncode=0, stdout="not_a_number\n")
        result = probe_audio_sample_rate(Path("/fake/video.mp4"))
        assert result is None

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_passes_custom_ffprobe_path(self, mock_run: MagicMock) -> None:
        """커스텀 ffprobe 경로 사용."""
        mock_run.return_value = MagicMock(returncode=0, stdout="44100\n")
        probe_audio_sample_rate(Path("/fake/video.mp4"), ffprobe_path="/usr/local/bin/ffprobe")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/ffprobe"


class TestProbeStreamDurations:
    """probe_stream_durations 테스트."""

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_returns_both_stream_durations(self, mock_run: MagicMock) -> None:
        """비디오·오디오 길이 모두 반환."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="video,60.5\naudio,59.8\n",
        )
        result = probe_stream_durations(Path("/fake/output.mp4"))
        assert result == {"video": 60.5, "audio": 59.8}

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_returns_video_only_when_no_audio(self, mock_run: MagicMock) -> None:
        """오디오 스트림 없는 경우 video 키만 반환."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="video,120.0\n",
        )
        result = probe_stream_durations(Path("/fake/output.mp4"))
        assert result == {"video": 120.0}
        assert "audio" not in result

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_returns_empty_dict_on_failure(self, mock_run: MagicMock) -> None:
        """ffprobe 실패 시 빈 딕셔너리 반환."""
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = probe_stream_durations(Path("/fake/output.mp4"))
        assert result == {}

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_skips_invalid_lines(self, mock_run: MagicMock) -> None:
        """파싱 불가 줄 건너뜀."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="video,60.0\nbadline\naudio,58.5\n",
        )
        result = probe_stream_durations(Path("/fake/output.mp4"))
        assert result == {"video": 60.0, "audio": 58.5}

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_skips_non_float_duration(self, mock_run: MagicMock) -> None:
        """duration이 float이 아닌 줄 건너뜀."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="video,N/A\naudio,59.0\n",
        )
        result = probe_stream_durations(Path("/fake/output.mp4"))
        assert "video" not in result
        assert result.get("audio") == 59.0

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_passes_custom_ffprobe_path(self, mock_run: MagicMock) -> None:
        """커스텀 ffprobe 경로 사용."""
        mock_run.return_value = MagicMock(returncode=0, stdout="video,10.0\n")
        probe_stream_durations(Path("/fake/output.mp4"), ffprobe_path="/usr/local/bin/ffprobe")
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/usr/local/bin/ffprobe"


class TestMergerCheckSampleRates:
    """Merger._check_sample_rates 테스트."""

    @pytest.fixture
    def merger(self, tmp_path: Path) -> Merger:
        return Merger(temp_dir=tmp_path)

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_logs_warning_on_mismatch(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """샘플레이트 불일치 시 경고 로그 출력."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="96000\n"),  # 첫 파일: 96kHz (loudnorm 업샘플)
            MagicMock(returncode=0, stdout="48000\n"),  # 두 번째 파일: 48kHz
        ]
        paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]

        import logging

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_sample_rates(paths)

        assert any("mismatch" in r.message.lower() for r in caplog.records)

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_no_warning_when_rates_consistent(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """샘플레이트 일치 시 경고 없음."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="48000\n"),
            MagicMock(returncode=0, stdout="48000\n"),
        ]
        paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]

        import logging

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_sample_rates(paths)

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_no_op_when_no_audio_streams(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """오디오 스트림 없는 파일만 있으면 아무것도 하지 않음."""
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]

        import logging

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_sample_rates(paths)

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_uses_merger_ffprobe_path(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Merger의 ffprobe_path 설정이 probe 호출에 반영됨."""
        mock_run.return_value = MagicMock(returncode=0, stdout="48000\n")
        merger = Merger(ffprobe_path="/custom/ffprobe", temp_dir=tmp_path)
        merger._check_sample_rates([tmp_path / "a.mp4"])
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/custom/ffprobe"


class TestMergerCheckMergedDurations:
    """Merger._check_merged_durations 테스트."""

    @pytest.fixture
    def merger(self, tmp_path: Path) -> Merger:
        return Merger(temp_dir=tmp_path)

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_logs_warning_when_diff_exceeds_threshold(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """오디오/비디오 길이 차이가 임계값 초과 시 경고."""
        # 비디오 6244초, 오디오 5028초 → 차이 1216초 > 5초 임계값
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="video,6244.0\naudio,5028.0\n",
        )
        import logging

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_merged_durations(tmp_path / "output.mp4")

        assert any(r.levelno >= logging.WARNING for r in caplog.records)
        assert any("truncated" in r.message.lower() for r in caplog.records)

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_no_warning_when_diff_within_threshold(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """차이가 임계값(5초) 이하 시 경고 없음."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="video,6244.0\naudio,6242.0\n",  # 차이 2초
        )
        import logging

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_merged_durations(tmp_path / "output.mp4")

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_skips_check_when_video_stream_missing(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """비디오 스트림 없으면 비교 생략."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="audio,60.0\n",
        )
        import logging

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_merged_durations(tmp_path / "output.mp4")

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_skips_check_when_audio_stream_missing(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """오디오 스트림 없으면 비교 생략 (영상 전용 파일)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="video,60.0\n",
        )
        import logging

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_merged_durations(tmp_path / "output.mp4")

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_exactly_at_threshold_does_not_warn(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """차이가 정확히 임계값(5.0초)이면 경고 없음."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="video,100.0\naudio,95.0\n",  # 차이 = 5.0 (초과 아님)
        )
        import logging

        with caplog.at_level(logging.WARNING, logger="tubearchive.domain.media.merger"):
            merger._check_merged_durations(tmp_path / "output.mp4")

        assert not any(r.levelno >= logging.WARNING for r in caplog.records)


class TestMerger:
    """Merger 클래스 통합 테스트."""

    @pytest.fixture
    def merger(self, tmp_path: Path) -> Merger:
        """Merger 인스턴스."""
        return Merger(temp_dir=tmp_path)

    def test_requires_temp_dir(self) -> None:
        """temp_dir 없으면 에러."""
        with pytest.raises(ValueError):
            Merger(temp_dir=None)  # type: ignore[arg-type]

    def test_build_merge_command(self, merger: Merger, tmp_path: Path) -> None:
        """병합 명령어 빌드."""
        concat_file = tmp_path / "concat.txt"
        concat_file.touch()
        output_path = tmp_path / "merged.mp4"

        cmd = merger.build_merge_command(concat_file, output_path)

        assert "ffmpeg" in cmd[0]
        assert "-f" in cmd
        assert "concat" in cmd
        assert "-safe" in cmd
        assert "0" in cmd
        assert "-c" in cmd
        assert "copy" in cmd
        assert str(concat_file) in cmd
        assert str(output_path) in cmd

    def test_build_merge_command_with_overwrite(self, merger: Merger, tmp_path: Path) -> None:
        """덮어쓰기 옵션."""
        concat_file = tmp_path / "concat.txt"
        concat_file.touch()
        output_path = tmp_path / "merged.mp4"

        cmd = merger.build_merge_command(concat_file, output_path, overwrite=True)

        assert "-y" in cmd

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_merge_videos(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
    ) -> None:
        """영상 병합 실행: 샘플레이트 probe → 병합 → 길이 검증 순으로 subprocess 호출."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="48000\n"),  # probe file1 sample rate
            MagicMock(returncode=0, stdout="48000\n"),  # probe file2 sample rate
            MagicMock(returncode=0, stderr="", stdout=""),  # ffmpeg merge
            MagicMock(returncode=0, stdout="video,60.0\naudio,60.0\n"),  # probe output durations
        ]

        video_paths = [
            tmp_path / "video1.mp4",
            tmp_path / "video2.mp4",
        ]
        for p in video_paths:
            p.touch()

        output_path = tmp_path / "merged.mp4"
        result = merger.merge(video_paths, output_path)

        assert result == output_path
        # probe x2 + merge x1 + duration probe x1
        assert mock_run.call_count == 4
        # 세 번째 호출이 실제 ffmpeg 병합
        merge_cmd = mock_run.call_args_list[2][0][0]
        assert "ffmpeg" in merge_cmd[0]
        assert "-f" in merge_cmd
        assert "concat" in merge_cmd

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_merge_cleans_up_concat_file(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
    ) -> None:
        """병합 후 concat 파일 정리."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="48000\n"),
            MagicMock(returncode=0, stdout="48000\n"),
            MagicMock(returncode=0, stderr="", stdout=""),
            MagicMock(returncode=0, stdout="video,60.0\naudio,60.0\n"),
        ]

        video_paths = [tmp_path / "video1.mp4", tmp_path / "video2.mp4"]
        for p in video_paths:
            p.touch()
        output_path = tmp_path / "merged.mp4"

        merger.merge(video_paths, output_path)

        # concat 파일이 삭제되었는지 확인
        concat_files = list(merger.temp_dir.glob("concat_*.txt"))
        assert len(concat_files) == 0

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_merge_cleans_concat_file_even_on_failure(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
    ) -> None:
        """병합 실패 시에도 concat 파일 정리."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="48000\n"),
            MagicMock(returncode=0, stdout="48000\n"),
            MagicMock(returncode=1, stderr="Error: invalid input", stdout=""),
        ]

        video_paths = [tmp_path / "video1.mp4", tmp_path / "video2.mp4"]
        for p in video_paths:
            p.touch()
        output_path = tmp_path / "merged.mp4"

        with pytest.raises(RuntimeError):
            merger.merge(video_paths, output_path)

        concat_files = list(merger.temp_dir.glob("concat_*.txt"))
        assert len(concat_files) == 0

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_merge_failure_raises_error(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
    ) -> None:
        """병합 실패 시 RuntimeError."""
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout="48000\n"),
            MagicMock(returncode=0, stdout="48000\n"),
            MagicMock(returncode=1, stderr="Error: invalid input", stdout=""),
        ]

        video_paths = [tmp_path / "video1.mp4", tmp_path / "video2.mp4"]
        for p in video_paths:
            p.touch()
        output_path = tmp_path / "merged.mp4"

        with pytest.raises(RuntimeError, match="FFmpeg merge failed"):
            merger.merge(video_paths, output_path)

    def test_merge_with_single_file(self, merger: Merger, tmp_path: Path) -> None:
        """단일 파일은 복사만 (subprocess 호출 없음)."""
        video_path = tmp_path / "single.mp4"
        video_path.write_bytes(b"fake video content")
        output_path = tmp_path / "output.mp4"

        with patch("tubearchive.domain.media.merger.shutil.copy2") as mock_copy:
            result = merger.merge([video_path], output_path)

            mock_copy.assert_called_once_with(video_path, output_path)
            assert result == output_path

    def test_merge_empty_list_raises_error(self, merger: Merger, tmp_path: Path) -> None:
        """빈 리스트는 ValueError."""
        with pytest.raises(ValueError, match="No video files"):
            merger.merge([], tmp_path / "output.mp4")

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_merge_calls_check_sample_rates_before_merge(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
    ) -> None:
        """샘플레이트 검증이 병합 전에 호출됨."""
        call_order: list[str] = []

        def side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if "sample_rate" in cmd:
                call_order.append("probe_sample_rate")
                return MagicMock(returncode=0, stdout="48000\n")
            elif "codec_type,duration" in str(cmd):
                call_order.append("probe_duration")
                return MagicMock(returncode=0, stdout="video,60.0\naudio,60.0\n")
            else:
                call_order.append("ffmpeg_merge")
                return MagicMock(returncode=0, stderr="", stdout="")

        mock_run.side_effect = side_effect

        video_paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
        for p in video_paths:
            p.touch()

        merger.merge(video_paths, tmp_path / "out.mp4")

        # 샘플레이트 probe가 merge 앞에 와야 함
        merge_idx = call_order.index("ffmpeg_merge")
        sample_rate_indices = [i for i, v in enumerate(call_order) if v == "probe_sample_rate"]
        assert all(i < merge_idx for i in sample_rate_indices)

    @patch("tubearchive.domain.media.merger.subprocess.run")
    def test_merge_calls_duration_check_after_merge(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
    ) -> None:
        """오디오/비디오 길이 검증이 병합 후에 호출됨."""
        call_order: list[str] = []

        def side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if "sample_rate" in cmd:
                call_order.append("probe_sample_rate")
                return MagicMock(returncode=0, stdout="48000\n")
            elif "codec_type,duration" in str(cmd):
                call_order.append("probe_duration")
                return MagicMock(returncode=0, stdout="video,60.0\naudio,60.0\n")
            else:
                call_order.append("ffmpeg_merge")
                return MagicMock(returncode=0, stderr="", stdout="")

        mock_run.side_effect = side_effect

        video_paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
        for p in video_paths:
            p.touch()

        merger.merge(video_paths, tmp_path / "out.mp4")

        # duration probe가 merge 뒤에 와야 함
        merge_idx = call_order.index("ffmpeg_merge")
        duration_idx = call_order.index("probe_duration")
        assert duration_idx > merge_idx
