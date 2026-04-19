"""FFmpeg concat demuxer를 사용한 영상 병합기.

트랜스코딩된 영상 파일들을 하나의 MP4 파일로 합치되,
재인코딩 없이(``-c copy``) 스트림을 이어붙인다.
"""

import contextlib
import logging
import shutil
import subprocess
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# 병합 후 오디오/비디오 길이 차이 허용 임계값 (초)
AUDIO_DURATION_TOLERANCE_SECONDS = 5.0


def create_concat_file(video_paths: list[Path], output_dir: Path) -> Path:
    """
    FFmpeg concat 형식의 텍스트 파일 생성.

    Args:
        video_paths: 병합할 영상 파일 경로 목록
        output_dir: concat 파일을 저장할 디렉토리

    Returns:
        생성된 concat 파일 경로

    Raises:
        ValueError: video_paths가 비어있는 경우
    """
    if not video_paths:
        raise ValueError("No video files provided")

    concat_file = output_dir / f"concat_{uuid.uuid4().hex[:8]}.txt"

    lines = [f"file '{path}'" for path in video_paths]
    concat_file.write_text("\n".join(lines))

    logger.debug(f"Created concat file: {concat_file}")
    return concat_file


def probe_audio_sample_rate(path: Path, ffprobe_path: str = "ffprobe") -> int | None:
    """ffprobe로 파일의 첫 번째 오디오 스트림 샘플레이트를 조회한다.

    컨테이너 헤더만 읽으므로 파일 크기와 무관하게 즉각 반환된다.

    Args:
        path: 조회할 파일 경로
        ffprobe_path: ffprobe 실행 파일 경로

    Returns:
        샘플레이트(Hz), 오디오 스트림 없거나 조회 실패 시 None
    """
    result = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "quiet",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def probe_stream_durations(path: Path, ffprobe_path: str = "ffprobe") -> dict[str, float]:
    """ffprobe로 파일의 스트림별 길이를 조회한다.

    컨테이너 헤더만 읽으므로 파일 크기와 무관하게 즉각 반환된다.

    Args:
        path: 조회할 파일 경로
        ffprobe_path: ffprobe 실행 파일 경로

    Returns:
        {'video': 초, 'audio': 초} 형태의 딕셔너리. 스트림이 없으면 해당 키 누락.
    """
    result = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "quiet",
            "-show_entries",
            "stream=codec_type,duration",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    durations: dict[str, float] = {}
    if result.returncode != 0:
        return durations
    for line in result.stdout.strip().splitlines():
        parts = line.split(",")
        if len(parts) == 2:
            codec_type, duration_str = parts
            with contextlib.suppress(ValueError):
                durations[codec_type] = float(duration_str)
    return durations


class Merger:
    """FFmpeg concat demuxer 기반 영상 병합기 (스트림 복사, 재인코딩 없음)."""

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        temp_dir: Path | None = None,
    ) -> None:
        """
        초기화.

        Args:
            ffmpeg_path: FFmpeg 실행 파일 경로
            ffprobe_path: ffprobe 실행 파일 경로
            temp_dir: 임시 파일 디렉토리 (필수, None이면 에러)

        Raises:
            ValueError: temp_dir이 None인 경우
        """
        if temp_dir is None:
            raise ValueError("temp_dir is required")

        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.temp_dir = temp_dir
        self.temp_dir.mkdir(exist_ok=True)

    def build_merge_command(
        self,
        concat_file: Path,
        output_path: Path,
        overwrite: bool = True,
    ) -> list[str]:
        """
        병합 FFmpeg 명령어 빌드.

        Args:
            concat_file: concat 텍스트 파일 경로
            output_path: 출력 파일 경로
            overwrite: 덮어쓰기 여부

        Returns:
            FFmpeg 명령어 리스트
        """
        cmd = [self.ffmpeg_path]

        if overwrite:
            cmd.append("-y")

        cmd.extend(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(output_path),
            ]
        )

        return cmd

    def _check_sample_rates(self, video_paths: list[Path]) -> None:
        """병합 전 모든 파일의 오디오 샘플레이트가 일치하는지 검증한다.

        불일치가 있으면 경고를 로깅한다. concat demuxer(-c copy)는 첫 파일의
        샘플레이트를 기준으로 삼기 때문에 다른 샘플레이트의 오디오는 무시된다.
        """
        rates: dict[str, int] = {}
        for path in video_paths:
            rate = probe_audio_sample_rate(path, self.ffprobe_path)
            if rate is not None:
                rates[path.name] = rate

        if not rates:
            return

        unique_rates = set(rates.values())
        if len(unique_rates) > 1:
            rate_info = ", ".join(f"{name}={rate}Hz" for name, rate in rates.items())
            logger.warning(
                "Audio sample rate mismatch — concat may silently drop audio tracks. "
                f"Files: {rate_info}"
            )
        else:
            logger.debug(f"Audio sample rates consistent: {next(iter(unique_rates))}Hz")

    def _check_merged_durations(self, output_path: Path) -> None:
        """병합 결과 파일의 오디오/비디오 길이 차이를 검증한다.

        차이가 AUDIO_DURATION_TOLERANCE_SECONDS를 초과하면 경고를 로깅한다.
        오디오 누락 시 비디오 길이와 크게 벌어지므로 조기에 감지할 수 있다.
        """
        durations = probe_stream_durations(output_path, self.ffprobe_path)
        video_dur = durations.get("video")
        audio_dur = durations.get("audio")

        if video_dur is None or audio_dur is None:
            logger.debug("Could not compare stream durations (missing video or audio stream)")
            return

        diff = abs(video_dur - audio_dur)
        if diff > AUDIO_DURATION_TOLERANCE_SECONDS:
            logger.warning(
                f"Audio/video duration mismatch in merged output: "
                f"video={video_dur:.1f}s, audio={audio_dur:.1f}s, diff={diff:.1f}s "
                f"(threshold={AUDIO_DURATION_TOLERANCE_SECONDS}s) — audio may be truncated"
            )
        else:
            logger.debug(
                f"Stream durations OK: video={video_dur:.1f}s, "
                f"audio={audio_dur:.1f}s, diff={diff:.2f}s"
            )

    def merge(
        self,
        video_paths: list[Path],
        output_path: Path,
        overwrite: bool = True,
    ) -> Path:
        """
        영상 병합 실행.

        병합 전 오디오 샘플레이트 일치를 검증하고,
        병합 후 오디오/비디오 스트림 길이 차이를 검증한다.

        Args:
            video_paths: 병합할 영상 파일 경로 목록
            output_path: 출력 파일 경로
            overwrite: 덮어쓰기 여부

        Returns:
            병합된 파일 경로

        Raises:
            ValueError: video_paths가 비어있는 경우
            RuntimeError: FFmpeg 실행 실패
        """
        if not video_paths:
            raise ValueError("No video files provided")

        # 단일 파일은 복사만
        if len(video_paths) == 1:
            logger.info(f"Single file, copying: {video_paths[0]} -> {output_path}")
            shutil.copy2(video_paths[0], output_path)
            return output_path

        # 병합 전 샘플레이트 일치 검증
        self._check_sample_rates(video_paths)

        # concat 파일 생성
        concat_file = create_concat_file(video_paths, self.temp_dir)

        try:
            # 명령어 빌드 및 실행
            cmd = self.build_merge_command(concat_file, output_path, overwrite)
            logger.info(f"Running FFmpeg merge: {' '.join(cmd)}")

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                logger.error(f"FFmpeg merge failed: {result.stderr}")
                raise RuntimeError(f"FFmpeg merge failed: {result.stderr}")

            logger.info(f"Merge completed: {output_path}")

        finally:
            # concat 파일 정리
            if concat_file.exists():
                concat_file.unlink()
                logger.debug(f"Cleaned up concat file: {concat_file}")

        # 병합 후 오디오/비디오 길이 검증
        self._check_merged_durations(output_path)

        return output_path
