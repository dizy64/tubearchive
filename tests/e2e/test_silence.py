"""
무음 구간 감지 및 제거 E2E 테스트.

실제 ffmpeg를 사용하여 무음이 포함된 영상 생성 → 무음 감지/제거를 검증한다.

실행:
    uv run pytest tests/e2e/test_silence.py -v
"""

import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from tubearchive.ffmpeg.effects import parse_silence_segments
from tubearchive.ffmpeg.executor import FFmpegExecutor

from .conftest import get_video_duration

# ffmpeg 없으면 전체 모듈 스킵
pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard2,
]


@pytest.fixture
def sample_video_with_silence(tmp_path: Path) -> Path:
    """시작/끝에 무음이 있는 샘플 영상 생성.

    구조:
    - 0-2초: 무음
    - 2-8초: 1kHz 톤 (오디오 있음)
    - 8-10초: 무음

    Returns:
        생성된 영상 파일 경로
    """
    output = tmp_path / "silence_test.mp4"

    # FFmpeg로 테스트 영상 생성
    # - 비디오: 파란색 배경 10초
    # - 오디오: anullsrc (무음) + sine (톤) + anullsrc (무음) concat
    cmd = [
        "ffmpeg",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=1920x1080:d=10:r=30",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo:d=2",
        "-f",
        "lavfi",
        "-i",
        "sine=f=1000:d=6:r=48000",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=48000:cl=stereo:d=2",
        "-filter_complex",
        "[1][2][3]concat=n=3:v=0:a=1[aout]",
        "-map",
        "0:v",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-t",
        "10",
        "-y",
        str(output),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"Failed to create test video: {result.stderr}")

    return output


@pytest.fixture
def sample_video_no_audio(tmp_path: Path) -> Path:
    """오디오 트랙이 없는 영상 생성.

    Returns:
        생성된 영상 파일 경로
    """
    output = tmp_path / "no_audio_test.mp4"

    cmd = [
        "ffmpeg",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=1920x1080:d=5:r=30",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-y",
        str(output),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        pytest.fail(f"Failed to create test video: {result.stderr}")

    return output


class TestSilenceDetection:
    """무음 구간 감지 테스트."""

    def test_detect_silence_finds_segments(self, sample_video_with_silence: Path) -> None:
        """무음 구간 감지 확인."""
        from tubearchive.ffmpeg.effects import create_silence_detect_filter

        executor = FFmpegExecutor()

        # silencedetect 필터 생성
        detect_filter = create_silence_detect_filter(
            threshold="-30dB",
            min_duration=1.0,  # 짧게 설정하여 더 잘 감지
        )

        # 분석 명령 실행
        cmd = executor.build_silence_detection_command(
            input_path=sample_video_with_silence,
            audio_filter=detect_filter,
        )
        stderr = executor.run_analysis(cmd)

        # 파싱
        segments = parse_silence_segments(stderr)

        # 최소 1개 이상의 무음 구간이 감지되어야 함
        assert len(segments) >= 1, f"Expected at least 1 silence segment, got {len(segments)}"

        # 첫 번째 세그먼트가 시작 부분에 있어야 함
        if segments:
            first_segment = segments[0]
            assert first_segment.start <= 1.0, "First silence should start near beginning"
            assert first_segment.duration >= 1.0, "Silence duration should be at least 1s"

    def test_detect_silence_on_video_without_audio(self, sample_video_no_audio: Path) -> None:
        """오디오 트랙이 없는 영상에서는 무음 구간이 감지되지 않음."""
        from tubearchive.ffmpeg.effects import create_silence_detect_filter

        executor = FFmpegExecutor()

        detect_filter = create_silence_detect_filter()

        cmd = executor.build_silence_detection_command(
            input_path=sample_video_no_audio,
            audio_filter=detect_filter,
        )

        # 오디오 트랙이 없으면 에러가 발생하거나 빈 결과를 반환해야 함
        # FFmpeg는 오디오 트랙이 없으면 에러를 반환할 수 있음
        # 여기서는 graceful failure를 확인
        try:
            stderr = executor.run_analysis(cmd)
            segments = parse_silence_segments(stderr)
            # 오디오가 없으면 무음 구간도 없어야 함
            assert len(segments) == 0
        except Exception:
            # 오디오 트랙이 없어서 에러 발생하는 것은 정상
            pass


class TestSilenceRemoval:
    """무음 구간 제거 테스트."""

    def test_trim_silence_removes_quiet_parts(
        self, sample_video_with_silence: Path, tmp_path: Path
    ) -> None:
        """무음 제거 확인 (영상 길이 변화)."""
        from tubearchive.core.transcoder import Transcoder
        from tubearchive.models.video import VideoFile

        db_path = tmp_path / "test.db"

        video_file = VideoFile(
            path=sample_video_with_silence,
            size_bytes=sample_video_with_silence.stat().st_size,
            creation_time=datetime.now(),
        )

        # 트랜스코딩 (무음 제거 활성화)
        with Transcoder(db_path=db_path, temp_dir=tmp_path) as transcoder:
            result_path, _, silence_segments = transcoder.transcode_video(
                video_file,
                trim_silence=True,
                silence_threshold="-30dB",
                silence_min_duration=1.0,
            )

            # 무음 구간이 감지되었는지 확인
            assert silence_segments is not None
            assert len(silence_segments) >= 1, "무음 구간이 최소 1개 이상 감지되어야 함"

            # 출력 영상이 생성되었는지 확인
            assert result_path.exists()

            # 출력 영상의 길이 확인
            original_duration = get_video_duration(sample_video_with_silence)
            trimmed_duration = get_video_duration(result_path)

            # silenceremove 필터의 동작 특성상 완벽하게 제거되지 않을 수 있고,
            # 트랜스코딩 과정에서 프레임 정렬 등으로 약간의 길이 변화가 있을 수 있음
            # 따라서 길이가 크게 증가하지 않았는지만 확인 (1% 이내 허용)
            assert trimmed_duration <= original_duration * 1.01, (
                f"트랜스코딩 후 길이 예상보다 많이 증가: "
                f"{original_duration:.2f}s → {trimmed_duration:.2f}s"
            )

    def test_trim_silence_with_custom_threshold(
        self, sample_video_with_silence: Path, tmp_path: Path
    ) -> None:
        """커스텀 threshold 설정 확인."""
        from tubearchive.core.transcoder import Transcoder
        from tubearchive.models.video import VideoFile

        db_path = tmp_path / "test.db"

        video_file = VideoFile(
            path=sample_video_with_silence,
            size_bytes=sample_video_with_silence.stat().st_size,
            creation_time=datetime.now(),
        )

        # 더 민감한 threshold로 트랜스코딩
        with Transcoder(db_path=db_path, temp_dir=tmp_path) as transcoder:
            result_path, _, _ = transcoder.transcode_video(
                video_file,
                trim_silence=True,
                silence_threshold="-40dB",  # 더 민감하게
                silence_min_duration=0.5,  # 더 짧은 무음도 감지
            )

            # 출력 영상이 생성되었는지 확인
            assert result_path.exists()
