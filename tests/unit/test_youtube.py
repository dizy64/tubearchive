"""YouTube 업로드 모듈 테스트."""

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestYouTubeAuth:
    """YouTube 인증 테스트."""

    def test_get_config_dir_creates_directory(self, tmp_path: Path) -> None:
        """설정 디렉토리가 없으면 생성."""
        from tubearchive.infra.youtube.auth import get_config_dir

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            config_dir = get_config_dir()
            # ~/.tubearchive 경로여야 함
            assert config_dir.name == ".tubearchive"

    def test_get_token_path_default(self, tmp_path: Path) -> None:
        """기본 토큰 경로."""
        from tubearchive.infra.youtube.auth import get_token_path

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            token_path = get_token_path()
            assert token_path.name == "youtube_token.json"
            assert ".tubearchive" in str(token_path)

    def test_get_token_path_from_env(self, tmp_path: Path) -> None:
        """환경 변수로 토큰 경로 지정."""
        from tubearchive.infra.youtube.auth import get_token_path

        custom_path = tmp_path / "custom_token.json"
        with patch.dict("os.environ", {"TUBEARCHIVE_YOUTUBE_TOKEN": str(custom_path)}, clear=False):
            token_path = get_token_path()
            assert token_path == custom_path

    def test_get_client_secrets_path_default(self, tmp_path: Path) -> None:
        """기본 클라이언트 시크릿 경로."""
        from tubearchive.infra.youtube.auth import get_client_secrets_path

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            secrets_path = get_client_secrets_path()
            assert secrets_path.name == "client_secrets.json"
            assert ".tubearchive" in str(secrets_path)

    def test_get_client_secrets_path_from_env(self, tmp_path: Path) -> None:
        """환경 변수로 클라이언트 시크릿 경로 지정."""
        from tubearchive.infra.youtube.auth import get_client_secrets_path

        custom_path = tmp_path / "my_secrets.json"
        with patch.dict(
            "os.environ",
            {"TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS": str(custom_path)},
            clear=False,
        ):
            secrets_path = get_client_secrets_path()
            assert secrets_path == custom_path

    def test_load_credentials_returns_none_when_no_token(self, tmp_path: Path) -> None:
        """토큰 파일이 없으면 None 반환."""
        from tubearchive.infra.youtube.auth import load_credentials

        token_path = tmp_path / "nonexistent_token.json"
        credentials = load_credentials(token_path)
        assert credentials is None

    def test_load_credentials_loads_valid_token(self, tmp_path: Path) -> None:
        """유효한 토큰 파일 로드."""
        from tubearchive.infra.youtube.auth import load_credentials

        token_path = tmp_path / "token.json"
        token_data = {
            "token": "test_token",
            "refresh_token": "test_refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
        }
        token_path.write_text(json.dumps(token_data))

        with patch("tubearchive.infra.youtube.auth.Credentials") as mock_creds:
            mock_creds.from_authorized_user_info.return_value = MagicMock(valid=True)
            credentials = load_credentials(token_path)
            assert credentials is not None
            mock_creds.from_authorized_user_info.assert_called_once()

    def test_save_credentials(self, tmp_path: Path) -> None:
        """자격 증명 저장."""
        from tubearchive.infra.youtube.auth import save_credentials

        token_path = tmp_path / "token.json"
        mock_credentials = MagicMock()
        mock_credentials.to_json.return_value = '{"token": "test"}'

        save_credentials(mock_credentials, token_path)

        assert token_path.exists()
        assert json.loads(token_path.read_text()) == {"token": "test"}

    def test_get_authenticated_service_raises_without_secrets(self, tmp_path: Path) -> None:
        """클라이언트 시크릿 없으면 에러."""
        from tubearchive.infra.youtube.auth import (
            YouTubeAuthError,
            get_authenticated_service,
        )

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            with pytest.raises(YouTubeAuthError) as exc_info:
                get_authenticated_service()
            assert "client_secrets.json" in str(exc_info.value)


class TestSanitizeDescription:
    """YouTube description 정제 테스트."""

    def test_short_description_unchanged(self) -> None:
        """5000자 이하의 정상 description은 그대로 반환."""
        from tubearchive.infra.youtube.uploader import sanitize_description

        desc = "00:00 clip1\n01:30 clip2"
        assert sanitize_description(desc) == desc

    def test_empty_description(self) -> None:
        """빈 description."""
        from tubearchive.infra.youtube.uploader import sanitize_description

        assert sanitize_description("") == ""

    def test_removes_angle_brackets(self) -> None:
        """<> 문자 제거."""
        from tubearchive.infra.youtube.uploader import sanitize_description

        desc = "test <script>alert(1)</script> end"
        result = sanitize_description(desc)
        assert "<" not in result
        assert ">" not in result
        assert "test script" in result

    def test_truncates_long_description(self) -> None:
        """5000자 초과 시 잘림."""
        from tubearchive.infra.youtube.uploader import (
            YOUTUBE_MAX_DESCRIPTION_LENGTH,
            sanitize_description,
        )

        # 5000자 초과하는 description 생성 (줄 단위)
        lines = [f"00:{i:02d} clip_{i}" for i in range(500)]
        desc = "\n".join(lines)
        assert len(desc) > YOUTUBE_MAX_DESCRIPTION_LENGTH

        result = sanitize_description(desc)
        assert len(result) <= YOUTUBE_MAX_DESCRIPTION_LENGTH
        assert result.endswith("...")

    def test_truncates_at_line_boundary(self) -> None:
        """잘림이 줄 경계에서 발생."""
        from tubearchive.infra.youtube.uploader import sanitize_description

        # 정확히 줄 경계에서 잘리는지 확인
        lines = [f"00:{i:02d} clip_{i}" for i in range(500)]
        desc = "\n".join(lines)

        result = sanitize_description(desc)
        # 마지막 줄 앞의 내용은 완전한 줄이어야 함
        body = result.removesuffix("\n\n...")
        # 잘린 줄이 없어야 함 (모든 줄이 "00:" 으로 시작)
        for line in body.split("\n"):
            if line:
                assert line.startswith("00:"), f"Incomplete line found: {line!r}"

    def test_exact_5000_chars_unchanged(self) -> None:
        """정확히 5000자이면 잘리지 않음."""
        from tubearchive.infra.youtube.uploader import (
            YOUTUBE_MAX_DESCRIPTION_LENGTH,
            sanitize_description,
        )

        desc = "a" * YOUTUBE_MAX_DESCRIPTION_LENGTH
        assert sanitize_description(desc) == desc


class TestUploadResult:
    """UploadResult 데이터 클래스 테스트."""

    def test_upload_result_creation(self) -> None:
        """UploadResult 생성."""
        from tubearchive.infra.youtube.uploader import UploadResult

        result = UploadResult(
            video_id="abc123",
            url="https://youtu.be/abc123",
            title="Test Video",
        )
        assert result.video_id == "abc123"
        assert result.url == "https://youtu.be/abc123"
        assert result.title == "Test Video"

    def test_upload_result_default_url(self) -> None:
        """video_id로 기본 URL 생성."""
        from tubearchive.infra.youtube.uploader import UploadResult

        result = UploadResult.from_video_id("xyz789", "My Title")
        assert result.video_id == "xyz789"
        assert result.url == "https://youtu.be/xyz789"
        assert result.title == "My Title"


class TestYouTubeUploader:
    """YouTubeUploader 테스트."""

    def test_uploader_init(self) -> None:
        """Uploader 초기화."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        mock_service = MagicMock()
        uploader = YouTubeUploader(mock_service)
        assert uploader.service == mock_service

    def test_upload_validates_file_exists(self, tmp_path: Path) -> None:
        """존재하지 않는 파일 업로드 시 에러."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        mock_service = MagicMock()
        uploader = YouTubeUploader(mock_service)

        nonexistent = tmp_path / "nonexistent.mp4"
        with pytest.raises(FileNotFoundError):
            uploader.upload(nonexistent, "Test")

    def test_upload_calls_youtube_api(self, tmp_path: Path) -> None:
        """YouTube API 호출 확인."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        # 임시 파일 생성
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video content")

        # Mock 설정
        mock_service = MagicMock()
        mock_insert = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_insert

        # Mock resumable upload 응답
        mock_insert.next_chunk.return_value = (
            None,
            {"id": "uploaded_id"},
        )

        uploader = YouTubeUploader(mock_service)

        with patch("tubearchive.infra.youtube.uploader.MediaFileUpload") as mock_media_upload:
            mock_media_upload.return_value = MagicMock()
            result = uploader.upload(
                video_file,
                title="Test Title",
                description="Test Description",
                privacy="unlisted",
            )

        assert result.video_id == "uploaded_id"
        assert result.title == "Test Title"
        mock_service.videos.return_value.insert.assert_called_once()

    def test_upload_progress_callback(self, tmp_path: Path) -> None:
        """진행률 콜백 호출 확인."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video content" * 1000)

        mock_service = MagicMock()
        mock_insert = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_insert

        # 진행률 시뮬레이션: 50% → 완료
        progress_mock = MagicMock()
        progress_mock.progress.return_value = 0.5
        mock_insert.next_chunk.side_effect = [
            (progress_mock, None),  # 50% 진행
            (None, {"id": "final_id"}),  # 완료
        ]

        progress_values: list[int] = []

        def on_progress(percent: int) -> None:
            progress_values.append(percent)

        uploader = YouTubeUploader(mock_service)

        with patch("tubearchive.infra.youtube.uploader.MediaFileUpload") as mock_media_upload:
            mock_media_upload.return_value = MagicMock()
            uploader.upload(
                video_file,
                title="Test",
                on_progress=on_progress,
            )

        assert 50 in progress_values
        assert 100 in progress_values

    def test_upload_with_default_privacy(self, tmp_path: Path) -> None:
        """기본 공개 설정은 unlisted."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video content")

        mock_service = MagicMock()
        mock_insert = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_insert
        mock_insert.next_chunk.return_value = (None, {"id": "test_id"})

        uploader = YouTubeUploader(mock_service)

        with patch("tubearchive.infra.youtube.uploader.MediaFileUpload") as mock_media_upload:
            mock_media_upload.return_value = MagicMock()
            uploader.upload(video_file, title="Test")

        # insert 호출 시 body의 status.privacyStatus 확인
        call_args = mock_service.videos.return_value.insert.call_args
        body = call_args.kwargs.get("body") or call_args[1].get("body")
        assert body["status"]["privacyStatus"] == "unlisted"

    def test_set_thumbnail_uploads_image(self, tmp_path: Path) -> None:
        """썸네일 업로드 API 호출."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        original_thumbnail = tmp_path / "thumb.jpg"
        original_thumbnail.write_bytes(b"original")
        prepared_thumbnail = tmp_path / "thumb_youtube.jpg"
        prepared_thumbnail.write_bytes(b"prepared")

        mock_service = MagicMock()
        mock_set = MagicMock()
        mock_service.thumbnails.return_value.set.return_value = mock_set

        uploader = YouTubeUploader(mock_service)

        with (
            patch(
                "tubearchive.infra.youtube.uploader.prepare_thumbnail_for_youtube",
                return_value=prepared_thumbnail,
            ),
            patch("tubearchive.infra.youtube.uploader.MediaFileUpload") as mock_media_upload,
        ):
            mock_media_upload.return_value = MagicMock()
            uploader.set_thumbnail("video123", original_thumbnail)

        mock_service.thumbnails.return_value.set.assert_called_once()
        call_kwargs = mock_service.thumbnails.return_value.set.call_args.kwargs
        assert call_kwargs["videoId"] == "video123"
        mock_set.execute.assert_called_once()

    def test_set_thumbnail_cleans_up_generated_file(self, tmp_path: Path) -> None:
        """생성된 썸네일은 업로드 후 정리된다."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        original_thumbnail = tmp_path / "thumb.jpg"
        original_thumbnail.write_bytes(b"original")
        prepared_thumbnail = tmp_path / "thumb_youtube.jpg"
        prepared_thumbnail.write_bytes(b"prepared")

        mock_service = MagicMock()
        mock_set = MagicMock()
        mock_service.thumbnails.return_value.set.return_value = mock_set

        uploader = YouTubeUploader(mock_service)

        with (
            patch(
                "tubearchive.infra.youtube.uploader.prepare_thumbnail_for_youtube",
                return_value=prepared_thumbnail,
            ),
            patch("tubearchive.infra.youtube.uploader.MediaFileUpload") as mock_media_upload,
        ):
            mock_media_upload.return_value = MagicMock()
            uploader.set_thumbnail("video123", original_thumbnail)

        assert not prepared_thumbnail.exists()

    def test_set_thumbnail_requires_video_id(self, tmp_path: Path) -> None:
        """video_id 누락 시 에러."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        mock_service = MagicMock()
        uploader = YouTubeUploader(mock_service)

        with pytest.raises(ValueError):
            uploader.set_thumbnail("", tmp_path / "thumb.jpg")

    def test_set_thumbnail_handles_api_error(self, tmp_path: Path) -> None:
        """썸네일 API 에러 처리."""
        from googleapiclient.errors import HttpError

        from tubearchive.infra.youtube.uploader import YouTubeUploader, YouTubeUploadError

        original_thumbnail = tmp_path / "thumb.jpg"
        original_thumbnail.write_bytes(b"original")
        prepared_thumbnail = tmp_path / "thumb_youtube.jpg"
        prepared_thumbnail.write_bytes(b"prepared")

        mock_service = MagicMock()
        mock_set = MagicMock()
        mock_service.thumbnails.return_value.set.return_value = mock_set
        mock_response = Mock()
        mock_response.status = 403
        mock_response.reason = "Forbidden"
        mock_set.execute.side_effect = HttpError(mock_response, b"forbidden")

        uploader = YouTubeUploader(mock_service)

        with (
            patch(
                "tubearchive.infra.youtube.uploader.prepare_thumbnail_for_youtube",
                return_value=prepared_thumbnail,
            ),
            patch("tubearchive.infra.youtube.uploader.MediaFileUpload"),
            pytest.raises(YouTubeUploadError) as exc_info,
        ):
            uploader.set_thumbnail("video123", original_thumbnail)

        assert "video123" in str(exc_info.value)
        assert not prepared_thumbnail.exists()


class TestYouTubeCaptions:
    """자막 업로드 API 테스트."""

    def test_set_captions_uploads_srt_file(self, tmp_path: Path) -> None:
        """SRT 파일을 업로드한다."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        caption_file = tmp_path / "caption.srt"
        caption_file.write_text("1\n00:00:00,000 --> 00:00:01,000\n안녕\n")

        mock_service = MagicMock()
        mock_insert = MagicMock()
        mock_service.captions().insert.return_value = mock_insert

        uploader = YouTubeUploader(mock_service)

        with patch("tubearchive.infra.youtube.uploader.MediaFileUpload") as mock_media_upload:
            mock_media_upload.return_value = MagicMock()
            uploader.set_captions(
                video_id="video123",
                caption_path=caption_file,
                language="ko",
            )

        mock_insert.execute.assert_called_once()
        call_args = mock_service.captions().insert.call_args.kwargs
        assert call_args["part"] == "snippet"
        assert call_args["body"]["snippet"]["language"] == "ko"
        assert call_args["body"]["snippet"]["name"] == "caption"

    def test_set_captions_uploads_vtt_file_with_default_name(self, tmp_path: Path) -> None:
        """VTT 파일은 파일명 기본값을 캡션명으로 사용한다."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        caption_file = tmp_path / "subtitle.vtt"
        caption_file.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello\n")

        mock_service = MagicMock()
        mock_insert = MagicMock()
        mock_service.captions().insert.return_value = mock_insert

        uploader = YouTubeUploader(mock_service)

        with patch("tubearchive.infra.youtube.uploader.MediaFileUpload") as mock_media_upload:
            mock_media_upload.return_value = MagicMock()
            uploader.set_captions(
                video_id="video123",
                caption_path=caption_file,
            )

        call_args = mock_service.captions().insert.call_args.kwargs
        assert call_args["body"]["snippet"]["name"] == "subtitle"

    def test_set_captions_rejects_unsupported_format(self, tmp_path: Path) -> None:
        """확장자가 지원되지 않으면 실패한다."""
        from tubearchive.infra.youtube.uploader import (
            YouTubeUploader,
            YouTubeUploadError,
        )

        caption_file = tmp_path / "caption.txt"
        caption_file.write_text("invalid")
        uploader = YouTubeUploader(MagicMock())

        with pytest.raises(YouTubeUploadError, match="Unsupported caption format"):
            uploader.set_captions("video123", caption_file)

    def test_set_captions_requires_existing_file(self) -> None:
        """자막 파일이 없으면 실패한다."""
        from tubearchive.infra.youtube.uploader import (
            YouTubeUploader,
            YouTubeUploadError,
        )

        uploader = YouTubeUploader(MagicMock())
        with pytest.raises(YouTubeUploadError, match="Caption file not found"):
            uploader.set_captions("video123", Path("missing.srt"))

    def test_set_captions_requires_video_id(self, tmp_path: Path) -> None:
        """video_id가 없으면 실패한다."""
        from tubearchive.infra.youtube.uploader import (
            YouTubeUploader,
            YouTubeUploadError,
        )

        caption_file = tmp_path / "caption.srt"
        caption_file.write_text("1\n00:00:00,000 --> 00:00:01,000\n안녕\n")
        uploader = YouTubeUploader(MagicMock())

        with pytest.raises(YouTubeUploadError):
            uploader.set_captions("", caption_file)

    def test_set_captions_handles_api_error(self, tmp_path: Path) -> None:
        """API 에러를 YouTubeUploadError로 감싼다."""
        from googleapiclient.errors import HttpError

        from tubearchive.infra.youtube.uploader import YouTubeUploader, YouTubeUploadError

        caption_file = tmp_path / "caption.srt"
        caption_file.write_text("1\n00:00:00,000 --> 00:00:01,000\n안녕\n")

        mock_service = MagicMock()
        mock_insert = MagicMock()
        mock_service.captions().insert.return_value = mock_insert
        mock_response = Mock()
        mock_response.status = 403
        mock_response.reason = "Forbidden"
        mock_insert.execute.side_effect = HttpError(mock_response, b"forbidden")

        uploader = YouTubeUploader(mock_service)

        with (
            patch("tubearchive.infra.youtube.uploader.MediaFileUpload"),
            pytest.raises(YouTubeUploadError) as exc_info,
        ):
            uploader.set_captions("video123", caption_file)

        assert "video123" in str(exc_info.value)


class TestYouTubeUploadError:
    """업로드 에러 처리 테스트."""

    def test_upload_handles_api_error(self, tmp_path: Path) -> None:
        """API 에러 처리."""
        from googleapiclient.errors import HttpError

        from tubearchive.infra.youtube.uploader import YouTubeUploader, YouTubeUploadError

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video content")

        mock_service = MagicMock()
        mock_insert = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_insert

        # API 에러 시뮬레이션
        mock_response = Mock()
        mock_response.status = 403
        mock_response.reason = "Quota Exceeded"
        mock_insert.next_chunk.side_effect = HttpError(mock_response, b"quota exceeded")

        uploader = YouTubeUploader(mock_service)

        with patch("tubearchive.infra.youtube.uploader.MediaFileUpload") as mock_media_upload:
            mock_media_upload.return_value = MagicMock()
            with pytest.raises(YouTubeUploadError) as exc_info:
                uploader.upload(video_file, title="Test")

        assert "quota" in str(exc_info.value).lower() or "403" in str(exc_info.value)


class TestCLIUploadIntegration:
    """CLI upload 관련 통합 테스트."""

    def test_upload_only_option_exists(self) -> None:
        """--upload-only 옵션이 존재."""
        from tubearchive.app.cli.main import create_parser

        parser = create_parser()
        args = parser.parse_args(["--upload-only", "test.mp4"])
        assert args.upload_only == "test.mp4"

    def test_upload_flag_in_main_parser(self) -> None:
        """--upload 플래그가 메인 파서에 존재."""
        from tubearchive.app.cli.main import create_parser

        parser = create_parser()
        args = parser.parse_args(["--upload"])
        assert args.upload is True

    def test_upload_privacy_option(self) -> None:
        """--upload-privacy 옵션이 존재."""
        from tubearchive.app.cli.main import create_parser

        parser = create_parser()
        args = parser.parse_args(["--upload-privacy", "private"])
        assert args.upload_privacy == "private"

    def test_upload_title_option(self) -> None:
        """--upload-title 옵션이 존재."""
        from tubearchive.app.cli.main import create_parser

        parser = create_parser()
        args = parser.parse_args(["--upload-title", "My Video"])
        assert args.upload_title == "My Video"

    def test_validated_args_includes_upload(self, tmp_path: Path) -> None:
        """ValidatedArgs에 upload 필드 포함."""
        import dataclasses

        from tubearchive.app.cli.main import ValidatedArgs

        fields = {f.name for f in dataclasses.fields(ValidatedArgs)}
        assert "upload" in fields

    def test_setup_youtube_option_exists(self) -> None:
        """--setup-youtube 옵션이 존재."""
        from tubearchive.app.cli.main import create_parser

        parser = create_parser()
        args = parser.parse_args(["--setup-youtube"])
        assert args.setup_youtube is True

    def test_youtube_auth_option_exists(self) -> None:
        """--youtube-auth 옵션이 존재."""
        from tubearchive.app.cli.main import create_parser

        parser = create_parser()
        args = parser.parse_args(["--youtube-auth"])
        assert args.youtube_auth is True


class TestCheckAuthStatus:
    """인증 상태 확인 기능 테스트."""

    def test_check_auth_status_no_client_secrets(self, tmp_path: Path) -> None:
        """client_secrets.json 없을 때 상태 반환."""
        from tubearchive.infra.youtube.auth import check_auth_status

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            status = check_auth_status()
            assert status.has_client_secrets is False
            assert status.has_valid_token is False
            assert status.needs_browser_auth is False  # secrets 없으면 브라우저 인증 불가

    def test_check_auth_status_has_secrets_no_token(self, tmp_path: Path) -> None:
        """client_secrets.json은 있고 토큰은 없을 때."""
        from tubearchive.infra.youtube.auth import check_auth_status

        # client_secrets.json 생성
        config_dir = tmp_path / ".tubearchive"
        config_dir.mkdir()
        secrets_file = config_dir / "client_secrets.json"
        secrets_file.write_text('{"installed": {"client_id": "test"}}')

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            status = check_auth_status()
            assert status.has_client_secrets is True
            assert status.has_valid_token is False
            assert status.needs_browser_auth is True  # 브라우저 인증 필요

    def test_check_auth_status_has_valid_token(self, tmp_path: Path) -> None:
        """유효한 토큰이 있을 때."""
        from tubearchive.infra.youtube.auth import check_auth_status

        # client_secrets.json 생성
        config_dir = tmp_path / ".tubearchive"
        config_dir.mkdir()
        secrets_file = config_dir / "client_secrets.json"
        secrets_file.write_text('{"installed": {"client_id": "test"}}')

        # token.json 생성
        token_file = config_dir / "youtube_token.json"
        token_data = {
            "token": "test_token",
            "refresh_token": "test_refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
        }
        token_file.write_text(json.dumps(token_data))

        with (
            patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False),
            patch("tubearchive.infra.youtube.auth.Credentials") as mock_creds,
        ):
            mock_cred_instance = MagicMock()
            mock_cred_instance.valid = True
            mock_cred_instance.expired = False
            mock_creds.from_authorized_user_info.return_value = mock_cred_instance

            status = check_auth_status()
            assert status.has_client_secrets is True
            assert status.has_valid_token is True
            assert status.needs_browser_auth is False

    def test_check_auth_status_expired_token(self, tmp_path: Path) -> None:
        """토큰이 만료되었을 때."""
        from tubearchive.infra.youtube.auth import check_auth_status

        # client_secrets.json 생성
        config_dir = tmp_path / ".tubearchive"
        config_dir.mkdir()
        secrets_file = config_dir / "client_secrets.json"
        secrets_file.write_text('{"installed": {"client_id": "test"}}')

        # token.json 생성 (만료됨)
        token_file = config_dir / "youtube_token.json"
        token_data = {
            "token": "expired_token",
            "refresh_token": "test_refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "test_client_id",
            "client_secret": "test_client_secret",
            "scopes": ["https://www.googleapis.com/auth/youtube.upload"],
        }
        token_file.write_text(json.dumps(token_data))

        with (
            patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False),
            patch("tubearchive.infra.youtube.auth.Credentials") as mock_creds,
        ):
            mock_cred_instance = MagicMock()
            mock_cred_instance.valid = False
            mock_cred_instance.expired = True
            mock_cred_instance.refresh_token = "test_refresh"
            mock_creds.from_authorized_user_info.return_value = mock_cred_instance

            status = check_auth_status()
            assert status.has_client_secrets is True
            assert status.has_valid_token is False
            assert status.needs_browser_auth is True  # 재인증 필요


class TestAuthStatusMessage:
    """인증 상태 메시지 생성 테스트."""

    def test_get_setup_guide_no_secrets(self) -> None:
        """client_secrets.json 없을 때 설정 가이드."""
        from tubearchive.infra.youtube.auth import AuthStatus

        status = AuthStatus(
            has_client_secrets=False,
            has_valid_token=False,
            needs_browser_auth=False,
            client_secrets_path=Path("~/.tubearchive/client_secrets.json"),
            token_path=Path("~/.tubearchive/youtube_token.json"),
        )
        guide = status.get_setup_guide()

        assert "Google Cloud Console" in guide
        assert "OAuth" in guide
        assert "client_secrets.json" in guide

    def test_get_setup_guide_needs_auth(self) -> None:
        """브라우저 인증이 필요할 때 메시지."""
        from tubearchive.infra.youtube.auth import AuthStatus

        status = AuthStatus(
            has_client_secrets=True,
            has_valid_token=False,
            needs_browser_auth=True,
            client_secrets_path=Path("~/.tubearchive/client_secrets.json"),
            token_path=Path("~/.tubearchive/youtube_token.json"),
        )
        guide = status.get_setup_guide()

        assert "브라우저" in guide or "인증" in guide

    def test_get_setup_guide_ready(self) -> None:
        """인증 완료 상태 메시지."""
        from tubearchive.infra.youtube.auth import AuthStatus

        status = AuthStatus(
            has_client_secrets=True,
            has_valid_token=True,
            needs_browser_auth=False,
            client_secrets_path=Path("~/.tubearchive/client_secrets.json"),
            token_path=Path("~/.tubearchive/youtube_token.json"),
        )
        guide = status.get_setup_guide()

        assert "완료" in guide or "준비" in guide or "✅" in guide


class TestListPlaylists:
    """list_playlists API 테스트."""

    def test_single_page(self) -> None:
        """단일 페이지 응답."""
        from tubearchive.infra.youtube.playlist import list_playlists

        service = MagicMock()
        service.playlists().list().execute.return_value = {
            "items": [
                {
                    "id": "PL1",
                    "snippet": {"title": "여행"},
                    "contentDetails": {"itemCount": 5},
                },
                {
                    "id": "PL2",
                    "snippet": {"title": "일상"},
                    "contentDetails": {"itemCount": 3},
                },
            ],
        }

        result = list_playlists(service)

        assert len(result) == 2
        assert result[0].id == "PL1"
        assert result[0].title == "여행"
        assert result[0].item_count == 5

    def test_pagination(self) -> None:
        """페이지네이션 응답."""
        from tubearchive.infra.youtube.playlist import list_playlists

        service = MagicMock()
        page1 = {
            "items": [
                {"id": "PL1", "snippet": {"title": "A"}, "contentDetails": {"itemCount": 1}},
            ],
            "nextPageToken": "token2",
        }
        page2 = {
            "items": [
                {"id": "PL2", "snippet": {"title": "B"}, "contentDetails": {"itemCount": 2}},
            ],
        }
        service.playlists().list().execute.side_effect = [page1, page2]

        result = list_playlists(service)

        assert len(result) == 2
        assert result[0].id == "PL1"
        assert result[1].id == "PL2"

    def test_empty_response(self) -> None:
        """빈 응답."""
        from tubearchive.infra.youtube.playlist import list_playlists

        service = MagicMock()
        service.playlists().list().execute.return_value = {"items": []}

        result = list_playlists(service)
        assert result == []

    def test_missing_items_key(self) -> None:
        """items 키 없는 응답은 빈 리스트."""
        from tubearchive.infra.youtube.playlist import list_playlists

        service = MagicMock()
        service.playlists().list().execute.return_value = {}

        result = list_playlists(service)
        assert result == []


class TestCreatePlaylist:
    """create_playlist API 테스트."""

    def test_success(self) -> None:
        """성공적으로 플레이리스트 생성."""
        from tubearchive.infra.youtube.playlist import create_playlist

        service = MagicMock()
        service.playlists().insert().execute.return_value = {"id": "PLnew"}

        result = create_playlist(service, "테스트 리스트")
        assert result == "PLnew"

    def test_missing_id_raises(self) -> None:
        """응답에 id 없으면 PlaylistError."""
        from tubearchive.infra.youtube.playlist import PlaylistError, create_playlist

        service = MagicMock()
        service.playlists().insert().execute.return_value = {}

        with pytest.raises(PlaylistError, match="missing"):
            create_playlist(service, "테스트")

    def test_api_exception_wraps(self) -> None:
        """API 예외를 PlaylistError로 래핑."""
        from tubearchive.infra.youtube.playlist import PlaylistError, create_playlist

        service = MagicMock()
        service.playlists().insert().execute.side_effect = RuntimeError("API error")

        with pytest.raises(PlaylistError, match="Failed to create"):
            create_playlist(service, "테스트")

    def test_passes_privacy(self) -> None:
        """privacy 파라미터가 body에 올바르게 전달."""
        from tubearchive.infra.youtube.playlist import create_playlist

        service = MagicMock()
        service.playlists().insert().execute.return_value = {"id": "PLnew"}

        create_playlist(service, "테스트", privacy="private")

        call_kwargs = service.playlists().insert.call_args
        body = call_kwargs[1]["body"]
        assert body["status"]["privacyStatus"] == "private"


class TestAddToPlaylist:
    """add_to_playlist API 테스트."""

    def test_success(self) -> None:
        """성공적으로 영상 추가."""
        from tubearchive.infra.youtube.playlist import add_to_playlist

        service = MagicMock()
        service.playlistItems().insert().execute.return_value = {"id": "ITEM1"}

        result = add_to_playlist(service, "PL1", "VIDEO1")
        assert result == "ITEM1"

    def test_missing_id_raises(self) -> None:
        """응답에 id 없으면 PlaylistError."""
        from tubearchive.infra.youtube.playlist import PlaylistError, add_to_playlist

        service = MagicMock()
        service.playlistItems().insert().execute.return_value = {}

        with pytest.raises(PlaylistError, match="missing"):
            add_to_playlist(service, "PL1", "VIDEO1")

    def test_api_exception_wraps(self) -> None:
        """API 예외를 PlaylistError로 래핑."""
        from tubearchive.infra.youtube.playlist import PlaylistError, add_to_playlist

        service = MagicMock()
        service.playlistItems().insert().execute.side_effect = RuntimeError("API error")

        with pytest.raises(PlaylistError, match="Failed to add"):
            add_to_playlist(service, "PL1", "VIDEO1")

    def test_correct_resource_body(self) -> None:
        """playlistId, videoId가 body에 올바르게 전달."""
        from tubearchive.infra.youtube.playlist import add_to_playlist

        service = MagicMock()
        service.playlistItems().insert().execute.return_value = {"id": "ITEM1"}

        add_to_playlist(service, "PL1", "VIDEO1")

        call_kwargs = service.playlistItems().insert.call_args
        body = call_kwargs[1]["body"]
        assert body["snippet"]["playlistId"] == "PL1"
        assert body["snippet"]["resourceId"]["videoId"] == "VIDEO1"


class TestScheduleUpload:
    """스케줄 업로드 기능 테스트."""

    def test_parse_schedule_datetime_valid_iso8601(self) -> None:
        """유효한 ISO 8601 형식 파싱."""
        from tubearchive.app.cli.main import parse_schedule_datetime

        # 미래 시간 (2050년)
        result = parse_schedule_datetime("2050-12-31T23:59:59+09:00")
        assert result == "2050-12-31T23:59:59+09:00"

    def test_parse_schedule_datetime_without_timezone(self) -> None:
        """타임존 없는 형식 (로컬 타임존 자동 추가)."""
        from tubearchive.app.cli.main import parse_schedule_datetime

        # 미래 시간 (2050년)
        with patch("tubearchive.app.cli.parser.logger") as mock_logger:
            result = parse_schedule_datetime("2050-12-31T23:59:59")
            # 로컬 타임존이 추가되어야 함
            assert result.startswith("2050-12-31T23:59:59")
            # info 로그 확인 (타임존 추가 알림)
            mock_logger.info.assert_called()
            assert "timezone" in str(mock_logger.info.call_args).lower()

    def test_parse_schedule_datetime_space_format(self) -> None:
        """공백 구분 형식 자동 변환."""
        from tubearchive.app.cli.main import parse_schedule_datetime

        # 공백 형식도 지원 ("2050-12-31 23:59:59" → "2050-12-31T23:59:59")
        result = parse_schedule_datetime("2050-12-31 23:59:59+09:00")
        assert result == "2050-12-31T23:59:59+09:00"

    def test_parse_schedule_datetime_past_time_raises(self) -> None:
        """과거 시간은 상세한 에러 메시지와 함께 ValueError 발생."""
        from tubearchive.app.cli.main import parse_schedule_datetime

        with pytest.raises(ValueError) as exc_info:
            parse_schedule_datetime("2020-01-01T00:00:00+09:00")

        # 에러 메시지에 "future"와 시간 차이 정보 포함 확인
        error_msg = str(exc_info.value)
        assert "future" in error_msg.lower()
        # 과거 시간이므로 "일 전" 또는 "시간 전" 등의 정보 포함
        assert any(word in error_msg for word in ["전", "ago", "Current time"])

    def test_parse_schedule_datetime_invalid_format_raises(self) -> None:
        """잘못된 형식은 ValueError 발생."""
        from tubearchive.app.cli.main import parse_schedule_datetime

        with pytest.raises(ValueError, match="Invalid datetime format"):
            parse_schedule_datetime("not-a-date")

    def test_schedule_option_in_parser(self) -> None:
        """--schedule 옵션이 파서에 존재."""
        from tubearchive.app.cli.main import create_parser

        parser = create_parser()
        args = parser.parse_args(["--schedule", "2050-12-31T18:00:00+09:00"])
        assert args.schedule == "2050-12-31T18:00:00+09:00"

    def test_upload_result_with_schedule(self) -> None:
        """UploadResult에 scheduled_publish_at 포함."""
        from tubearchive.infra.youtube.uploader import UploadResult

        result = UploadResult.from_video_id(
            "xyz789", "My Title", scheduled_publish_at="2050-12-31T18:00:00+09:00"
        )
        assert result.scheduled_publish_at == "2050-12-31T18:00:00+09:00"

    def test_upload_with_schedule_sets_private(self, tmp_path: Path) -> None:
        """publish_at 설정 시 privacy가 private로 자동 변경."""
        from tubearchive.infra.youtube.uploader import YouTubeUploader

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video content")

        mock_service = MagicMock()
        mock_insert = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_insert
        mock_insert.next_chunk.return_value = (None, {"id": "test_id"})

        uploader = YouTubeUploader(mock_service)

        with patch("tubearchive.infra.youtube.uploader.MediaFileUpload") as mock_media_upload:
            mock_media_upload.return_value = MagicMock()
            uploader.upload(
                video_file,
                title="Test",
                privacy="unlisted",
                publish_at="2050-12-31T18:00:00+09:00",
            )

        # insert 호출 시 body 확인
        call_args = mock_service.videos.return_value.insert.call_args
        body = call_args.kwargs.get("body") or call_args[1].get("body")

        # privacy가 private로 변경되었는지 확인
        assert body["status"]["privacyStatus"] == "private"

        # status에 publishAt이 포함되었는지 확인 (YouTube API 명세)
        assert "publishAt" in body["status"]
        assert body["status"]["publishAt"] == "2050-12-31T18:00:00+09:00"

    def test_validated_args_includes_schedule(self) -> None:
        """ValidatedArgs에 schedule 필드 포함."""
        import dataclasses

        from tubearchive.app.cli.main import ValidatedArgs

        fields = {f.name for f in dataclasses.fields(ValidatedArgs)}
        assert "schedule" in fields


class TestSelectPlaylistInteractive:
    """select_playlist_interactive 인터랙션 테스트."""

    def _make_playlists(self, count: int = 3) -> list:
        from tubearchive.infra.youtube.playlist import Playlist

        return [
            Playlist(id=f"PL{i}", title=f"리스트{i}", item_count=i) for i in range(1, count + 1)
        ]

    def test_empty_list_returns_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        """빈 목록 → 빈 리스트 반환."""
        from tubearchive.infra.youtube.playlist import select_playlist_interactive

        result = select_playlist_interactive([])

        assert result == []
        captured = capsys.readouterr()
        assert "없습니다" in captured.out

    def test_single_selection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """단일 선택 (번호 1 입력)."""
        from tubearchive.infra.youtube.playlist import select_playlist_interactive

        playlists = self._make_playlists()
        monkeypatch.setattr("builtins.input", lambda _: "1")

        result = select_playlist_interactive(playlists)

        assert len(result) == 1
        assert result[0].id == "PL1"

    def test_cancel_with_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """0 입력으로 취소."""
        from tubearchive.infra.youtube.playlist import select_playlist_interactive

        playlists = self._make_playlists()
        monkeypatch.setattr("builtins.input", lambda _: "0")

        result = select_playlist_interactive(playlists)
        assert result == []

    def test_multi_selection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """여러 개 선택 (1,3 입력)."""
        from tubearchive.infra.youtube.playlist import select_playlist_interactive

        playlists = self._make_playlists()
        monkeypatch.setattr("builtins.input", lambda _: "1,3")

        result = select_playlist_interactive(playlists)

        assert len(result) == 2
        assert result[0].id == "PL1"
        assert result[1].id == "PL3"

    def test_invalid_then_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """잘못된 입력 후 올바른 입력."""
        from tubearchive.infra.youtube.playlist import select_playlist_interactive

        playlists = self._make_playlists()
        inputs = iter(["abc", "1"])
        monkeypatch.setattr("builtins.input", lambda _: next(inputs))

        result = select_playlist_interactive(playlists)

        assert len(result) == 1
        assert result[0].id == "PL1"

    def test_keyboard_interrupt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """EOFError → 빈 리스트 반환."""
        from tubearchive.infra.youtube.playlist import select_playlist_interactive

        playlists = self._make_playlists()

        def raise_eof(_: str) -> str:
            raise EOFError()

        monkeypatch.setattr("builtins.input", raise_eof)

        result = select_playlist_interactive(playlists)
        assert result == []


# ---------------------------------------------------------------------------
# YouTube CLI 커맨드 (youtube.py) 테스트
# ---------------------------------------------------------------------------


class TestCmdSetupYoutube:
    """cmd_setup_youtube: 인증 상태 가이드 출력."""

    def test_prints_guide(self, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import MagicMock, patch

        from tubearchive.app.cli.youtube import cmd_setup_youtube

        mock_status = MagicMock()
        mock_status.get_setup_guide.return_value = "Setup guide text"
        mock_status.needs_browser_auth = False

        with patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=mock_status):
            cmd_setup_youtube()

        out = capsys.readouterr().out
        assert "Setup guide text" in out

    def test_prints_auth_hint_when_browser_auth_needed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from unittest.mock import MagicMock, patch

        from tubearchive.app.cli.youtube import cmd_setup_youtube

        mock_status = MagicMock()
        mock_status.get_setup_guide.return_value = "guide"
        mock_status.needs_browser_auth = True

        with patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=mock_status):
            cmd_setup_youtube()

        out = capsys.readouterr().out
        assert "--youtube-auth" in out


class TestCmdYoutubeAuth:
    """cmd_youtube_auth: OAuth 인증 흐름."""

    def test_already_authenticated_returns_early(self, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import MagicMock, patch

        from tubearchive.app.cli.youtube import cmd_youtube_auth

        mock_status = MagicMock()
        mock_status.has_valid_token = True
        mock_status.token_path = "/path/to/token.json"

        with patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=mock_status):
            cmd_youtube_auth()

        out = capsys.readouterr().out
        assert "이미 인증되어 있습니다" in out

    def test_no_client_secrets_raises(self) -> None:
        from unittest.mock import MagicMock, patch

        import pytest

        from tubearchive.app.cli.youtube import cmd_youtube_auth
        from tubearchive.infra.youtube.auth import YouTubeAuthError

        mock_status = MagicMock()
        mock_status.has_valid_token = False
        mock_status.has_client_secrets = False
        mock_status.client_secrets_path = "/missing/secrets.json"

        with (
            patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=mock_status),
            pytest.raises(YouTubeAuthError),
        ):
            cmd_youtube_auth()

    def test_successful_auth_saves_credentials(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from unittest.mock import MagicMock, patch

        from tubearchive.app.cli.youtube import cmd_youtube_auth

        mock_status = MagicMock()
        mock_status.has_valid_token = False
        mock_status.has_client_secrets = True

        mock_creds = MagicMock()
        token_path = tmp_path / "token.json"

        with (
            patch("tubearchive.infra.youtube.auth.check_auth_status", return_value=mock_status),
            patch(
                "tubearchive.infra.youtube.auth.get_client_secrets_path",
                return_value=tmp_path / "secrets.json",
            ),
            patch("tubearchive.infra.youtube.auth.get_token_path", return_value=token_path),
            patch("tubearchive.infra.youtube.auth.run_auth_flow", return_value=mock_creds),
            patch("tubearchive.infra.youtube.auth.save_credentials") as mock_save,
        ):
            cmd_youtube_auth()

        mock_save.assert_called_once_with(mock_creds, token_path)
        out = capsys.readouterr().out
        assert "인증 완료" in out


class TestCmdListPlaylists:
    """cmd_list_playlists: 플레이리스트 목록 조회."""

    def test_prints_playlist_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import MagicMock, patch

        from tubearchive.app.cli.youtube import cmd_list_playlists
        from tubearchive.infra.youtube.playlist import Playlist

        mock_service = MagicMock()
        playlists = [
            Playlist(id="PLabc", title="My Playlist", item_count=5),
            Playlist(id="PLxyz", title="Another", item_count=10),
        ]

        with (
            patch(
                "tubearchive.infra.youtube.auth.get_authenticated_service",
                return_value=mock_service,
            ),
            patch("tubearchive.infra.youtube.playlist.list_playlists", return_value=playlists),
        ):
            cmd_list_playlists()

        out = capsys.readouterr().out
        assert "My Playlist" in out
        assert "PLabc" in out

    def test_empty_playlist_shows_message(self, capsys: pytest.CaptureFixture[str]) -> None:
        from unittest.mock import MagicMock, patch

        from tubearchive.app.cli.youtube import cmd_list_playlists

        mock_service = MagicMock()

        with (
            patch(
                "tubearchive.infra.youtube.auth.get_authenticated_service",
                return_value=mock_service,
            ),
            patch("tubearchive.infra.youtube.playlist.list_playlists", return_value=[]),
        ):
            cmd_list_playlists()

        out = capsys.readouterr().out
        assert "플레이리스트가 없습니다" in out

    def test_api_error_raises(self) -> None:
        from unittest.mock import patch

        import pytest

        from tubearchive.app.cli.youtube import cmd_list_playlists

        with (
            patch(
                "tubearchive.infra.youtube.auth.get_authenticated_service",
                side_effect=Exception("API error"),
            ),
            pytest.raises(Exception, match="API error"),
        ):
            cmd_list_playlists()
