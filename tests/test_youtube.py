"""YouTube 업로드 모듈 테스트."""

import json
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestYouTubeAuth:
    """YouTube 인증 테스트."""

    def test_get_config_dir_creates_directory(self, tmp_path: Path) -> None:
        """설정 디렉토리가 없으면 생성."""
        from tubearchive.youtube.auth import get_config_dir

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            config_dir = get_config_dir()
            # ~/.tubearchive 경로여야 함
            assert config_dir.name == ".tubearchive"

    def test_get_token_path_default(self, tmp_path: Path) -> None:
        """기본 토큰 경로."""
        from tubearchive.youtube.auth import get_token_path

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            token_path = get_token_path()
            assert token_path.name == "youtube_token.json"
            assert ".tubearchive" in str(token_path)

    def test_get_token_path_from_env(self, tmp_path: Path) -> None:
        """환경 변수로 토큰 경로 지정."""
        from tubearchive.youtube.auth import get_token_path

        custom_path = tmp_path / "custom_token.json"
        with patch.dict("os.environ", {"TUBEARCHIVE_YOUTUBE_TOKEN": str(custom_path)}, clear=False):
            token_path = get_token_path()
            assert token_path == custom_path

    def test_get_client_secrets_path_default(self, tmp_path: Path) -> None:
        """기본 클라이언트 시크릿 경로."""
        from tubearchive.youtube.auth import get_client_secrets_path

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            secrets_path = get_client_secrets_path()
            assert secrets_path.name == "client_secrets.json"
            assert ".tubearchive" in str(secrets_path)

    def test_get_client_secrets_path_from_env(self, tmp_path: Path) -> None:
        """환경 변수로 클라이언트 시크릿 경로 지정."""
        from tubearchive.youtube.auth import get_client_secrets_path

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
        from tubearchive.youtube.auth import load_credentials

        token_path = tmp_path / "nonexistent_token.json"
        credentials = load_credentials(token_path)
        assert credentials is None

    def test_load_credentials_loads_valid_token(self, tmp_path: Path) -> None:
        """유효한 토큰 파일 로드."""
        from tubearchive.youtube.auth import load_credentials

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

        with patch("tubearchive.youtube.auth.Credentials") as mock_creds:
            mock_creds.from_authorized_user_info.return_value = MagicMock(valid=True)
            credentials = load_credentials(token_path)
            assert credentials is not None
            mock_creds.from_authorized_user_info.assert_called_once()

    def test_save_credentials(self, tmp_path: Path) -> None:
        """자격 증명 저장."""
        from tubearchive.youtube.auth import save_credentials

        token_path = tmp_path / "token.json"
        mock_credentials = MagicMock()
        mock_credentials.to_json.return_value = '{"token": "test"}'

        save_credentials(mock_credentials, token_path)

        assert token_path.exists()
        assert json.loads(token_path.read_text()) == {"token": "test"}

    def test_get_authenticated_service_raises_without_secrets(self, tmp_path: Path) -> None:
        """클라이언트 시크릿 없으면 에러."""
        from tubearchive.youtube.auth import (
            YouTubeAuthError,
            get_authenticated_service,
        )

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            with pytest.raises(YouTubeAuthError) as exc_info:
                get_authenticated_service()
            assert "client_secrets.json" in str(exc_info.value)


class TestUploadResult:
    """UploadResult 데이터 클래스 테스트."""

    def test_upload_result_creation(self) -> None:
        """UploadResult 생성."""
        from tubearchive.youtube.uploader import UploadResult

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
        from tubearchive.youtube.uploader import UploadResult

        result = UploadResult.from_video_id("xyz789", "My Title")
        assert result.video_id == "xyz789"
        assert result.url == "https://youtu.be/xyz789"
        assert result.title == "My Title"


class TestYouTubeUploader:
    """YouTubeUploader 테스트."""

    def test_uploader_init(self) -> None:
        """Uploader 초기화."""
        from tubearchive.youtube.uploader import YouTubeUploader

        mock_service = MagicMock()
        uploader = YouTubeUploader(mock_service)
        assert uploader.service == mock_service

    def test_upload_validates_file_exists(self, tmp_path: Path) -> None:
        """존재하지 않는 파일 업로드 시 에러."""
        from tubearchive.youtube.uploader import YouTubeUploader

        mock_service = MagicMock()
        uploader = YouTubeUploader(mock_service)

        nonexistent = tmp_path / "nonexistent.mp4"
        with pytest.raises(FileNotFoundError):
            uploader.upload(nonexistent, "Test")

    def test_upload_calls_youtube_api(self, tmp_path: Path) -> None:
        """YouTube API 호출 확인."""
        from tubearchive.youtube.uploader import YouTubeUploader

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

        with patch("tubearchive.youtube.uploader.MediaFileUpload") as mock_media_upload:
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
        from tubearchive.youtube.uploader import YouTubeUploader

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

        with patch("tubearchive.youtube.uploader.MediaFileUpload") as mock_media_upload:
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
        from tubearchive.youtube.uploader import YouTubeUploader

        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"fake video content")

        mock_service = MagicMock()
        mock_insert = MagicMock()
        mock_service.videos.return_value.insert.return_value = mock_insert
        mock_insert.next_chunk.return_value = (None, {"id": "test_id"})

        uploader = YouTubeUploader(mock_service)

        with patch("tubearchive.youtube.uploader.MediaFileUpload") as mock_media_upload:
            mock_media_upload.return_value = MagicMock()
            uploader.upload(video_file, title="Test")

        # insert 호출 시 body의 status.privacyStatus 확인
        call_args = mock_service.videos.return_value.insert.call_args
        body = call_args.kwargs.get("body") or call_args[1].get("body")
        assert body["status"]["privacyStatus"] == "unlisted"


class TestYouTubeUploadError:
    """업로드 에러 처리 테스트."""

    def test_upload_handles_api_error(self, tmp_path: Path) -> None:
        """API 에러 처리."""
        from googleapiclient.errors import HttpError

        from tubearchive.youtube.uploader import YouTubeUploader, YouTubeUploadError

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

        with patch("tubearchive.youtube.uploader.MediaFileUpload") as mock_media_upload:
            mock_media_upload.return_value = MagicMock()
            with pytest.raises(YouTubeUploadError) as exc_info:
                uploader.upload(video_file, title="Test")

        assert "quota" in str(exc_info.value).lower() or "403" in str(exc_info.value)


class TestCLIUploadIntegration:
    """CLI upload 관련 통합 테스트."""

    def test_upload_only_option_exists(self) -> None:
        """--upload-only 옵션이 존재."""
        from tubearchive.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["--upload-only", "test.mp4"])
        assert args.upload_only == "test.mp4"

    def test_upload_flag_in_main_parser(self) -> None:
        """--upload 플래그가 메인 파서에 존재."""
        from tubearchive.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["--upload"])
        assert args.upload is True

    def test_upload_privacy_option(self) -> None:
        """--upload-privacy 옵션이 존재."""
        from tubearchive.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["--upload-privacy", "private"])
        assert args.upload_privacy == "private"

    def test_upload_title_option(self) -> None:
        """--upload-title 옵션이 존재."""
        from tubearchive.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["--upload-title", "My Video"])
        assert args.upload_title == "My Video"

    def test_validated_args_includes_upload(self, tmp_path: Path) -> None:
        """ValidatedArgs에 upload 필드 포함."""
        import dataclasses

        from tubearchive.cli import ValidatedArgs

        fields = {f.name for f in dataclasses.fields(ValidatedArgs)}
        assert "upload" in fields

    def test_setup_youtube_option_exists(self) -> None:
        """--setup-youtube 옵션이 존재."""
        from tubearchive.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["--setup-youtube"])
        assert args.setup_youtube is True

    def test_youtube_auth_option_exists(self) -> None:
        """--youtube-auth 옵션이 존재."""
        from tubearchive.cli import create_parser

        parser = create_parser()
        args = parser.parse_args(["--youtube-auth"])
        assert args.youtube_auth is True


class TestCheckAuthStatus:
    """인증 상태 확인 기능 테스트."""

    def test_check_auth_status_no_client_secrets(self, tmp_path: Path) -> None:
        """client_secrets.json 없을 때 상태 반환."""
        from tubearchive.youtube.auth import check_auth_status

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            status = check_auth_status()
            assert status.has_client_secrets is False
            assert status.has_valid_token is False
            assert status.needs_browser_auth is False  # secrets 없으면 브라우저 인증 불가

    def test_check_auth_status_has_secrets_no_token(self, tmp_path: Path) -> None:
        """client_secrets.json은 있고 토큰은 없을 때."""
        from tubearchive.youtube.auth import check_auth_status

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
        from tubearchive.youtube.auth import check_auth_status

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

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            with patch("tubearchive.youtube.auth.Credentials") as mock_creds:
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
        from tubearchive.youtube.auth import check_auth_status

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

        with patch.dict("os.environ", {"HOME": str(tmp_path)}, clear=False):
            with patch("tubearchive.youtube.auth.Credentials") as mock_creds:
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
        from tubearchive.youtube.auth import AuthStatus

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
        from tubearchive.youtube.auth import AuthStatus

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
        from tubearchive.youtube.auth import AuthStatus

        status = AuthStatus(
            has_client_secrets=True,
            has_valid_token=True,
            needs_browser_auth=False,
            client_secrets_path=Path("~/.tubearchive/client_secrets.json"),
            token_path=Path("~/.tubearchive/youtube_token.json"),
        )
        guide = status.get_setup_guide()

        assert "완료" in guide or "준비" in guide or "✅" in guide
