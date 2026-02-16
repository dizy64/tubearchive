"""
파이프라인 옵션 E2E 테스트.

dry-run, parallel 등 파이프라인 옵션의 동작을 실제 ffmpeg로 검증한다.

실행:
    uv run pytest tests/e2e/test_pipeline_options.py -v
"""

import logging
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from tubearchive.cli import _cmd_dry_run, main, run_pipeline
from tubearchive.core import scanner

from .conftest import (
    create_test_video,
    get_video_duration,
    make_pipeline_args,
    probe_video,
)

# ffmpeg 없으면 전체 모듈 스킵
pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard1,
]


class TestDryRun:
    """dry-run 옵션 테스트."""

    def test_dry_run_no_output(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dry_run=True → _cmd_dry_run 호출 시 출력 파일 미생성."""
        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)

        output_file = e2e_output_dir / "dry_run_output.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            dry_run=True,
        )

        # dry_run은 main()에서 _cmd_dry_run을 호출하고 run_pipeline을 스킵함
        _cmd_dry_run(args)

        assert not output_file.exists(), "dry-run에서는 출력 파일이 생성되면 안 됨"

    def test_dry_run_warns_slow_remote_source(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """원격/외장 경로에서 느린 읽기 속도 경고가 출력된다."""
        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)

        output_file = e2e_output_dir / "dry_run_remote.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            dry_run=True,
        )

        caplog.set_level(logging.WARNING)

        monkeypatch.setattr(
            scanner, "_get_remote_source_root", lambda *_args, **_kwargs: Path("/Volumes/RemoteNAS")
        )
        monkeypatch.setattr(scanner, "_check_remote_source", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            scanner, "_measure_source_read_speed", lambda *_args, **_kwargs: 2 * 1024 * 1024
        )

        _cmd_dry_run(args)

        assert any("로컬 복사 후 처리하는 것을 권장합니다" in rec.message for rec in caplog.records)


class TestParallel:
    """parallel 옵션 테스트."""

    def test_parallel_transcoding(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """parallel=2로 2개 영상을 병렬 트랜스코딩 후 병합."""
        create_test_video(e2e_video_dir / "clip_001.mov", duration=2.0)
        create_test_video(e2e_video_dir / "clip_002.mov", duration=2.0)

        output_file = e2e_output_dir / "parallel_output.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            parallel=2,
        )

        result_path = run_pipeline(args)

        assert result_path.exists(), "병렬 트랜스코딩 결과 파일이 존재해야 함"
        assert result_path.stat().st_size > 0

        info = probe_video(result_path)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert video_stream["codec_name"] == "hevc"


class TestTemplateOptions:
    """템플릿 옵션 테스트."""

    def test_template_intro_and_outro_merged(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """인트로/아웃트로 템플릿 지정 시 본문 앞뒤에 병합된다."""
        intro = e2e_video_dir / "intro.mov"
        body = e2e_video_dir / "body.mov"
        outro = e2e_video_dir / "outro.mov"
        create_test_video(intro, duration=1.0)
        create_test_video(body, duration=1.5)
        create_test_video(outro, duration=1.0)

        output_file = e2e_output_dir / "templated_output.mp4"
        args = make_pipeline_args(
            [body],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            template_intro=intro,
            template_outro=outro,
        )

        result_path = run_pipeline(args)
        duration = get_video_duration(result_path)

        assert result_path.exists()
        assert duration >= 3.0


class TestWatchMode:
    """watch 모드 호출 e2e 테스트."""

    def test_main_uses_watch_mode(self, e2e_video_dir: Path) -> None:
        """--watch 옵션 시 main에서 watch 모드 핸들러를 호출."""
        watch_dir = e2e_video_dir / "watch"
        watch_dir.mkdir()

        with (
            patch("tubearchive.cli._run_watch_mode") as mock_run_watch_mode,
            patch("sys.argv", ["tubearchive", "--watch", str(watch_dir)]),
        ):
            main()

        mock_run_watch_mode.assert_called_once()


class TestWatermark:
    """워터마크 옵션 테스트."""

    def test_pipeline_with_watermark(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """워터마크 옵션으로 run_pipeline 실행."""
        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)

        output_file = e2e_output_dir / "watermark_output.mp4"
        args = make_pipeline_args(
            [e2e_video_dir],
            output_file,
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            watermark=True,
            watermark_pos="top-left",
            watermark_size=36,
            watermark_color="yellow",
            watermark_alpha=0.7,
        )

        result_path = run_pipeline(args)

        assert result_path == output_file
        assert result_path.exists()
        assert result_path.stat().st_size > 0
