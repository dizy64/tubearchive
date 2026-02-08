"""영상 분할 E2E 테스트.

파이프라인(트랜스코딩+병합) 이후 VideoSplitter를 실행하여
시간 기준 분할 및 코덱 보존을 검증한다.
"""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import run_pipeline
from tubearchive.core.splitter import SplitOptions, VideoSplitter

from .conftest import (
    create_test_video,
    make_pipeline_args,
    probe_video,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard3,
]


class TestVideoSplit:
    """영상 분할 E2E 테스트."""

    def test_split_by_duration(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """10초 영상 → 파이프라인 → 5초 단위 분할 → 2개 이상 파일 생성."""
        create_test_video(e2e_video_dir / "clip.mov", duration=10.0)

        output_file = e2e_output_dir / "merged.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
        )
        merged = run_pipeline(args)

        split_dir = e2e_output_dir / "splits"
        split_dir.mkdir()

        splitter = VideoSplitter()
        options = SplitOptions(duration=5)
        output_files = splitter.split_video(merged, split_dir, options)

        assert len(output_files) >= 2
        for f in output_files:
            assert f.exists()
            assert f.stat().st_size > 0

    def test_split_preserves_codec(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """5초 영상 → 파이프라인 → 분할 → 코덱 HEVC 유지."""
        create_test_video(e2e_video_dir / "clip.mov", duration=5.0)

        output_file = e2e_output_dir / "merged.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
        )
        merged = run_pipeline(args)

        # 원본 병합 파일 코덱 확인
        merged_info = probe_video(merged)
        merged_video = next(s for s in merged_info["streams"] if s["codec_type"] == "video")
        assert merged_video["codec_name"] == "hevc"

        split_dir = e2e_output_dir / "splits"
        split_dir.mkdir()

        splitter = VideoSplitter()
        # 짧은 분할 시간으로 최소 1개 파일 보장
        options = SplitOptions(duration=3)
        output_files = splitter.split_video(merged, split_dir, options)

        assert len(output_files) >= 1
        for f in output_files:
            info = probe_video(f)
            video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
            # segment muxer는 재인코딩 없이 copy → 코덱 동일
            assert video_stream["codec_name"] == "hevc"
