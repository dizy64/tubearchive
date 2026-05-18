"""
외부 마이크 오디오 E2E 테스트.

실제 ffmpeg를 사용하여 외부 오디오 replace/mix 파이프라인을 검증한다.

실행:
    uv run pytest tests/e2e/test_external_audio.py -v
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tubearchive.app.cli.main import run_pipeline

from .conftest import (
    create_test_video,
    get_audio_stream_count,
    get_video_duration,
    make_pipeline_args,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard2,
]


def create_external_wav(path: Path, *, duration: float = 2.0, frequency: int = 880) -> Path:
    """전용 보이스레코더 WAV를 대체하는 테스트용 sine WAV 생성."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:duration={duration}:sample_rate=48000",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
    return path


def create_pulse_audio(path: Path, *, duration: float, pulses: tuple[float, ...]) -> Path:
    """긴 외부 녹음 매칭 테스트용 pulse WAV 생성."""
    terms = [
        f"if(between(t\\,{pulse}\\,{pulse + 0.05})\\,0.9*sin(2*PI*1000*t)\\,0)" for pulse in pulses
    ]
    expr = "+".join(terms) if terms else "0"
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"aevalsrc={expr}:s=48000:d={duration}",
        "-c:a",
        "pcm_s16le",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
    return path


def create_pulse_video(path: Path, *, duration: float, pulses: tuple[float, ...]) -> Path:
    """긴 외부 녹음 매칭 테스트용 pulse 오디오 포함 영상 생성."""
    audio_path = path.with_suffix(".wav")
    create_pulse_audio(audio_path, duration=duration, pulses=pulses)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={duration}:size=320x240:rate=30",
        "-i",
        str(audio_path),
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")
    return path


class TestExternalAudio:
    """외부 오디오 replace/mix 파이프라인 E2E 테스트."""

    def test_external_audio_replace_pipeline(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """replace 모드는 외부 오디오를 출력 오디오 트랙으로 사용한다."""
        video = create_test_video(e2e_video_dir / "clip.mov", duration=2.0)
        external_audio = create_external_wav(
            e2e_output_dir / "external_replace.wav",
            duration=2.0,
            frequency=880,
        )

        output = e2e_output_dir / "external_replace_output.mp4"
        args = make_pipeline_args(
            [video],
            output,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            external_audio_path=external_audio,
            external_audio_mode="replace",
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0
        assert get_audio_stream_count(result_path) >= 1
        assert abs(get_video_duration(result_path) - 2.0) < 1.0

    def test_external_audio_dir_selects_candidate_pipeline(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """외부 오디오 디렉토리에서 영상과 가까운 후보를 자동 선택한다."""
        video = create_test_video(e2e_video_dir / "clip.mov", duration=2.0)
        audio_dir = e2e_output_dir / "external_audio_candidates"
        audio_dir.mkdir()
        poor = create_external_wav(audio_dir / "old_take.wav", duration=0.5, frequency=440)
        create_external_wav(audio_dir / "best_take.wav", duration=2.0, frequency=880)

        old_time = poor.stat().st_mtime - 3600
        os.utime(poor, (old_time, old_time))

        output = e2e_output_dir / "external_dir_output.mp4"
        args = make_pipeline_args(
            [video],
            output,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            external_audio_dir=audio_dir,
            external_audio_mode="replace",
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0
        assert get_audio_stream_count(result_path) >= 1
        assert abs(get_video_duration(result_path) - 2.0) < 1.0

    def test_external_audio_shorter_than_video_preserves_video_duration(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """외부 오디오가 짧아도 apad + -shortest로 영상 길이를 보존한다."""
        video = create_test_video(e2e_video_dir / "clip.mov", duration=2.0)
        external_audio = create_external_wav(
            e2e_output_dir / "external_short.wav",
            duration=0.5,
            frequency=880,
        )

        output = e2e_output_dir / "external_short_output.mp4"
        args = make_pipeline_args(
            [video],
            output,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            external_audio_path=external_audio,
            external_audio_mode="replace",
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0
        assert get_audio_stream_count(result_path) >= 1
        assert abs(get_video_duration(result_path) - 2.0) < 1.0

    def test_external_audio_mix_pipeline(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """mix 모드는 외부 오디오와 카메라 오디오를 합성한다."""
        video = create_test_video(e2e_video_dir / "clip.mov", duration=2.0)
        external_audio = create_external_wav(
            e2e_output_dir / "external_mix.wav",
            duration=2.0,
            frequency=880,
        )

        output = e2e_output_dir / "external_mix_output.mp4"
        args = make_pipeline_args(
            [video],
            output,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            external_audio_path=external_audio,
            external_audio_mode="mix",
            camera_audio_volume=0.1,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0
        assert get_audio_stream_count(result_path) >= 1
        assert abs(get_video_duration(result_path) - 2.0) < 1.0

    def test_external_audio_long_scope_matches_segments_pipeline(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """긴 외부 녹음 하나를 여러 영상 클립 구간에 자동 매칭한다."""
        clip1 = create_pulse_video(
            e2e_video_dir / "clip_001.mov",
            duration=2.0,
            pulses=(0.3, 1.2),
        )
        clip2 = create_pulse_video(
            e2e_video_dir / "clip_002.mov",
            duration=2.0,
            pulses=(0.4, 1.4),
        )
        long_audio = create_pulse_audio(
            e2e_output_dir / "long_recorder.wav",
            duration=5.5,
            pulses=(1.0, 1.9, 3.6, 4.6),
        )

        output = e2e_output_dir / "external_long_output.mp4"
        args = make_pipeline_args(
            [clip1, clip2],
            output,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            external_audio_path=long_audio,
            external_audio_scope="long",
            external_audio_min_confidence=0.5,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()
        assert result_path.stat().st_size > 0
        assert get_audio_stream_count(result_path) >= 1
        assert abs(get_video_duration(result_path) - 4.0) < 1.0
