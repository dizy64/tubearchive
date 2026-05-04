"""YouTube 인증·플레이리스트 조회 CLI 커맨드."""

from __future__ import annotations

import logging

from tubearchive.app.cli.upload import (
    cmd_upload_only,
    upload_to_youtube,
)
from tubearchive.config import ENV_YOUTUBE_PLAYLIST

logger = logging.getLogger(__name__)


def cmd_setup_youtube() -> None:
    """
    --setup-youtube 옵션 처리.

    YouTube 인증 상태를 확인하고 설정 가이드를 출력합니다.
    """
    from tubearchive.infra.youtube.auth import check_auth_status

    print("\n🎬 YouTube 업로드 설정 상태\n")
    print("=" * 50)

    status = check_auth_status()
    print(status.get_setup_guide())

    print("=" * 50)

    # 브라우저 인증이 필요하면 바로 실행 제안
    if status.needs_browser_auth:
        print("\n💡 지금 바로 인증하려면:")
        print("   tubearchive --youtube-auth")
        print("   (브라우저가 열리며 Google 계정 인증이 진행됩니다)")


def cmd_youtube_auth() -> None:
    """
    --youtube-auth 옵션 처리.

    브라우저를 열어 YouTube OAuth 인증을 실행합니다.
    """
    from tubearchive.infra.youtube.auth import (
        YouTubeAuthError,
        check_auth_status,
        get_client_secrets_path,
        get_token_path,
        run_auth_flow,
        save_credentials,
    )

    print("\n🔐 YouTube 인증 시작\n")

    # 먼저 상태 확인
    status = check_auth_status()

    if status.has_valid_token:
        print("✅ 이미 인증되어 있습니다!")
        print(f"   토큰 위치: {status.token_path}")
        return

    if not status.has_client_secrets:
        print("❌ client_secrets.json이 없습니다.")
        print(f"   필요한 위치: {status.client_secrets_path}")
        print("\n설정 가이드를 보려면: tubearchive --setup-youtube")
        raise YouTubeAuthError("client_secrets.json not found")

    # 브라우저 인증 실행
    print("🌐 브라우저에서 Google 계정 인증을 진행합니다...")
    print("   (브라우저가 자동으로 열립니다)\n")

    try:
        secrets_path = get_client_secrets_path()
        token_path = get_token_path()

        credentials = run_auth_flow(secrets_path)
        save_credentials(credentials, token_path)

        print("\n✅ 인증 완료!")
        print(f"   토큰 저장됨: {token_path}")
        print("\n이제 업로드할 수 있습니다:")
        print("   tubearchive --upload ~/Videos/")
        print("   tubearchive --upload-only video.mp4")

    except Exception as e:
        logger.error(f"YouTube authentication failed: {e}")
        print(f"\n❌ 인증 실패: {e}")
        raise


def cmd_list_playlists() -> None:
    """
    --list-playlists 옵션 처리.

    내 플레이리스트 목록을 조회하여 ID와 함께 출력합니다.
    """
    from tubearchive.infra.youtube.auth import get_authenticated_service
    from tubearchive.infra.youtube.playlist import list_playlists

    print("\n📋 내 플레이리스트 목록\n")

    try:
        service = get_authenticated_service()
        playlists = list_playlists(service)

        if not playlists:
            print("플레이리스트가 없습니다.")
            return

        print(f"{'번호':<4} {'제목':<40} {'영상수':<8} ID")
        print("-" * 80)
        for i, pl in enumerate(playlists, 1):
            print(f"{i:<4} {pl.title:<40} {pl.item_count:<8} {pl.id}")

        print("-" * 80)
        print("\n💡 환경 변수 설정 예시:")
        print(f"   export {ENV_YOUTUBE_PLAYLIST}={playlists[0].id}")
        if len(playlists) > 1:
            ids = ",".join(pl.id for pl in playlists[:2])
            print(f"   export {ENV_YOUTUBE_PLAYLIST}={ids}  # 여러 개")

    except Exception as e:
        logger.error(f"Failed to list playlists: {e}")
        print(f"\n❌ 플레이리스트 조회 실패: {e}")

        # 스코프 부족 에러 처리
        if "insufficient" in str(e).lower() or "scope" in str(e).lower():
            from tubearchive.infra.youtube.auth import get_token_path

            token_path = get_token_path()
            print("\n💡 권한이 부족합니다. 토큰을 삭제하고 재인증하세요:")
            print(f"   rm {token_path}")
            print("   tubearchive --youtube-auth")
        raise


__all__ = [
    "cmd_list_playlists",
    "cmd_setup_youtube",
    "cmd_upload_only",
    "cmd_youtube_auth",
    "upload_to_youtube",
]
