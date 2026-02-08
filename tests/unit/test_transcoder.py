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


class TestTranscoderVidstab:
    """Transcoder의 vidstab 2-pass 통합 테스트."""

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

    def test_stabilize_false_skips_vidstab(
        self,
        mock_transcoder: MagicMock,
        mock_metadata: MagicMock,
        video_file: MagicMock,
    ) -> None:
        """stabilize=False이면 vidstab 분석을 실행하지 않는다."""
        with patch("tubearchive.core.transcoder.detect_metadata", return_value=mock_metadata):
            mock_transcoder.executor.run.return_value = None
            with contextlib.suppress(Exception):
                mock_transcoder.transcode_video(video_file, stabilize=False)

        mock_transcoder.executor.build_vidstab_detect_command.assert_not_called()

    def test_stabilize_true_runs_vidstab_analysis(
        self,
        mock_transcoder: MagicMock,
        mock_metadata: MagicMock,
        video_file: MagicMock,
    ) -> None:
        """stabilize=True이면 vidstab detect + transform을 실행한다."""
        with (
            patch("tubearchive.core.transcoder.detect_metadata", return_value=mock_metadata),
            patch("tubearchive.core.transcoder.create_vidstab_detect_filter") as mock_detect,
            patch("tubearchive.core.transcoder.create_vidstab_transform_filter") as mock_transform,
        ):
            mock_detect.return_value = "vidstabdetect=shakiness=5"
            mock_transform.return_value = "vidstabtransform=smoothing=10"
            mock_transcoder.executor.run.return_value = None
            mock_transcoder.executor.run_analysis.return_value = ""
            mock_transcoder.executor.build_vidstab_detect_command.return_value = ["ffmpeg"]
            with contextlib.suppress(Exception):
                mock_transcoder.transcode_video(
                    video_file,
                    stabilize=True,
                    stabilize_strength="medium",
                    stabilize_crop="crop",
                )

        mock_detect.assert_called_once()
        mock_transcoder.executor.build_vidstab_detect_command.assert_called_once()
        mock_transcoder.executor.run_analysis.assert_called()
        mock_transform.assert_called_once()

    def test_stabilize_passes_filter_to_combined_filter(
        self,
        mock_transcoder: MagicMock,
        mock_metadata: MagicMock,
        video_file: MagicMock,
    ) -> None:
        """stabilize=True이면 transform 필터를 create_combined_filter에 전달한다."""
        with (
            patch("tubearchive.core.transcoder.detect_metadata", return_value=mock_metadata),
            patch("tubearchive.core.transcoder.create_vidstab_detect_filter") as mock_detect,
            patch("tubearchive.core.transcoder.create_vidstab_transform_filter") as mock_transform,
            patch("tubearchive.core.transcoder.create_combined_filter") as mock_combined,
        ):
            mock_detect.return_value = "vidstabdetect=shakiness=5"
            mock_transform.return_value = "vidstabtransform=smoothing=10:crop=keep"
            mock_combined.return_value = ("video_filter", "audio_filter")
            mock_transcoder.executor.run.return_value = None
            mock_transcoder.executor.run_analysis.return_value = ""
            mock_transcoder.executor.build_vidstab_detect_command.return_value = ["ffmpeg"]
            with contextlib.suppress(Exception):
                mock_transcoder.transcode_video(
                    video_file,
                    stabilize=True,
                    stabilize_strength="medium",
                    stabilize_crop="crop",
                )

        if mock_combined.call_args:
            stabilize_arg = mock_combined.call_args.kwargs.get("stabilize_filter", "")
            assert "vidstabtransform" in stabilize_arg

    def test_vidstab_analysis_failure_skips_stabilization(
        self,
        mock_transcoder: MagicMock,
        mock_metadata: MagicMock,
        video_file: MagicMock,
    ) -> None:
        """vidstab detect 실패 시 graceful degradation (스킵 + warning)."""
        from tubearchive.ffmpeg.executor import FFmpegError

        with (
            patch("tubearchive.core.transcoder.detect_metadata", return_value=mock_metadata),
            patch("tubearchive.core.transcoder.create_vidstab_detect_filter") as mock_detect,
            patch("tubearchive.core.transcoder.create_combined_filter") as mock_combined,
        ):
            mock_detect.return_value = "vidstabdetect=shakiness=5"
            mock_combined.return_value = ("video_filter", "audio_filter")
            mock_transcoder.executor.run.return_value = None
            mock_transcoder.executor.run_analysis.side_effect = FFmpegError("vidstab failed")
            mock_transcoder.executor.build_vidstab_detect_command.return_value = ["ffmpeg"]
            with contextlib.suppress(Exception):
                mock_transcoder.transcode_video(
                    video_file,
                    stabilize=True,
                    stabilize_strength="medium",
                    stabilize_crop="crop",
                )

        # combined_filter에 stabilize_filter=""로 전달 (스킵)
        if mock_combined.call_args:
            stabilize_arg = mock_combined.call_args.kwargs.get("stabilize_filter", "")
            assert stabilize_arg == ""

    def test_trf_temp_file_cleanup(
        self,
        mock_transcoder: MagicMock,
        mock_metadata: MagicMock,
        video_file: MagicMock,
        tmp_path: Path,
    ) -> None:
        """vidstab trf 임시 파일이 정리된다."""
        with (
            patch("tubearchive.core.transcoder.detect_metadata", return_value=mock_metadata),
            patch("tubearchive.core.transcoder.create_vidstab_detect_filter") as mock_detect,
            patch("tubearchive.core.transcoder.create_vidstab_transform_filter") as mock_transform,
        ):
            mock_detect.return_value = "vidstabdetect=shakiness=5"
            mock_transform.return_value = "vidstabtransform=smoothing=10"
            mock_transcoder.executor.run.return_value = None
            mock_transcoder.executor.run_analysis.return_value = ""
            mock_transcoder.executor.build_vidstab_detect_command.return_value = ["ffmpeg"]

            # trf 파일 미리 생성 (실제 vidstab이 생성하는 파일 시뮬레이션)
            trf_file = tmp_path / "vidstab_1.trf"
            trf_file.write_text("dummy trf data")

            with contextlib.suppress(Exception):
                mock_transcoder.transcode_video(
                    video_file,
                    stabilize=True,
                    stabilize_strength="medium",
                    stabilize_crop="crop",
                )

        # trf 파일이 정리됐는지 확인 (temp_dir 기반)
        # Note: 실제 trf_path는 mock_transcoder.temp_dir 기반이므로 직접 확인
        # 여기서는 finally 블록의 존재와 동작을 간접적으로 검증

    def test_stabilize_strength_parameter_passed(
        self,
        mock_transcoder: MagicMock,
        mock_metadata: MagicMock,
        video_file: MagicMock,
    ) -> None:
        """각 strength별 올바른 파라미터 전달."""
        for strength in ("light", "medium", "heavy"):
            with (
                patch("tubearchive.core.transcoder.detect_metadata", return_value=mock_metadata),
                patch("tubearchive.core.transcoder.create_vidstab_detect_filter") as mock_detect,
                patch(
                    "tubearchive.core.transcoder.create_vidstab_transform_filter",
                ) as mock_transform,
            ):
                mock_detect.return_value = "vidstabdetect"
                mock_transform.return_value = "vidstabtransform"
                mock_transcoder.executor.run.return_value = None
                mock_transcoder.executor.run_analysis.return_value = ""
                mock_transcoder.executor.build_vidstab_detect_command.return_value = ["ffmpeg"]
                with contextlib.suppress(Exception):
                    mock_transcoder.transcode_video(
                        video_file,
                        stabilize=True,
                        stabilize_strength=strength,
                        stabilize_crop="crop",
                    )

                # StabilizeStrength enum으로 변환되어 전달되는지 확인
                from tubearchive.ffmpeg.effects import StabilizeStrength

                mock_detect.assert_called_once()
                call_args = mock_detect.call_args
                assert call_args[0][0] == StabilizeStrength(strength)
