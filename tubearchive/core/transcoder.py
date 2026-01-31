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
from tubearchive.ffmpeg.profiles import PROFILE_SDR, EncodingProfile, get_fallback_profile
from tubearchive.models.job import JobStatus
from tubearchive.models.video import VideoFile
from tubearchive.utils.progress import ProgressInfo

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

    # ---------- 내부 헬퍼 ----------

    def _register_video(self, video_file: VideoFile, metadata: VideoMetadata) -> int:
        """영상을 DB에 등록하고 video_id를 반환한다 (이미 존재하면 기존 ID)."""
        existing = self.video_repo.get_by_path(video_file.path)
        if existing:
            return int(existing["id"])
        return self.video_repo.insert(video_file, metadata)

    def _find_existing_result(self, video_id: int, path: Path) -> Path | None:
        """이미 처리 완료된 결과 파일을 찾는다. 없으면 None."""
        if not self.resume_mgr.is_video_processed(video_id):
            return None

        jobs = self.job_repo.get_by_video_id(video_id)
        completed_job = next(
            (j for j in jobs if j.status == JobStatus.COMPLETED and j.temp_file_path),
            None,
        )

        # 파일이 실제 존재하면 스킵
        if completed_job and completed_job.temp_file_path and completed_job.temp_file_path.exists():
            logger.info(f"Video already processed: {path}")
            return completed_job.temp_file_path

        # DB에만 완료 기록이 남아있고 파일이 없으면 → merged로 전이
        if completed_job and completed_job.id is not None:
            logger.info(f"Completed but temp file gone, marking as merged: {path}")
            self.job_repo.update_status(completed_job.id, JobStatus.MERGED)

        return None

    def _build_transcode_cmd(
        self,
        video_file: VideoFile,
        metadata: VideoMetadata,
        output_path: Path,
        profile: EncodingProfile,
        video_filter: str,
        audio_filter: str,
        seek_start: float | None,
    ) -> list[str]:
        """세로/가로 영상에 맞는 FFmpeg 커맨드를 생성한다."""
        # 세로: filter_complex (split → blur → overlay), 가로: -vf
        if metadata.is_portrait:
            return self.executor.build_transcode_command(
                input_path=video_file.path,
                output_path=output_path,
                profile=profile,
                filter_complex=video_filter,
                audio_filter=audio_filter,
                seek_start=seek_start,
            )
        return self.executor.build_transcode_command(
            input_path=video_file.path,
            output_path=output_path,
            profile=profile,
            video_filter=video_filter,
            audio_filter=audio_filter,
            seek_start=seek_start,
        )

    def _run_transcode(
        self,
        cmd: list[str],
        duration: float,
        job_id: int,
        progress_info_callback: Callable[[ProgressInfo], None] | None,
    ) -> None:
        """FFmpeg를 실행하고 진행률을 DB(+UI)에 보고한다."""
        if progress_info_callback:

            def on_progress_info(info: ProgressInfo) -> None:
                self.resume_mgr.save_progress(job_id, info.percent)
                progress_info_callback(info)

            self.executor.run(cmd, duration, progress_info_callback=on_progress_info)
        else:
            self.executor.run(
                cmd,
                duration,
                lambda percent: self.resume_mgr.save_progress(job_id, percent),
            )

    # ---------- 공개 API ----------

    def transcode_video(
        self,
        video_file: VideoFile,
        target_width: int = 3840,
        target_height: int = 2160,
        fade_duration: float = 0.5,
        denoise: bool = False,
        denoise_level: str = "medium",
        progress_info_callback: Callable[[ProgressInfo], None] | None = None,
    ) -> tuple[Path, int]:
        """
        단일 영상 트랜스코딩.

        Args:
            video_file: 입력 영상 파일
            target_width: 타겟 너비
            target_height: 타겟 높이
            fade_duration: 페이드 지속 시간
            denoise: 오디오 노이즈 제거 활성화 여부
            denoise_level: 노이즈 제거 강도 (light/medium/heavy)
            progress_info_callback: 상세 진행률 콜백 (UI 업데이트용)

        Returns:
            (트랜스코딩된 파일 경로, video_id) 튜플

        Raises:
            FFmpegError: 트랜스코딩 실패
        """
        # 1. 메타데이터 감지 및 DB 등록
        metadata = detect_metadata(video_file.path)
        logger.info(f"Detected: {metadata.device_model}, {metadata.width}x{metadata.height}")
        video_id = self._register_video(video_file, metadata)

        # 2. 이미 처리된 결과가 있으면 스킵
        existing = self._find_existing_result(video_id, video_file.path)
        if existing:
            return existing, video_id

        # 3. 작업 생성/조회 및 Resume 위치 계산
        job_id = self.resume_mgr.get_or_create_job(video_id)
        job = self.job_repo.get_by_id(job_id)
        if job is None:
            raise RuntimeError(f"Failed to create job for video {video_id}")

        output_path = self.temp_dir / f"transcoded_{video_id}.mp4"

        seek_start: float | None = None
        if job.status == JobStatus.PROCESSING and job.progress_percent > 0:
            seek_start = self.resume_mgr.calculate_resume_position(job, metadata.duration_seconds)
            logger.info(f"Resuming from {seek_start:.2f}s ({job.progress_percent}%)")

        # 4. 작업 시작
        self.job_repo.update_status(job_id, JobStatus.PROCESSING)
        self.resume_mgr.set_temp_file(job_id, output_path)

        # 5. 프로파일 및 필터 준비 (항상 SDR, HDR은 필터에서 변환)
        profile = PROFILE_SDR
        logger.info(f"Using profile: {profile.name}")

        video_filter, audio_filter = create_combined_filter(
            source_width=metadata.width,
            source_height=metadata.height,
            total_duration=metadata.duration_seconds,
            is_portrait=metadata.is_portrait,
            target_width=target_width,
            target_height=target_height,
            fade_duration=fade_duration,
            color_transfer=metadata.color_transfer,
            denoise=denoise,
            denoise_level=denoise_level,
        )

        # 6. 실행: VideoToolbox → (실패 시) libx265 폴백
        try:
            cmd = self._build_transcode_cmd(
                video_file,
                metadata,
                output_path,
                profile,
                video_filter,
                audio_filter,
                seek_start,
            )
            self._run_transcode(cmd, metadata.duration_seconds, job_id, progress_info_callback)
            self.job_repo.mark_completed(job_id, output_path)
            logger.info(f"Transcoding completed: {output_path}")
            return output_path, video_id

        except FFmpegError as e:
            if "videotoolbox" not in str(e.stderr or "").lower():
                self.job_repo.mark_failed(job_id, str(e))
                raise

            # VideoToolbox 실패 → libx265 폴백
            logger.warning("VideoToolbox failed, trying libx265 fallback")
            fallback = get_fallback_profile()
            logger.info(f"Using fallback profile: {fallback.name}")

            try:
                cmd = self._build_transcode_cmd(
                    video_file,
                    metadata,
                    output_path,
                    fallback,
                    video_filter,
                    audio_filter,
                    seek_start,
                )
                self._run_transcode(cmd, metadata.duration_seconds, job_id, progress_info_callback)
                self.job_repo.mark_completed(job_id, output_path)
                logger.info(f"Fallback transcoding completed: {output_path}")
                return output_path, video_id

            except FFmpegError as fallback_error:
                self.job_repo.mark_failed(job_id, str(fallback_error))
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
