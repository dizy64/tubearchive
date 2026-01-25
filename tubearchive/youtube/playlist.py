"""YouTube 플레이리스트 관리."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from googleapiclient._apis.youtube.v3 import YouTubeResource

logger = logging.getLogger(__name__)


class PlaylistError(Exception):
    """플레이리스트 관련 에러."""

    pass


@dataclass
class Playlist:
    """플레이리스트 정보."""

    id: str
    title: str
    item_count: int

    def __str__(self) -> str:
        return f"{self.title} ({self.item_count}개)"


def list_playlists(service: YouTubeResource) -> list[Playlist]:
    """
    내 플레이리스트 목록 조회.

    Args:
        service: 인증된 YouTube API 서비스

    Returns:
        플레이리스트 목록
    """
    playlists: list[Playlist] = []
    next_page_token: str | None = None

    while True:
        request = service.playlists().list(
            part="snippet,contentDetails",
            mine=True,
            maxResults=50,
            pageToken=next_page_token,
        )
        response = request.execute()

        for item in response.get("items", []):
            playlists.append(
                Playlist(
                    id=item["id"],
                    title=item["snippet"]["title"],
                    item_count=item["contentDetails"]["itemCount"],
                )
            )

        next_page_token = response.get("nextPageToken")
        if not next_page_token:
            break

    logger.info(f"Found {len(playlists)} playlists")
    return playlists


def add_to_playlist(
    service: YouTubeResource,
    playlist_id: str,
    video_id: str,
) -> None:
    """
    영상을 플레이리스트에 추가.

    Args:
        service: 인증된 YouTube API 서비스
        playlist_id: 플레이리스트 ID
        video_id: 추가할 영상 ID

    Raises:
        PlaylistError: 추가 실패 시
    """
    try:
        request = service.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                },
            },
        )
        request.execute()
        logger.info(f"Video {video_id} added to playlist {playlist_id}")

    except Exception as e:
        logger.error(f"Failed to add video to playlist: {e}")
        raise PlaylistError(f"Failed to add video to playlist: {e}") from e


def select_playlist_interactive(
    playlists: list[Playlist],
    multi_select: bool = True,
) -> list[Playlist]:
    """
    터미널에서 플레이리스트 선택.

    Args:
        playlists: 플레이리스트 목록
        multi_select: 여러 개 선택 허용 여부

    Returns:
        선택한 플레이리스트 목록 (취소 시 빈 리스트)
    """
    if not playlists:
        print("📭 플레이리스트가 없습니다.")
        return []

    print("\n📋 플레이리스트 목록:\n")
    for i, pl in enumerate(playlists, 1):
        print(f"  {i}. {pl.title} ({pl.item_count}개)")
    print("  0. 취소")

    if multi_select:
        print("\n💡 여러 개 선택: 쉼표로 구분 (예: 1,3,5)")

    while True:
        try:
            choice = input("\n선택 (번호): ").strip()
            if not choice:
                continue

            # 여러 개 선택 처리
            if multi_select and "," in choice:
                nums = [int(n.strip()) for n in choice.split(",")]
                selected = []
                for num in nums:
                    if num == 0:
                        return []
                    if 1 <= num <= len(playlists):
                        selected.append(playlists[num - 1])
                    else:
                        print(f"  {num}은 유효하지 않은 번호입니다.")
                        selected = []
                        break
                if selected:
                    return selected
                continue

            # 단일 선택
            num = int(choice)
            if num == 0:
                return []
            if 1 <= num <= len(playlists):
                return [playlists[num - 1]]

            print(f"  1~{len(playlists)} 또는 0을 입력하세요.")
        except ValueError:
            print("  숫자를 입력하세요.")
        except (KeyboardInterrupt, EOFError):
            print("\n취소됨")
            return []
