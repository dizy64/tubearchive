"""FFmpeg concat demuxer를 사용한 영상 병합기.

트랜스코딩된 영상 파일들을 하나의 MP4 파일로 합치되,
재인코딩 없이(``-c copy``) 스트림을 이어붙인다.
"""

import logging
import shutil
import subprocess
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


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


class Merger:
    """FFmpeg concat demuxer 기반 영상 병합기 (스트림 복사, 재인코딩 없음)."""

    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        temp_dir: Path | None = None,
    ) -> None:
        """
        초기화.

        Args:
            ffmpeg_path: FFmpeg 실행 파일 경로
            temp_dir: 임시 파일 디렉토리 (필수, None이면 에러)

        Raises:
            ValueError: temp_dir이 None인 경우
        """
        if temp_dir is None:
            raise ValueError("temp_dir is required")

        self.ffmpeg_path = ffmpeg_path
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

    def merge(
        self,
        video_paths: list[Path],
        output_path: Path,
        overwrite: bool = True,
    ) -> Path:
        """
        영상 병합 실행.

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
            return output_path

        finally:
            # concat 파일 정리
            if concat_file.exists():
                concat_file.unlink()
                logger.debug(f"Cleaned up concat file: {concat_file}")
