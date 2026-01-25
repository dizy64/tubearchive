"""트랜스코딩 엔진."""

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tubearchive.models.video import VideoMetadata

from tubearchive.core.detector import detect_metadata
from tubearchive.database.repository import TranscodingJobRepository, VideoRepository
from tubearchive.database.resume import ResumeManager
from tubearchive.database.schema import init_database
from tubearchive.ffmpeg.effects import create_combined_filter
from tubearchive.ffmpeg.executor import FFmpegError, FFmpegExecutor
from tubearchive.ffmpeg.profiles import PROFILE_SDR, get_fallback_profile
from tubearchive.models.job import JobStatus
from tubearchive.models.video import VideoFile

logger = logging.getLogger(__name__)


class Transcoder:
    """트랜스코딩 엔진."""

    def __init__(
        self,
        db_path: Path | None = None,
        temp_dir: Path | None = None,
    ) -> None:
        """
        초기화.

        Args:
            db_path: 데이터베이스 경로 (None이면 기본값)
            temp_dir: 임시 파일 디렉토리 (필수, None이면 에러)

        Raises:
            ValueError: temp_dir이 None인 경우
        """
        if temp_dir is None:
            raise ValueError("temp_dir is required")

        self.conn = init_database(db_path)
        self.video_repo = VideoRepository(self.conn)
        self.job_repo = TranscodingJobRepository(self.conn)
        self.resume_mgr = ResumeManager(self.conn)
        self.executor = FFmpegExecutor()
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(exist_ok=True)

    def transcode_video(
        self,
        video_file: VideoFile,
        target_width: int = 3840,
        target_height: int = 2160,
        fade_duration: float = 0.5,
    ) -> Path:
        """
        단일 영상 트랜스코딩.

        Args:
            video_file: 입력 영상 파일
            target_width: 타겟 너비
            target_height: 타겟 높이
            fade_duration: 페이드 지속 시간

        Returns:
            트랜스코딩된 파일 경로

        Raises:
            FFmpegError: 트랜스코딩 실패
        """
        # 메타데이터 감지
        metadata = detect_metadata(video_file.path)
        logger.info(f"Detected: {metadata.device_model}, {metadata.width}x{metadata.height}")

        # 데이터베이스에 영상 등록
        existing = self.video_repo.get_by_path(video_file.path)
        if existing:
            video_id = existing["id"]
        else:
            video_id = self.video_repo.insert(video_file, metadata)

        # 이미 처리된 영상인지 확인
        if self.resume_mgr.is_video_processed(video_id):
            jobs = self.job_repo.get_by_video_id(video_id)
            completed_job = next(
                (j for j in jobs if j.status == JobStatus.COMPLETED and j.temp_file_path),
                None,
            )
            if completed_job and completed_job.temp_file_path:
                logger.info(f"Video already processed: {video_file.path}")
                return completed_job.temp_file_path

        # 작업 생성 또는 기존 작업 조회
        job_id = self.resume_mgr.get_or_create_job(video_id)
        job = self.job_repo.get_by_id(job_id)

        if job is None:
            raise RuntimeError(f"Failed to create job for video {video_id}")

        # 출력 파일 경로
        output_path = self.temp_dir / f"transcoded_{video_id}.mp4"

        # Resume 시작 위치 계산
        seek_start: float | None = None
        if job.status == JobStatus.PROCESSING and job.progress_percent > 0:
            seek_start = self.resume_mgr.calculate_resume_position(job, metadata.duration_seconds)
            logger.info(f"Resuming from {seek_start:.2f}s ({job.progress_percent}%)")

        # 작업 시작
        self.job_repo.update_status(job_id, JobStatus.PROCESSING)
        self.resume_mgr.set_temp_file(job_id, output_path)

        # 프로파일: 항상 SDR로 통일 (concat 호환성)
        # HDR 소스는 필터에서 SDR로 변환됨
        profile = PROFILE_SDR
        logger.info(f"Using profile: {profile.name}")

        # 필터 생성 (HDR 소스는 SDR로 변환)
        video_filter, audio_filter = create_combined_filter(
            source_width=metadata.width,
            source_height=metadata.height,
            total_duration=metadata.duration_seconds,
            is_portrait=metadata.is_portrait,
            target_width=target_width,
            target_height=target_height,
            fade_duration=fade_duration,
            color_transfer=metadata.color_transfer,
        )

        # 진행률 콜백
        def on_progress(percent: int) -> None:
            self.resume_mgr.save_progress(job_id, percent)

        # 트랜스코딩 실행
        try:
            if metadata.is_portrait:
                # 세로 영상: filter_complex 사용
                cmd = self.executor.build_transcode_command(
                    input_path=video_file.path,
                    output_path=output_path,
                    profile=profile,
                    filter_complex=video_filter,
                    audio_filter=audio_filter,
                    seek_start=seek_start,
                )
            else:
                # 가로 영상: -vf 사용
                cmd = self.executor.build_transcode_command(
                    input_path=video_file.path,
                    output_path=output_path,
                    profile=profile,
                    video_filter=video_filter,
                    audio_filter=audio_filter,
                    seek_start=seek_start,
                )

            self.executor.run(cmd, metadata.duration_seconds, on_progress)
            self.job_repo.mark_completed(job_id, output_path)
            logger.info(f"Transcoding completed: {output_path}")

            return output_path

        except FFmpegError as e:
            # VideoToolbox 실패 시 폴백 시도
            if "videotoolbox" in str(e.stderr or "").lower():
                logger.warning("VideoToolbox failed, trying libx265 fallback")
                return self._transcode_with_fallback(
                    video_file,
                    metadata,
                    job_id,
                    output_path,
                    video_filter,
                    audio_filter,
                    seek_start,
                    on_progress,
                )
            else:
                self.job_repo.mark_failed(job_id, str(e))
                raise

    def _transcode_with_fallback(
        self,
        video_file: VideoFile,
        metadata: VideoMetadata,
        job_id: int,
        output_path: Path,
        video_filter: str,
        audio_filter: str,
        seek_start: float | None,
        on_progress: Callable[[int], None],
    ) -> Path:
        """libx265 폴백 트랜스코딩."""

        fallback_profile = get_fallback_profile()
        logger.info(f"Using fallback profile: {fallback_profile.name}")

        try:
            if metadata.is_portrait:
                cmd = self.executor.build_transcode_command(
                    input_path=video_file.path,
                    output_path=output_path,
                    profile=fallback_profile,
                    filter_complex=video_filter,
                    audio_filter=audio_filter,
                    seek_start=seek_start,
                )
            else:
                cmd = self.executor.build_transcode_command(
                    input_path=video_file.path,
                    output_path=output_path,
                    profile=fallback_profile,
                    video_filter=video_filter,
                    audio_filter=audio_filter,
                    seek_start=seek_start,
                )

            self.executor.run(cmd, metadata.duration_seconds, on_progress)
            self.job_repo.mark_completed(job_id, output_path)
            logger.info(f"Fallback transcoding completed: {output_path}")

            return output_path

        except FFmpegError as e:
            self.job_repo.mark_failed(job_id, str(e))
            raise

    def close(self) -> None:
        """리소스 정리."""
        self.conn.close()

    def __enter__(self) -> Transcoder:
        """Context manager 진입."""
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager 종료."""
        self.close()
