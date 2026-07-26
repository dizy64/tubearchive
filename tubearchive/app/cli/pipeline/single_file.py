"""단일 파일 직접 업로드 처리."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from tubearchive.app.cli.pipeline.io_utils import get_output_filename
from tubearchive.app.cli.validators import ValidatedArgs
from tubearchive.domain.media.detector import (
    detect_metadata,
)
from tubearchive.domain.models.clip import ClipInfo
from tubearchive.domain.models.video import VideoFile
from tubearchive.infra.db.repository import (
    MergeJobRepository,
)
from tubearchive.shared.summary_generator import generate_single_file_description

logger = logging.getLogger(__name__)


def handle_single_file_upload(
    video_file: VideoFile,
    args: ValidatedArgs,
) -> Path:
    """
    단일 파일 직접 업로드 처리.

    인코딩/병합 없이 DB 저장 후 원본 파일 경로 반환.

    Args:
        video_file: VideoFile 객체
        args: 검증된 CLI 인자

    Returns:
        원본 파일 경로
    """
    logger.info(f"Single file detected with --upload, skipping transcode: {video_file.path.name}")

    # 1. 메타데이터 수집
    metadata = detect_metadata(video_file.path)

    # 2. YouTube 제목 생성 (디렉토리명 기반)
    title = get_output_filename([video_file.path]).replace(".mp4", "")

    # 3. 촬영 시간 추출
    creation_time_str = video_file.creation_time.strftime("%H:%M:%S")

    # 4. 클립 정보 생성
    clip = ClipInfo(
        name=video_file.path.name,
        duration=metadata.duration_seconds,
        device=metadata.device_model or "Unknown",
        shot_time=creation_time_str,
    )

    # 5. YouTube 설명 생성 (단일 파일용)
    youtube_description = generate_single_file_description(
        device=clip.device, shot_time=clip.shot_time
    )

    # 6. DB 저장 (타임라인 dict: start/end 포함)
    clip_dict: dict[str, str | float | None] = {
        "name": clip.name,
        "duration": clip.duration,
        "start": 0.0,
        "end": clip.duration,
        "device": clip.device,
        "shot_time": clip.shot_time,
    }
    from tubearchive.app.cli.main import database_session  # lazy: avoids circular import

    with database_session() as conn:
        repo = MergeJobRepository(conn)
        today = date.today().isoformat()

        repo.create(
            output_path=video_file.path,
            video_ids=[],  # 트랜스코딩 안 함
            title=title,
            date=today,
            total_duration_seconds=metadata.duration_seconds,
            total_size_bytes=video_file.path.stat().st_size,
            clips_info_json=json.dumps([clip_dict]),
            summary_markdown=youtube_description,
        )

    # 7. 콘솔 출력
    logger.info(f"Saved to DB: {title}")
    print("\n📁 단일 파일 업로드 모드 (트랜스코딩 생략)")
    print(f"📹 파일: {video_file.path.name}")
    minutes = int(metadata.duration_seconds // 60)
    seconds = int(metadata.duration_seconds % 60)
    print(f"⏱️  길이: {minutes}분 {seconds}초")
    if metadata.device_model:
        print(f"📷 기기: {metadata.device_model}")

    return video_file.path
