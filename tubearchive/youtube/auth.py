"""YouTube OAuth 2.0 인증."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

if TYPE_CHECKING:
    from googleapiclient._apis.youtube.v3 import YouTubeResource

logger = logging.getLogger(__name__)

# OAuth 스코프 (업로드 + 플레이리스트 관리)
SCOPES = ["https://www.googleapis.com/auth/youtube"]

# 환경 변수
ENV_CLIENT_SECRETS = "TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS"
ENV_TOKEN = "TUBEARCHIVE_YOUTUBE_TOKEN"

# Google Cloud Console URL
GOOGLE_CLOUD_CONSOLE_URL = "https://console.cloud.google.com/apis/credentials"


class YouTubeAuthError(Exception):
    """YouTube 인증 에러."""

    pass


@dataclass
class AuthStatus:
    """YouTube 인증 상태."""

    has_client_secrets: bool
    has_valid_token: bool
    needs_browser_auth: bool
    client_secrets_path: Path
    token_path: Path

    def get_setup_guide(self) -> str:
        """
        현재 상태에 따른 설정 가이드 반환.

        Returns:
            설정 가이드 문자열
        """
        if self.has_valid_token:
            return f"✅ YouTube 인증 완료!\n   토큰 위치: {self.token_path}"

        if not self.has_client_secrets:
            return (
                "❌ YouTube 설정이 필요합니다.\n\n"
                "📋 설정 단계:\n"
                f"1. Google Cloud Console 접속:\n"
                f"   {GOOGLE_CLOUD_CONSOLE_URL}\n\n"
                "2. 새 프로젝트 생성 또는 기존 프로젝트 선택\n\n"
                "3. YouTube Data API v3 활성화:\n"
                "   - 'APIs & Services' → 'Enabled APIs & services'\n"
                "   - '+ ENABLE APIS AND SERVICES' 클릭\n"
                "   - 'YouTube Data API v3' 검색 후 활성화\n\n"
                "4. OAuth 클라이언트 ID 생성:\n"
                "   - 'APIs & Services' → 'Credentials'\n"
                "   - '+ CREATE CREDENTIALS' → 'OAuth client ID'\n"
                "   - Application type: 'Desktop app'\n"
                "   - JSON 다운로드\n\n"
                "5. 다운로드한 JSON 파일 저장:\n"
                f"   mv ~/Downloads/client_secret_*.json {self.client_secrets_path}\n\n"
                "6. 다시 업로드 명령어 실행"
            )

        if self.needs_browser_auth:
            return (
                "🔐 브라우저 인증이 필요합니다.\n\n"
                f"   client_secrets.json: ✅ {self.client_secrets_path}\n"
                f"   토큰: ❌ 없음 또는 만료\n\n"
                "   업로드 명령어를 실행하면 브라우저가 열리며\n"
                "   Google 계정으로 인증을 진행합니다."
            )

        return "인증 상태를 확인할 수 없습니다."


def get_config_dir() -> Path:
    """
    tubearchive 설정 디렉토리 경로 반환.

    Returns:
        ~/.tubearchive 경로
    """
    config_dir = Path.home() / ".tubearchive"
    config_dir.mkdir(exist_ok=True)
    return config_dir


def get_token_path(custom_path: Path | None = None) -> Path:
    """
    토큰 파일 경로 반환.

    Args:
        custom_path: 사용자 지정 경로 (None이면 환경 변수 또는 기본 경로)

    Returns:
        토큰 파일 경로
    """
    if custom_path is not None:
        return custom_path

    env_path = os.environ.get(ENV_TOKEN)
    if env_path:
        return Path(env_path)

    return get_config_dir() / "youtube_token.json"


def get_client_secrets_path(custom_path: Path | None = None) -> Path:
    """
    클라이언트 시크릿 파일 경로 반환.

    Args:
        custom_path: 사용자 지정 경로 (None이면 환경 변수 또는 기본 경로)

    Returns:
        클라이언트 시크릿 파일 경로
    """
    if custom_path is not None:
        return custom_path

    env_path = os.environ.get(ENV_CLIENT_SECRETS)
    if env_path:
        return Path(env_path)

    return get_config_dir() / "client_secrets.json"


def load_credentials(token_path: Path) -> Credentials | None:
    """
    저장된 자격 증명 로드.

    Args:
        token_path: 토큰 파일 경로

    Returns:
        자격 증명 객체 (없거나 무효하면 None)
    """
    if not token_path.exists():
        return None

    try:
        token_data = json.loads(token_path.read_text())
        credentials: Credentials = Credentials.from_authorized_user_info(  # type: ignore[no-untyped-call]
            token_data, SCOPES
        )
        return credentials
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.warning(f"Failed to load credentials: {e}")
        return None


def save_credentials(credentials: Credentials, token_path: Path) -> None:
    """
    자격 증명 저장.

    Args:
        credentials: 저장할 자격 증명
        token_path: 저장 경로
    """
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_json: str = credentials.to_json()  # type: ignore[no-untyped-call]
    token_path.write_text(token_json)
    logger.info(f"Credentials saved to {token_path}")


def refresh_credentials(credentials: Credentials) -> Credentials:
    """
    만료된 자격 증명 갱신.

    Args:
        credentials: 갱신할 자격 증명

    Returns:
        갱신된 자격 증명
    """
    if credentials.expired and credentials.refresh_token:
        logger.info("Refreshing expired credentials...")
        credentials.refresh(Request())
    return credentials


def run_auth_flow(client_secrets_path: Path) -> Credentials:
    """
    OAuth 인증 플로우 실행.

    브라우저를 열어 사용자 인증을 받습니다.

    Args:
        client_secrets_path: 클라이언트 시크릿 파일 경로

    Returns:
        인증된 자격 증명
    """
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_path), SCOPES)
    credentials: Credentials = flow.run_local_server(port=0)
    return credentials


def check_auth_status(
    client_secrets_path: Path | None = None,
    token_path: Path | None = None,
) -> AuthStatus:
    """
    YouTube 인증 상태 확인.

    Args:
        client_secrets_path: 클라이언트 시크릿 파일 경로
        token_path: 토큰 파일 경로

    Returns:
        AuthStatus 객체
    """
    secrets_path = get_client_secrets_path(client_secrets_path)
    token_file = get_token_path(token_path)

    has_client_secrets = secrets_path.exists()
    has_valid_token = False
    needs_browser_auth = False

    if has_client_secrets:
        # 토큰 확인
        credentials = load_credentials(token_file)
        if credentials is not None:
            if credentials.valid:
                has_valid_token = True
            elif credentials.expired and credentials.refresh_token:
                # 갱신 가능 → 유효한 토큰으로 간주
                has_valid_token = False
                needs_browser_auth = True
            else:
                needs_browser_auth = True
        else:
            needs_browser_auth = True

    return AuthStatus(
        has_client_secrets=has_client_secrets,
        has_valid_token=has_valid_token,
        needs_browser_auth=needs_browser_auth,
        client_secrets_path=secrets_path,
        token_path=token_file,
    )


def get_authenticated_service(
    client_secrets_path: Path | None = None,
    token_path: Path | None = None,
) -> YouTubeResource:
    """
    인증된 YouTube API 서비스 반환.

    1. 저장된 토큰이 있으면 로드
    2. 만료되었으면 갱신
    3. 토큰이 없으면 새 인증 플로우 실행

    Args:
        client_secrets_path: 클라이언트 시크릿 파일 경로
        token_path: 토큰 파일 경로

    Returns:
        YouTube API 서비스 객체

    Raises:
        YouTubeAuthError: 인증 실패 시
    """
    secrets_path = get_client_secrets_path(client_secrets_path)
    token_file = get_token_path(token_path)

    # 1. 저장된 토큰 로드 시도
    credentials = load_credentials(token_file)

    # 2. 토큰이 있고 만료되었으면 갱신
    if credentials is not None and credentials.expired and credentials.refresh_token:
        try:
            credentials = refresh_credentials(credentials)
            save_credentials(credentials, token_file)
        except Exception as e:
            logger.warning(f"Failed to refresh credentials: {e}")
            credentials = None

    # 3. 유효한 토큰이 없으면 새 인증 플로우
    if credentials is None or not credentials.valid:
        if not secrets_path.exists():
            raise YouTubeAuthError(
                f"client_secrets.json not found at {secrets_path}\n"
                f"1. Google Cloud Console에서 OAuth 클라이언트 ID 생성\n"
                f"2. JSON 다운로드 후 {secrets_path}에 저장\n"
                f"또는 환경 변수 설정: {ENV_CLIENT_SECRETS}=/path/to/client_secrets.json"
            )

        logger.info("Starting OAuth authentication flow...")
        credentials = run_auth_flow(secrets_path)
        save_credentials(credentials, token_file)

    # 서비스 빌드
    service: YouTubeResource = build("youtube", "v3", credentials=credentials)
    return service
