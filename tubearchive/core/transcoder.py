"""영상 트랜스코딩 엔진.

입력 영상을 HEVC 10-bit(p010le) BT.709 SDR 프로파일로 변환한다.
VideoToolbox 하드웨어 가속을 우선 사용하고, 실패 시 libx265로 폴백한다.

주요 기능:
    - 세로 영상 → 가로(3840x2160) 레이아웃 자동 변환 (블러 배경)
    - HDR(HLG/PQ) → SDR 톤매핑
    - 오디오 노이즈 제거 (afftdn)
    - EBU R128 라우드니스 정규화 (loudnorm 2-pass)
    - 클립 간 Dip-to-Black 페이드
    - Resume 지원 (중단 후 재시작)
"""

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
from tubearchive.ffmpeg.effects import (
    LoudnormAnalysis,
    SilenceSegment,
    StabilizeCrop,
    StabilizeStrength,
    create_combined_filter,
    create_loudnorm_analysis_filter,
    create_vidstab_detect_filter,
    create_vidstab_transform_filter,
    parse_loudnorm_stats,
)
from tubearchive.ffmpeg.executor import FFmpegError, FFmpegExecutor
from tubearchive.ffmpeg.profiles import PROFILE_SDR, EncodingProfile, get_fallback_profile
from tubearchive.models.job import JobStatus
from tubearchive.models.video import VideoFile
from tubearchive.utils.progress import ProgressInfo

logger = logging.getLogger(__name__)


def _resolve_auto_lut(device_model: str, device_luts: dict[str, str]) -> str | None:
    """기기 모델명 기반으로 LUT 파일 경로를 자동 매칭한다.

    부분 문자열 매칭(대소문자 무시)을 사용하며,
    다중 매칭 시 가장 긴 키워드(가장 구체적)를 우선한다.

    Note:
        짧은 키워드(예: "z")는 의도치 않은 매칭을 유발할 수 있으므로
        config.toml에 충분히 구체적인 키워드를 사용하는 것을 권장한다.
        ``~`` 경로도 지원한다 (expanduser 자동 적용).

    Args:
        device_model: FFprobe에서 감지된 기기 모델명
        device_luts: 기기 키워드 → LUT 파일 경로 매핑

    Returns:
        매칭된 LUT 파일의 확장(절대) 경로 또는 None
    """
    if not device_model or not device_luts:
        return None

    model_lower = device_model.lower()
    matches: list[tuple[int, str]] = []

    for keyword, lut_path in device_luts.items():
        if not keyword:  # 빈 키워드는 모든 기기에 매칭되므로 건너뜀
            continue
        if keyword.lower() in model_lower:
            matches.append((len(keyword), lut_path))

    if not matches:
        return None

    # 가장 긴 키워드(가장 구체적) 우선
    matches.sort(key=lambda x: x[0], reverse=True)
    best_path = matches[0][1]

    # config.toml에서 ~/LUTs/nikon.cube 같은 경로가 올 수 있으므로 확장
    resolved = Path(best_path).expanduser()
    if not resolved.is_file():
        logger.warning("Auto-LUT file not found: %s", best_path)
        return None

    return str(resolved)


def _is_vidstab_fileformat_unsupported(error: FFmpegError) -> bool:
    """vidstabdetect의 fileformat 옵션 미지원 오류인지 판별한다."""
    err_text = f"{error}\n{error.stderr or ''}".lower()
    return "fileformat" in err_text and "option not found" in err_text


class Transcoder:
    """영상 파일을 HEVC 10-bit SDR로 트랜스코딩하는 엔진.

    컨텍스트 매니저로 사용하며, DB 연결·Resume 매니저·임시 디렉토리를 관리한다.

    Usage::

        with Transcoder(temp_dir=Path("/tmp/ta")) as t:
            output, vid = t.transcode_video(video_file)
    """

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
        """세로/가로 영상에 맞는 FFmpeg 커맨드를 생성한다.

        세로 영상(portrait)은 ``filter_complex`` 인자로 전달하여
        split→blur→overlay 파이프라인을 구성하고,
        가로 영상은 ``video_filter`` (-vf) 인자를 사용한다.

        Args:
            video_file: 입력 영상 파일.
            metadata: ffprobe에서 추출한 영상 메타데이터.
            output_path: 트랜스코딩 결과 저장 경로.
            profile: 인코딩 프로파일 (코덱, 비트레이트, 색 공간).
            video_filter: FFmpeg 비디오 필터 문자열.
            audio_filter: FFmpeg 오디오 필터 문자열.
            seek_start: Resume 시작 위치 (초). None이면 처음부터.

        Returns:
            FFmpeg 명령어 인자 리스트.
        """
        # 세로: filter_complex (split → blur → overlay), 가로: -vf
        if metadata.is_portrait:
            return self.executor.build_transcode_command(
                input_path=video_file.path,
                output_path=output_path,
                profile=profile,
                filter_complex=video_filter,
                audio_filter=audio_filter,
                seek_start=seek_start,
                has_audio=metadata.has_audio,
            )
        return self.executor.build_transcode_command(
            input_path=video_file.path,
            output_path=output_path,
            profile=profile,
            video_filter=video_filter,
            audio_filter=audio_filter,
            seek_start=seek_start,
            has_audio=metadata.has_audio,
        )

    def _run_transcode(
        self,
        cmd: list[str],
        duration: float,
        job_id: int,
        progress_info_callback: Callable[[ProgressInfo], None] | None,
    ) -> None:
        """FFmpeg를 실행하고 진행률을 DB와 UI에 동시 보고한다.

        ``progress_info_callback`` 이 지정되면 DB 저장과 UI 콜백을
        모두 호출하는 래퍼를 구성하고, 없으면 DB 저장만 수행한다.

        Args:
            cmd: FFmpeg 명령어 인자 리스트.
            duration: 영상 총 길이 (초). 진행률 퍼센트 계산에 사용.
            job_id: ``transcoding_jobs`` 테이블의 작업 ID (진행률 저장용).
            progress_info_callback: UI 진행률 업데이트 콜백 (선택).
        """
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

    def _run_loudnorm_analysis(self, video_file: VideoFile) -> LoudnormAnalysis:
        """EBU R128 loudnorm 1st pass — 오디오 라우드니스 분석.

        FFmpeg의 ``loudnorm`` 필터를 분석 모드(``print_format=json``)로
        실행하여 입력 영상의 라우드니스 통계(I, TP, LRA, threshold)를 측정한다.
        이 결과는 2nd pass에서 정규화 파라미터로 사용된다.

        Args:
            video_file: 분석 대상 영상 파일.

        Returns:
            1st pass 분석 결과 (:class:`LoudnormAnalysis`).
        """
        analysis_filter = create_loudnorm_analysis_filter()
        cmd = self.executor.build_loudness_analysis_command(
            input_path=video_file.path,
            audio_filter=analysis_filter,
        )
        logger.info("Running loudnorm analysis pass")
        stderr = self.executor.run_analysis(cmd)
        return parse_loudnorm_stats(stderr)

    def _run_silence_analysis(
        self,
        video_file: VideoFile,
        threshold: str = "-30dB",
        min_duration: float = 2.0,
    ) -> list[SilenceSegment]:
        """무음 구간 분석 (1st pass).

        FFmpeg의 ``silencedetect`` 필터를 사용하여 오디오 트랙의 무음 구간을 감지한다.

        Args:
            video_file: 분석 대상 영상 파일.
            threshold: 무음 기준 데시벨 (예: "-30dB")
            min_duration: 최소 무음 길이 (초)

        Returns:
            무음 구간 리스트 (:class:`SilenceSegment`).
        """
        from tubearchive.ffmpeg.effects import (
            create_silence_detect_filter,
            parse_silence_segments,
        )

        detect_filter = create_silence_detect_filter(threshold, min_duration)
        cmd = self.executor.build_silence_detection_command(
            input_path=video_file.path,
            audio_filter=detect_filter,
        )
        logger.info("Running silence detection pass")
        stderr = self.executor.run_analysis(cmd)
        return parse_silence_segments(stderr)

    def _run_vidstab_analysis(
        self,
        video_file: VideoFile,
        strength: StabilizeStrength,
        trf_path: Path,
    ) -> None:
        """vidstab 1st pass — 흔들림 감지 분석.

        FFmpeg의 ``vidstabdetect`` 필터를 실행하여 입력 영상의
        흔들림 데이터를 ``.trf`` 파일에 기록한다.
        이 결과는 2nd pass(``vidstabtransform``)에서 사용된다.

        Args:
            video_file: 분석 대상 영상 파일.
            strength: 안정화 강도.
            trf_path: transform 데이터 저장 경로.
        """
        detect_filter = create_vidstab_detect_filter(
            strength=strength,
            trf_path=str(trf_path),
            include_fileformat=True,
        )
        cmd = self.executor.build_vidstab_detect_command(
            input_path=video_file.path,
            video_filter=detect_filter,
        )
        logger.info("Running vidstab detection pass (strength=%s)", strength.value)
        try:
            self.executor.run_analysis(cmd)
        except FFmpegError as e:
            if not _is_vidstab_fileformat_unsupported(e):
                raise

            # FFmpeg/vidstab 버전에 따라 fileformat 옵션이 없을 수 있으므로
            # 해당 경우에 한해 옵션을 제거하고 1회 재시도한다.
            logger.info("vidstabdetect fileformat option unsupported, retrying without fileformat")
            retry_filter = create_vidstab_detect_filter(
                strength=strength,
                trf_path=str(trf_path),
                include_fileformat=False,
            )
            retry_cmd = self.executor.build_vidstab_detect_command(
                input_path=video_file.path,
                video_filter=retry_filter,
            )
            self.executor.run_analysis(retry_cmd)

    def transcode_video(
        self,
        video_file: VideoFile,
        target_width: int = 3840,
        target_height: int = 2160,
        fade_duration: float = 0.5,
        fade_in_duration: float | None = None,
        fade_out_duration: float | None = None,
        denoise: bool = False,
        denoise_level: str = "medium",
        normalize_audio: bool = False,
        trim_silence: bool = False,
        silence_threshold: str = "-30dB",
        silence_min_duration: float = 2.0,
        stabilize: bool = False,
        stabilize_strength: str = "medium",
        stabilize_crop: str = "crop",
        lut_path: str | None = None,
        auto_lut: bool = False,
        lut_before_hdr: bool = False,
        device_luts: dict[str, str] | None = None,
        watermark_text: str | None = None,
        watermark_position: str = "bottom-right",
        watermark_size: int = 48,
        watermark_color: str = "white",
        watermark_alpha: float = 1.0,
        progress_info_callback: Callable[[ProgressInfo], None] | None = None,
    ) -> tuple[Path, int, list[SilenceSegment] | None]:
        """
        단일 영상 트랜스코딩.

        Args:
            video_file: 입력 영상 파일
            target_width: 타겟 너비
            target_height: 타겟 높이
            fade_duration: 페이드 기본 지속 시간
            fade_in_duration: Fade In 지속 시간 (None이면 fade_duration 사용)
            fade_out_duration: Fade Out 지속 시간 (None이면 fade_duration 사용)
            denoise: 오디오 노이즈 제거 활성화 여부
            denoise_level: 노이즈 제거 강도 (light/medium/heavy)
            normalize_audio: EBU R128 오디오 정규화 활성화 여부
            trim_silence: 무음 구간 제거 활성화 여부
            silence_threshold: 무음 기준 데시벨 (예: "-30dB")
            silence_min_duration: 최소 무음 길이 (초)
            stabilize: 영상 안정화(vidstab) 활성화 여부
            stabilize_strength: 안정화 강도 (light/medium/heavy)
            stabilize_crop: 안정화 후 크롭 모드 (crop/expand)
            lut_path: LUT 파일 경로 (직접 지정, auto_lut보다 우선)
            auto_lut: 기기 모델 기반 자동 LUT 매칭 활성화
            lut_before_hdr: LUT 필터를 HDR→SDR 변환 전에 적용
            device_luts: 기기 키워드 → LUT 파일 경로 매핑
            watermark_text: 워터마크 텍스트
            watermark_position: 워터마크 위치
            watermark_size: 워터마크 글자 크기
            watermark_color: 워터마크 글자 색
            watermark_alpha: 워터마크 투명도
            progress_info_callback: 상세 진행률 콜백 (UI 업데이트용)

        Returns:
            (트랜스코딩된 파일 경로, video_id, 무음 구간 리스트) 튜플

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
            return existing, video_id, None

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

        # 5. loudnorm 분석 (활성화된 경우, 트랜스코딩 전에 실행)
        # 오디오 스트림이 없는 영상에서는 오디오 분석을 스킵한다
        loudnorm_analysis: LoudnormAnalysis | None = None
        if normalize_audio and metadata.has_audio:
            try:
                loudnorm_analysis = self._run_loudnorm_analysis(video_file)
                logger.info(
                    f"Loudnorm: I={loudnorm_analysis.input_i:.1f}dB "
                    f"TP={loudnorm_analysis.input_tp:.1f}dB "
                    f"LRA={loudnorm_analysis.input_lra:.1f}"
                )
            except (FFmpegError, ValueError) as e:
                logger.warning(f"Loudnorm analysis failed, skipping normalization: {e}")
        elif normalize_audio and not metadata.has_audio:
            logger.info("No audio stream, skipping loudnorm analysis")

        # 5.5 무음 구간 분석 (활성화된 경우)
        silence_segments: list[SilenceSegment] | None = None
        silence_remove_filter = ""
        if trim_silence and not metadata.has_audio:
            logger.info("No audio stream, skipping silence analysis")
        elif trim_silence:
            try:
                silence_segments = self._run_silence_analysis(
                    video_file,
                    threshold=silence_threshold,
                    min_duration=silence_min_duration,
                )
                if silence_segments:
                    from tubearchive.ffmpeg.effects import create_silence_remove_filter

                    silence_remove_filter = create_silence_remove_filter(
                        threshold=silence_threshold,
                        min_duration=silence_min_duration,
                        trim_start=True,
                        trim_end=True,
                    )
                    logger.info(f"Found {len(silence_segments)} silence segments, will trim")
            except (FFmpegError, ValueError) as e:
                logger.warning(f"Silence analysis failed, skipping trim: {e}")

        # 5.7 vidstab 영상 안정화 (2-pass)
        # 1st pass: vidstabdetect로 손떨림 분석 → trf 파일에 모션 벡터 기록
        # 2nd pass: vidstabtransform 필터가 trf를 읽어 트랜스코딩 중 보정 적용
        # stabilize_filter=""이면 안정화 비활성 (create_combined_filter에서 무시)
        stabilize_filter = ""
        trf_path: Path | None = None
        if stabilize:
            strength_enum = StabilizeStrength(stabilize_strength)
            crop_enum = StabilizeCrop(stabilize_crop)
            trf_path = self.temp_dir / f"vidstab_{video_id}.trf"
            try:
                self._run_vidstab_analysis(video_file, strength_enum, trf_path)
                stabilize_filter = create_vidstab_transform_filter(
                    strength=strength_enum,
                    crop=crop_enum,
                    trf_path=str(trf_path),
                )
                logger.info(
                    "Vidstab: strength=%s, crop=%s",
                    stabilize_strength,
                    stabilize_crop,
                )
            except (FFmpegError, ValueError) as e:
                logger.warning(f"Vidstab analysis failed, skipping stabilization: {e}")
                stabilize_filter = ""

        # 5.8 LUT 해석 (lut_path 직접 지정 > auto_lut 매칭)
        resolved_lut: str | None = lut_path
        if resolved_lut is None and auto_lut and device_luts and metadata.device_model:
            resolved_lut = _resolve_auto_lut(metadata.device_model, device_luts)
            if resolved_lut:
                logger.info(f"Auto-LUT matched: {resolved_lut} for {metadata.device_model}")

        # 6. 프로파일 및 필터 준비 (항상 SDR, HDR은 필터에서 변환)
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
            fade_in_duration=fade_in_duration,
            fade_out_duration=fade_out_duration,
            color_transfer=metadata.color_transfer,
            stabilize_filter=stabilize_filter,
            denoise=denoise,
            denoise_level=denoise_level,
            silence_remove=silence_remove_filter,
            loudnorm_analysis=loudnorm_analysis,
            lut_path=resolved_lut,
            lut_before_hdr=lut_before_hdr,
            watermark_text=watermark_text,
            watermark_position=watermark_position,
            watermark_size=watermark_size,
            watermark_color=watermark_color,
            watermark_alpha=watermark_alpha,
        )

        # 7. 실행: VideoToolbox → (실패 시) libx265 폴백
        # Note: vidstab trf 파일은 vidstabtransform 필터가 트랜스코딩 중 참조하므로
        # 트랜스코딩 완료(성공/실패 모두) 후 finally에서 정리한다.
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
            return output_path, video_id, silence_segments

        except FFmpegError as e:
            # 하드웨어 인코더(VideoToolbox) 사용 중이 아니면 폴백 불가
            if "videotoolbox" not in profile.video_codec.lower():
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
                return output_path, video_id, silence_segments

            except FFmpegError as fallback_error:
                self.job_repo.mark_failed(job_id, str(fallback_error))
                raise

        finally:
            # vidstab trf 임시 파일 정리 (트랜스코딩 성공/실패 후)
            if trf_path and trf_path.exists():
                trf_path.unlink()
                logger.debug("Cleaned up vidstab trf: %s", trf_path)

    def close(self) -> None:
        """리소스 정리."""
        self.conn.close()

    def __enter__(self) -> Transcoder:
        """Context manager 진입."""
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager 종료."""
        self.close()
