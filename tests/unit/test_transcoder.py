"""트랜스코더 loudnorm 통합 테스트."""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestTranscoderLoudnorm:
    """Transcoder의 loudnorm 2-pass 통합 테스트."""

    @pytest.fixture
    def mock_transcoder(self, tmp_path: Path) -> Generator[MagicMock]:
        """DB/FFmpeg를 mock한 Transcoder 인스턴스."""
        with (
            patch("tubearchive.core.transcoder.init_database"),
            patch("tubearchive.core.transcoder.VideoRepository"),
            patch("tubearchive.core.transcoder.TranscodingJobRepository") as mock_job_repo_cls,
            patch("tubearchive.core.transcoder.ResumeManager") as mock_resume_cls,
        ):
            from tubearchive.core.transcoder import Transcoder

            t = Transcoder(db_path=tmp_path / "test.db", temp_dir=tmp_path)
            t.executor = MagicMock()

            # resume_mgr mocks
            mock_resume = mock_resume_cls.return_value
            mock_resume.is_video_processed.return_value = False
            mock_resume.get_or_create_job.return_value = 1

            # job_repo mocks
            mock_job_repo = mock_job_repo_cls.return_value
            mock_job = MagicMock()
            mock_job.status = MagicMock()
            mock_job.status.__eq__ = lambda self, other: False
            mock_job.progress_percent = 0
            mock_job_repo.get_by_id.return_value = mock_job

            # video_repo mock
            t.video_repo.get_by_path.return_value = None
            t.video_repo.insert.return_value = 1

            yield t

    @pytest.fixture
    def mock_metadata(self) -> MagicMock:
        """메타데이터 mock."""
        meta = MagicMock()
        meta.width = 3840
        meta.height = 2160
        meta.is_portrait = False
        meta.duration_seconds = 60.0
        meta.device_model = "TestCam"
        meta.color_transfer = "bt709"
        return meta

    @pytest.fixture
    def video_file(self, tmp_path: Path) -> MagicMock:
        """VideoFile mock."""
        from tubearchive.models.video import VideoFile

        vf = MagicMock(spec=VideoFile)
        vf.path = tmp_path / "test_video.mp4"
        return vf

    @pytest.fixture
    def loudnorm_stderr(self) -> str:
        """FFmpeg loudnorm 분석 stderr 출력 예시."""
        return """
[Parsed_loudnorm_0 @ 0x7f9b4] Summary:
{
\t"input_i" : "-24.50",
\t"input_tp" : "-3.20",
\t"input_lra" : "8.10",
\t"input_thresh" : "-35.00",
\t"output_i" : "-14.00",
\t"output_tp" : "-1.50",
\t"output_lra" : "7.00",
\t"output_thresh" : "-24.50",
\t"normalization_type" : "dynamic",
\t"target_offset" : "0.50"
}
"""

    def test_transcode_without_normalize_skips_analysis(
        self,
        mock_transcoder: MagicMock,
        mock_metadata: MagicMock,
        video_file: MagicMock,
    ) -> None:
        """normalize_audio=False이면 분석 패스를 실행하지 않는다."""
        with patch("tubearchive.core.transcoder.detect_metadata", return_value=mock_metadata):
            mock_transcoder.executor.run.return_value = None
            with contextlib.suppress(Exception):
                mock_transcoder.transcode_video(video_file, normalize_audio=False)

        mock_transcoder.executor.build_loudness_analysis_command.assert_not_called()

    def test_transcode_with_normalize_runs_analysis(
        self,
        mock_transcoder: MagicMock,
        mock_metadata: MagicMock,
        video_file: MagicMock,
        loudnorm_stderr: str,
    ) -> None:
        """normalize_audio=True이면 loudness 분석 패스를 실행한다."""
        with patch("tubearchive.core.transcoder.detect_metadata", return_value=mock_metadata):
            mock_transcoder.executor.run.return_value = None
            mock_transcoder.executor.run_analysis.return_value = loudnorm_stderr
            mock_transcoder.executor.build_loudness_analysis_command.return_value = [
                "ffmpeg",
                "-i",
                "test.mp4",
                "-af",
                "loudnorm",
                "-vn",
                "-f",
                "null",
                "-",
            ]
            with contextlib.suppress(Exception):
                mock_transcoder.transcode_video(video_file, normalize_audio=True)

        mock_transcoder.executor.build_loudness_analysis_command.assert_called_once()
        mock_transcoder.executor.run_analysis.assert_called_once()

    def test_normalize_passes_analysis_to_combined_filter(
        self,
        mock_transcoder: MagicMock,
        mock_metadata: MagicMock,
        video_file: MagicMock,
        loudnorm_stderr: str,
    ) -> None:
        """normalize_audio=True이면 분석 결과를 create_combined_filter에 전달한다."""
        with (
            patch("tubearchive.core.transcoder.detect_metadata", return_value=mock_metadata),
            patch("tubearchive.core.transcoder.create_combined_filter") as mock_filter,
        ):
            mock_filter.return_value = ("scale=3840:2160", "afade=t=in:st=0:d=0.5")
            mock_transcoder.executor.run.return_value = None
            mock_transcoder.executor.run_analysis.return_value = loudnorm_stderr
            mock_transcoder.executor.build_loudness_analysis_command.return_value = ["ffmpeg"]
            with contextlib.suppress(Exception):
                mock_transcoder.transcode_video(video_file, normalize_audio=True)

        call_kwargs = mock_filter.call_args
        assert call_kwargs is not None
        loudnorm_arg = call_kwargs.kwargs.get("loudnorm_analysis")
        assert loudnorm_arg is not None
        assert loudnorm_arg.input_i == pytest.approx(-24.5)
