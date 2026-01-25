"""YouTube 영상 업로드."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

if TYPE_CHECKING:
    from googleapiclient._apis.youtube.v3 import YouTubeResource

logger = logging.getLogger(__name__)

# 업로드 청크 크기 (1MB)
CHUNK_SIZE = 1 * 1024 * 1024

# 재시도 가능한 HTTP 상태 코드
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

# 최대 재시도 횟수
MAX_RETRIES = 10


class YouTubeUploadError(Exception):
    """YouTube 업로드 에러."""

    pass


@dataclass
class UploadResult:
    """업로드 결과."""

    video_id: str
    url: str
    title: str

    @classmethod
    def from_video_id(cls, video_id: str, title: str) -> UploadResult:
        """
        video_id로 UploadResult 생성.

        Args:
            video_id: YouTube 영상 ID
            title: 영상 제목

        Returns:
            UploadResult 인스턴스
        """
        return cls(
            video_id=video_id,
            url=f"https://youtu.be/{video_id}",
            title=title,
        )


class YouTubeUploader:
    """YouTube 영상 업로더."""

    def __init__(self, service: YouTubeResource) -> None:
        """
        초기화.

        Args:
            service: 인증된 YouTube API 서비스
        """
        self.service = service

    def upload(
        self,
        file_path: Path,
        title: str,
        description: str = "",
        privacy: str = "unlisted",
        on_progress: Callable[[int], None] | None = None,
    ) -> UploadResult:
        """
        영상 업로드.

        Resumable upload를 사용하여 대용량 파일도 안정적으로 업로드합니다.

        Args:
            file_path: 업로드할 영상 파일 경로
            title: 영상 제목
            description: 영상 설명
            privacy: 공개 설정 (public, unlisted, private)
            on_progress: 진행률 콜백 (0-100)

        Returns:
            업로드 결과

        Raises:
            FileNotFoundError: 파일이 존재하지 않을 때
            YouTubeUploadError: 업로드 실패 시
        """
        if not file_path.exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        logger.info(f"Uploading {file_path} to YouTube...")
        logger.info(f"  Title: {title}")
        logger.info(f"  Privacy: {privacy}")

        # 메타데이터 설정
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "categoryId": "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        # 미디어 파일 설정 (resumable upload)
        media = MediaFileUpload(
            str(file_path),
            chunksize=CHUNK_SIZE,
            resumable=True,
            mimetype="video/*",
        )

        # 업로드 요청 생성
        request = self.service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        # 업로드 실행
        response = self._execute_upload(request, on_progress)

        video_id = response["id"]
        result = UploadResult.from_video_id(video_id, title)

        logger.info(f"Upload complete: {result.url}")
        return result

    def _execute_upload(
        self,
        request: Any,
        on_progress: Callable[[int], None] | None = None,
    ) -> dict[str, Any]:
        """
        Resumable upload 실행.

        Args:
            request: 업로드 요청 객체
            on_progress: 진행률 콜백

        Returns:
            API 응답

        Raises:
            YouTubeUploadError: 업로드 실패 시
        """
        response: dict[str, Any] | None = None
        retries = 0

        while response is None:
            try:
                status, response = request.next_chunk()

                if status is not None:
                    progress_percent = int(status.progress() * 100)
                    logger.debug(f"Upload progress: {progress_percent}%")
                    if on_progress:
                        on_progress(progress_percent)

            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS_CODES:
                    retries += 1
                    if retries > MAX_RETRIES:
                        raise YouTubeUploadError(
                            f"Max retries exceeded: {e.resp.status} {e.resp.reason}"
                        ) from e
                    logger.warning(
                        f"Retriable error {e.resp.status}, retry {retries}/{MAX_RETRIES}"
                    )
                    continue
                else:
                    raise YouTubeUploadError(
                        f"Upload failed: {e.resp.status} {e.resp.reason}"
                    ) from e

        # 완료 콜백
        if on_progress:
            on_progress(100)

        return response
