"""
스케줄 업로드 E2E 테스트.

실제 파이프라인과 CLI 인자 파싱을 포함한 통합 테스트.
YouTube 업로드는 mock으로 처리하여 실제 API 호출 없이 검증.
"""

import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.cli import create_parser, parse_schedule_datetime, validate_args
from tubearchive.youtube.uploader import UploadResult

from .conftest import create_test_video

# ffmpeg 없으면 전체 모듈 스킵
pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard1,
]


# ---------- Fixtures ----------


@pytest.fixture
def test_videos_for_upload(e2e_video_dir: Path) -> Path:
    """업로드 테스트용 영상 2개 생성."""
    create_test_video(e2e_video_dir / "clip_001.mov", duration=2.0)
    create_test_video(e2e_video_dir / "clip_002.mov", duration=2.0)
    return e2e_video_dir


@pytest.fixture
def future_schedule() -> str:
    """미래 시간 스케줄 (고정: 2050년)."""
    return "2050-12-31T23:59:59+09:00"


# ---------- 테스트 ----------


class TestScheduleParsing:
    """스케줄 파싱 E2E 테스트."""

    def test_parse_iso8601_with_timezone(self) -> None:
        """ISO 8601 형식 (타임존 포함) 파싱."""
        schedule = "2050-12-31T23:59:59+09:00"
        result = parse_schedule_datetime(schedule)
        assert result == "2050-12-31T23:59:59+09:00"

    def test_parse_space_separated_format(self) -> None:
        """공백 구분 형식 자동 변환."""
        schedule = "2050-12-31 23:59:59+09:00"
        result = parse_schedule_datetime(schedule)
        assert result == "2050-12-31T23:59:59+09:00"

    def test_auto_add_timezone(self) -> None:
        """타임존 없으면 로컬 타임존 자동 추가."""
        schedule = "2050-12-31T23:59:59"
        result = parse_schedule_datetime(schedule)
        # 로컬 타임존이 추가되어야 함
        assert result.startswith("2050-12-31T23:59:59")
        assert "+" in result or "-" in result or result.endswith("Z")

    def test_past_time_raises_with_details(self) -> None:
        """과거 시간은 상세한 에러 메시지와 함께 실패."""
        with pytest.raises(ValueError) as exc_info:
            parse_schedule_datetime("2020-01-01T00:00:00+09:00")
        error_msg = str(exc_info.value)
        assert "future" in error_msg.lower()
        assert any(word in error_msg for word in ["전", "ago", "Current time"])


class TestScheduleWithCLI:
    """CLI 인자 파싱 E2E 테스트."""

    def test_schedule_option_in_parser(self, future_schedule: str) -> None:
        """--schedule 옵션이 파서에서 올바르게 파싱됨."""
        parser = create_parser()
        args = parser.parse_args(["--schedule", future_schedule, "/dummy/path"])
        assert args.schedule == future_schedule

    def test_validated_args_includes_schedule(
        self, test_videos_for_upload: Path, tmp_path: Path, future_schedule: str
    ) -> None:
        """ValidatedArgs에 schedule이 포함되고 파싱됨."""
        parser = create_parser()
        args = parser.parse_args(
            [
                "--schedule",
                future_schedule,
                "--output",
                str(tmp_path),
                str(test_videos_for_upload),
            ]
        )

        validated_args = validate_args(args)
        assert validated_args.schedule is not None
        assert validated_args.schedule.startswith("2050-")


class TestScheduleUploadIntegration:
    """스케줄 업로드 통합 테스트 (mock YouTube)."""

    @patch("tubearchive.cli.upload_to_youtube")
    def test_upload_with_schedule(
        self,
        mock_upload: MagicMock,
        test_videos_for_upload: Path,
        e2e_output_dir: Path,
        future_schedule: str,
    ) -> None:
        """--upload --schedule 옵션과 함께 파이프라인 실행."""
        from tubearchive.cli import create_parser, run_pipeline, validate_args

        # Mock 설정
        mock_upload.return_value = UploadResult.from_video_id(
            "test_video_id",
            "Test Title",
            scheduled_publish_at=future_schedule,
        )

        # 출력 파일명 지정
        output_file = e2e_output_dir / "merged.mp4"

        # CLI 인자 파싱
        parser = create_parser()
        args = parser.parse_args(
            [
                str(test_videos_for_upload),
                "--output",
                str(output_file),
                "--schedule",
                future_schedule,
                "--upload",
                "--upload-title",
                "E2E Test Video",
            ]
        )

        # ValidatedArgs 생성
        validated_args = validate_args(args)

        # 파이프라인 실행 (병합까지)
        output_path = run_pipeline(validated_args)

        # 병합 파일 생성 확인
        assert output_path.exists()
        assert output_path.stat().st_size > 0

    @patch("tubearchive.youtube.auth.get_authenticated_service")
    @patch("tubearchive.youtube.auth.check_auth_status")
    @patch("tubearchive.youtube.uploader.validate_upload")
    @patch("tubearchive.youtube.uploader.YouTubeUploader")
    def test_upload_passes_publish_at_correctly(
        self,
        mock_uploader_class: MagicMock,
        mock_validate: MagicMock,
        mock_check_auth_status: MagicMock,
        mock_get_authenticated_service: MagicMock,
        tmp_path: Path,
        future_schedule: str,
    ) -> None:
        """업로드 시 publish_at이 올바르게 전달됨."""
        from tubearchive.cli import upload_to_youtube
        from tubearchive.youtube.uploader import UploadValidation

        # Validation mock
        mock_validate.return_value = UploadValidation(
            is_valid=True,
            file_size_bytes=1024,
            duration_seconds=10.0,
            errors=[],
            warnings=[],
        )
        mock_check_auth_status.return_value = MagicMock(
            has_client_secrets=True,
            has_valid_token=True,
        )
        mock_get_authenticated_service.return_value = MagicMock()

        # Mock 인스턴스 설정
        mock_uploader = MagicMock()
        mock_uploader_class.return_value = mock_uploader
        mock_uploader.upload.return_value = UploadResult.from_video_id(
            "test_id",
            "Test",
            scheduled_publish_at=future_schedule,
        )

        # 테스트 파일 생성
        test_file = tmp_path / "test.mp4"
        test_file.write_bytes(b"fake video")

        # 업로드 실행
        upload_to_youtube(
            file_path=test_file,
            title="Test Upload",
            description="Test",
            privacy="unlisted",
            publish_at=future_schedule,
            merge_job_id=None,
            playlist_ids=[],
            chunk_mb=32,
        )

        # publish_at이 전달되었는지 확인
        mock_uploader.upload.assert_called_once()
        call_kwargs = mock_uploader.upload.call_args.kwargs
        assert call_kwargs["publish_at"] == future_schedule

    def test_schedule_without_upload_works(
        self,
        test_videos_for_upload: Path,
        e2e_output_dir: Path,
        future_schedule: str,
    ) -> None:
        """--schedule만 있고 --upload 없으면 병합까지만 실행."""
        from tubearchive.cli import create_parser, run_pipeline, validate_args

        # 출력 파일명 지정
        output_file = e2e_output_dir / "merged.mp4"

        parser = create_parser()
        args = parser.parse_args(
            [
                str(test_videos_for_upload),
                "--output",
                str(output_file),
                "--schedule",
                future_schedule,
                # --upload 없음
            ]
        )

        validated_args = validate_args(args)

        # schedule은 파싱되지만 upload가 False이므로 병합만 실행
        assert validated_args.schedule is not None
        assert validated_args.upload is False

        # 파이프라인 실행 (병합까지)
        output_path = run_pipeline(validated_args)

        # 병합 파일 생성 확인
        assert output_path.exists()
        assert output_path.stat().st_size > 0
