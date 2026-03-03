"""YouTube Resumable Upload API를 통한 영상 업로드.

청크 단위 업로드(resumable upload)를 지원하며,
업로드 전 파일 크기·길이 검증을 수행한다.
"""

import logging
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from tubearchive.infra.ffmpeg.thumbnail import prepare_thumbnail_for_youtube

if TYPE_CHECKING:
    from googleapiclient._apis.youtube.v3 import YouTubeResource

logger = logging.getLogger(__name__)

# YouTube 업로드 제한
# - 인증 계정: 256GB / 12시간
# - 미인증 계정: 128GB / 15분
# 안전하게 인증 계정 기준 사용 (대부분 API 사용자는 인증됨)
YOUTUBE_MAX_FILE_SIZE_GB = 256
YOUTUBE_MAX_DURATION_HOURS = 12
YOUTUBE_MAX_FILE_SIZE_BYTES = YOUTUBE_MAX_FILE_SIZE_GB * 1024 * 1024 * 1024
YOUTUBE_MAX_DURATION_SECONDS = YOUTUBE_MAX_DURATION_HOURS * 60 * 60

# 업로드 청크 크기 설정
# - 환경변수: TUBEARCHIVE_UPLOAD_CHUNK_MB (MB 단위, 1-256)
# - 기본값: 32MB (고속 네트워크용)
# - YouTube API 최대: 256MB
ENV_UPLOAD_CHUNK_MB = "TUBEARCHIVE_UPLOAD_CHUNK_MB"
DEFAULT_CHUNK_MB = 32
MIN_CHUNK_MB = 1
MAX_CHUNK_MB = 256

# YouTube 카테고리 ID (https://developers.google.com/youtube/v3/docs/videoCategories)
YOUTUBE_CATEGORY_PEOPLE_BLOGS = "22"

# 업로드 제한 경고 임계치 (최대치의 90% 도달 시 경고)
UPLOAD_LIMIT_WARNING_RATIO = 0.9

# YouTube description 제한
YOUTUBE_MAX_DESCRIPTION_LENGTH = 5000
# YouTube description에서 허용되지 않는 문자 패턴
_INVALID_DESCRIPTION_CHARS = re.compile(r"[<>]")


def sanitize_description(description: str) -> str:
    """YouTube description을 API 제한에 맞게 정제한다.

    - ``<`` / ``>`` 등 허용되지 않는 문자를 제거한다.
    - 5000자 초과 시 마지막 완전한 줄까지만 유지하고 말줄임 표시를 추가한다.

    Args:
        description: 원본 설명 문자열.

    Returns:
        정제된 설명 문자열.
    """
    # 허용되지 않는 문자 제거
    cleaned = _INVALID_DESCRIPTION_CHARS.sub("", description)

    if len(cleaned) <= YOUTUBE_MAX_DESCRIPTION_LENGTH:
        return cleaned

    # 말줄임 표시를 위한 여유 확보
    truncation_marker = "\n\n..."
    budget = YOUTUBE_MAX_DESCRIPTION_LENGTH - len(truncation_marker)

    # 마지막 완전한 줄 경계에서 자르기
    truncated = cleaned[:budget]
    last_newline = truncated.rfind("\n")
    if last_newline > 0:
        truncated = truncated[:last_newline]

    logger.warning(
        "YouTube description truncated: %d -> %d chars",
        len(cleaned),
        len(truncated) + len(truncation_marker),
    )
    return truncated + truncation_marker


def get_chunk_size(chunk_mb: int | None = None) -> int:
    """
    청크 크기 결정 (바이트 단위).

    우선순위: 인자 > 환경변수 > 기본값

    Args:
        chunk_mb: MB 단위 청크 크기 (None이면 환경변수/기본값 사용)

    Returns:
        바이트 단위 청크 크기
    """
    if chunk_mb is None:
        env_val = os.environ.get(ENV_UPLOAD_CHUNK_MB)
        if env_val:
            try:
                chunk_mb = int(env_val)
            except ValueError:
                logger.warning(f"{ENV_UPLOAD_CHUNK_MB}={env_val} is not valid, using default")
                chunk_mb = DEFAULT_CHUNK_MB
        else:
            chunk_mb = DEFAULT_CHUNK_MB

    # 범위 제한
    if chunk_mb < MIN_CHUNK_MB:
        logger.warning(f"Chunk size {chunk_mb}MB too small, using {MIN_CHUNK_MB}MB")
        chunk_mb = MIN_CHUNK_MB
    elif chunk_mb > MAX_CHUNK_MB:
        logger.warning(f"Chunk size {chunk_mb}MB too large, using {MAX_CHUNK_MB}MB")
        chunk_mb = MAX_CHUNK_MB

    return chunk_mb * 1024 * 1024


@dataclass
class UploadValidation:
    """업로드 전 검증 결과 (파일 크기, 길이, 제한 초과 여부)."""

    is_valid: bool
    file_size_bytes: int
    duration_seconds: float
    errors: list[str]
    warnings: list[str]

    @property
    def file_size_gb(self) -> float:
        """파일 크기 (GB)."""
        return self.file_size_bytes / (1024 * 1024 * 1024)

    @property
    def duration_hours(self) -> float:
        """영상 길이 (시간)."""
        return self.duration_seconds / 3600

    def get_summary(self) -> str:
        """검증 결과 요약 메시지."""
        lines = []

        # 파일 정보
        if self.file_size_gb >= 1:
            size_str = f"{self.file_size_gb:.1f}GB"
        else:
            size_str = f"{self.file_size_bytes / (1024 * 1024):.0f}MB"

        hours = int(self.duration_seconds // 3600)
        minutes = int((self.duration_seconds % 3600) // 60)
        seconds = int(self.duration_seconds % 60)
        if hours > 0:
            duration_str = f"{hours}시간 {minutes}분 {seconds}초"
        elif minutes > 0:
            duration_str = f"{minutes}분 {seconds}초"
        else:
            duration_str = f"{seconds}초"

        lines.append(f"📊 파일 크기: {size_str} (제한: {YOUTUBE_MAX_FILE_SIZE_GB}GB)")
        lines.append(f"⏱️  영상 길이: {duration_str} (제한: {YOUTUBE_MAX_DURATION_HOURS}시간)")

        if self.errors:
            lines.append("")
            lines.append("❌ 업로드 불가:")
            for err in self.errors:
                lines.append(f"   - {err}")

        if self.warnings:
            lines.append("")
            lines.append("⚠️  경고:")
            for warn in self.warnings:
                lines.append(f"   - {warn}")

        return "\n".join(lines)


def get_video_duration(file_path: Path) -> float:
    """
    FFprobe로 영상 길이 조회.

    Args:
        file_path: 영상 파일 경로

    Returns:
        초 단위 영상 길이 (실패 시 0.0)
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        import json

        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except (subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to get video duration: {e}")
        return 0.0


def validate_upload(file_path: Path) -> UploadValidation:
    """
    YouTube 업로드 가능 여부 검증.

    Args:
        file_path: 업로드할 영상 파일 경로

    Returns:
        검증 결과
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 파일 크기 확인
    file_size = file_path.stat().st_size

    # 영상 길이 확인
    duration = get_video_duration(file_path)

    # 파일 크기 검증
    if file_size > YOUTUBE_MAX_FILE_SIZE_BYTES:
        excess_gb = (file_size - YOUTUBE_MAX_FILE_SIZE_BYTES) / (1024 * 1024 * 1024)
        errors.append(f"파일 크기 초과 ({excess_gb:.1f}GB 초과)")
    elif file_size > YOUTUBE_MAX_FILE_SIZE_BYTES * UPLOAD_LIMIT_WARNING_RATIO:
        warnings.append("파일 크기가 제한에 근접함")

    # 영상 길이 검증
    if duration > YOUTUBE_MAX_DURATION_SECONDS:
        excess_hours = (duration - YOUTUBE_MAX_DURATION_SECONDS) / 3600
        errors.append(f"영상 길이 초과 ({excess_hours:.1f}시간 초과)")
    elif duration > YOUTUBE_MAX_DURATION_SECONDS * UPLOAD_LIMIT_WARNING_RATIO:
        warnings.append("영상 길이가 제한에 근접함")

    # 길이를 못 가져온 경우 경고
    if duration == 0.0:
        warnings.append("영상 길이를 확인할 수 없음 (ffprobe 필요)")

    return UploadValidation(
        is_valid=len(errors) == 0,
        file_size_bytes=file_size,
        duration_seconds=duration,
        errors=errors,
        warnings=warnings,
    )


# 재시도 가능한 HTTP 상태 코드
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

# 최대 재시도 횟수
MAX_RETRIES = 10


class YouTubeUploadError(Exception):
    """YouTube 업로드 실패 시 발생하는 예외."""

    pass


@dataclass
class UploadResult:
    """업로드 완료 결과 (YouTube 영상 ID, URL, 제목, 예약 공개 시간)."""

    video_id: str
    url: str
    title: str
    scheduled_publish_at: str | None = None

    @classmethod
    def from_video_id(
        cls, video_id: str, title: str, scheduled_publish_at: str | None = None
    ) -> UploadResult:
        """
        video_id로 UploadResult 생성.

        Args:
            video_id: YouTube 영상 ID
            title: 영상 제목
            scheduled_publish_at: 예약 공개 시간 (ISO 8601 형식, optional)

        Returns:
            UploadResult 인스턴스
        """
        return cls(
            video_id=video_id,
            url=f"https://youtu.be/{video_id}",
            title=title,
            scheduled_publish_at=scheduled_publish_at,
        )


class YouTubeUploader:
    """YouTube Resumable Upload 클라이언트.

    인증된 YouTube API 서비스를 받아 청크 단위로 영상을 업로드한다.
    """

    def __init__(self, service: YouTubeResource, chunk_mb: int | None = None) -> None:
        """
        초기화.

        Args:
            service: 인증된 YouTube API 서비스
            chunk_mb: 업로드 청크 크기 (MB, None이면 환경변수/기본값)
        """
        self.service = service
        self.chunk_size = get_chunk_size(chunk_mb)

    def upload(
        self,
        file_path: Path,
        title: str,
        description: str = "",
        privacy: str = "unlisted",
        publish_at: str | None = None,
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
            publish_at: 예약 공개 시간 (ISO 8601 형식, 설정 시 privacy는 private로 자동 변경)
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

        # 예약 공개 설정 시 privacy를 private로 자동 변경
        if publish_at:
            privacy = "private"
            logger.info(f"  Scheduled publish: {publish_at}")
            logger.info(f"  Privacy: {privacy} (auto-set for scheduled upload)")
        else:
            logger.info(f"  Privacy: {privacy}")

        # description 정제 (길이 제한 + 금지 문자 제거)
        description = sanitize_description(description)

        # 메타데이터 설정
        snippet: dict[str, Any] = {
            "title": title,
            "description": description,
            "categoryId": YOUTUBE_CATEGORY_PEOPLE_BLOGS,
        }

        # status 설정
        status: dict[str, Any] = {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        }

        # 예약 공개 시간 추가 (YouTube API: status에 위치)
        if publish_at:
            status["publishAt"] = publish_at

        body = {
            "snippet": snippet,
            "status": status,
        }

        # 미디어 파일 설정 (resumable upload)
        chunk_mb = self.chunk_size // (1024 * 1024)
        logger.info(f"  Chunk size: {chunk_mb}MB")
        media = MediaFileUpload(
            str(file_path),
            chunksize=self.chunk_size,
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
        result = UploadResult.from_video_id(video_id, title, publish_at)

        logger.info(f"Upload complete: {result.url}")
        if publish_at:
            logger.info(f"Scheduled to publish at: {publish_at}")
        return result

    def set_thumbnail(self, video_id: str, thumbnail_path: Path) -> None:
        """영상 썸네일을 업로드한다.

        Args:
            video_id: 대상 YouTube 영상 ID
            thumbnail_path: 썸네일 이미지 경로
        """
        if not video_id:
            raise ValueError("video_id is required")

        prepared_thumbnail = prepare_thumbnail_for_youtube(thumbnail_path)

        mimetype = "image/png" if prepared_thumbnail.suffix.lower() == ".png" else "image/jpeg"
        media = MediaFileUpload(
            str(prepared_thumbnail),
            mimetype=mimetype,
            resumable=False,
        )

        try:
            request = self.service.thumbnails().set(
                videoId=video_id,
                media_body=media,
            )
            request.execute()
        except HttpError as e:
            raise YouTubeUploadError(
                f"Failed to set thumbnail for video {video_id}: {e.resp.status} {e.resp.reason}"
            ) from e
        finally:
            if prepared_thumbnail != thumbnail_path and prepared_thumbnail.exists():
                prepared_thumbnail.unlink(missing_ok=True)

    def set_captions(
        self,
        video_id: str,
        caption_path: Path,
        language: str | None = None,
        name: str | None = None,
    ) -> None:
        """영상에 자막(캡션)을 업로드한다.

        Args:
            video_id: 대상 YouTube 영상 ID
            caption_path: 자막 파일 경로 (`.srt`/`.vtt` 지원)
            language: 언어 코드(미지정 시 `en`).
        name: 캡션 트랙 이름(미지정 시 파일명 사용)
        """
        if not video_id:
            raise YouTubeUploadError("video_id is required")

        caption_file = Path(caption_path)
        if not caption_file.exists():
            raise YouTubeUploadError(f"Caption file not found: {caption_path}")

        extension = caption_file.suffix.lower()
        if extension not in {".srt", ".vtt"}:
            raise YouTubeUploadError(
                f"Unsupported caption format: {extension} (supported: .srt, .vtt)"
            )

        caption_name = name or caption_file.stem
        caption_language = language.lower() if language else "en"

        mimetype = "application/octet-stream"
        media = MediaFileUpload(
            str(caption_file),
            mimetype=mimetype,
            resumable=False,
        )

        body: dict[str, Any] = {
            "snippet": {
                "videoId": video_id,
                "language": caption_language,
                "name": caption_name,
            }
        }
        try:
            request = self.service.captions().insert(
                part="snippet",
                body=body,
                media_body=media,
            )
            request.execute()
        except HttpError as e:
            raise YouTubeUploadError(
                f"Failed to set captions for video {video_id}: {e.resp.status} {e.resp.reason}"
            ) from e

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

        # 시작 시 0% 표시
        if on_progress:
            on_progress(0)

        while response is None:
            try:
                status, response = request.next_chunk()

                if status is not None:
                    progress_percent = int(status.progress() * 100)
                    logger.debug(f"Upload progress: {progress_percent}%")
                    if on_progress:
                        on_progress(progress_percent)
                elif response is None:
                    # 청크 전송 중이지만 status가 None인 경우
                    logger.debug("Uploading chunk...")

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
