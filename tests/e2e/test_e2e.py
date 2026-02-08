"""
E2E 파이프라인 테스트.

실제 ffmpeg를 사용하여 영상 생성 → 트랜스코딩 → 병합 파이프라인을 검증한다.
테스트 영상은 ffmpeg의 testsrc/color 필터로 생성하므로 외부 파일 불필요.

실행:
    uv run pytest tests/test_e2e.py -v              # E2E 테스트만
    uv run pytest tests/test_e2e.py -v -k transcode  # 트랜스코딩만
"""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import ValidatedArgs, run_pipeline

from .conftest import create_test_video, probe_video

# ffmpeg 없으면 전체 모듈 스킵
pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard1,
]

# ---------- Fixtures ----------


@pytest.fixture
def single_landscape_video(e2e_video_dir: Path) -> Path:
    """가로 영상 1개."""
    return create_test_video(e2e_video_dir / "landscape.mov")


@pytest.fixture
def two_landscape_videos(e2e_video_dir: Path) -> Path:
    """가로 영상 2개 (병합 테스트용). 디렉토리 경로 반환."""
    create_test_video(e2e_video_dir / "clip_001.mov", duration=2.0)
    create_test_video(e2e_video_dir / "clip_002.mov", duration=2.0)
    return e2e_video_dir


@pytest.fixture
def portrait_video(e2e_video_dir: Path) -> Path:
    """세로 영상 1개 (1080x1920)."""
    return create_test_video(
        e2e_video_dir / "portrait.mov",
        width=1080,
        height=1920,
        duration=2.0,
    )


@pytest.fixture
def mixed_videos(e2e_video_dir: Path) -> Path:
    """가로 + 세로 영상 혼합. 디렉토리 경로 반환."""
    create_test_video(e2e_video_dir / "landscape.mov", duration=2.0)
    create_test_video(
        e2e_video_dir / "portrait.mov",
        width=1080,
        height=1920,
        duration=2.0,
    )
    return e2e_video_dir


# ---------- 테스트 ----------


class TestVideoGeneration:
    """테스트 영상 생성이 올바르게 동작하는지 확인."""

    def test_creates_landscape_video(self, single_landscape_video: Path) -> None:
        """가로 영상이 생성되고 메타데이터가 올바른지 확인."""
        assert single_landscape_video.exists()
        assert single_landscape_video.stat().st_size > 0

        info = probe_video(single_landscape_video)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")
        audio_stream = next(s for s in info["streams"] if s["codec_type"] == "audio")

        assert int(video_stream["width"]) == 1920
        assert int(video_stream["height"]) == 1080
        assert audio_stream["codec_name"] == "aac"

    def test_creates_portrait_video(self, portrait_video: Path) -> None:
        """세로 영상이 생성되고 메타데이터가 올바른지 확인."""
        assert portrait_video.exists()

        info = probe_video(portrait_video)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")

        assert int(video_stream["width"]) == 1080
        assert int(video_stream["height"]) == 1920


class TestTranscodePipeline:
    """트랜스코딩 파이프라인 E2E 테스트."""

    def test_single_video_transcode_and_merge(
        self,
        single_landscape_video: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """단일 영상: 스캔 → 트랜스코딩 → 출력 파일 생성."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        output_file = e2e_output_dir / "output.mp4"
        args = ValidatedArgs(
            targets=[single_landscape_video],
            output=output_file,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )

        result_path = run_pipeline(args)

        # 출력 파일 존재
        assert result_path.exists()
        assert result_path.stat().st_size > 0

        # 출력 메타데이터 검증
        info = probe_video(result_path)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")

        # HEVC 코덱이어야 함
        assert video_stream["codec_name"] == "hevc"
        # 10-bit (p010le)
        assert "10" in video_stream.get("pix_fmt", "") or "p010" in video_stream.get("pix_fmt", "")

    def test_two_videos_merge(
        self,
        two_landscape_videos: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """2개 영상: 스캔 → 트랜스코딩 → 병합 → 단일 출력 파일."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        output_file = e2e_output_dir / "merged.mp4"
        args = ValidatedArgs(
            targets=[two_landscape_videos],
            output=output_file,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()

        # 병합된 영상 길이 ≈ 2초 + 2초 (fade 때문에 약간 차이 가능)
        info = probe_video(result_path)
        duration = float(info["format"]["duration"])
        assert duration >= 3.0  # 최소 3초 이상 (fade overlap 고려)

    def test_portrait_video_gets_letterboxed(
        self,
        portrait_video: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """세로 영상: blur 배경 + 전경 오버레이로 3840x2160 출력."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        output_file = e2e_output_dir / "portrait_output.mp4"
        args = ValidatedArgs(
            targets=[portrait_video],
            output=output_file,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()

        info = probe_video(result_path)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")

        # 3840x2160으로 표준화
        assert int(video_stream["width"]) == 3840
        assert int(video_stream["height"]) == 2160


class TestResumeAndRerun:
    """Resume 및 재실행 시나리오 테스트."""

    def test_rerun_skips_completed_videos(
        self,
        two_landscape_videos: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """같은 파이프라인을 두 번 실행하면 두 번째는 재트랜스코딩 없이 완료."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        # 1차 실행
        output1 = e2e_output_dir / "run1.mp4"
        args1 = ValidatedArgs(
            targets=[two_landscape_videos],
            output=output1,
            output_dir=None,
            no_resume=False,
            keep_temp=True,  # 임시 파일 보존 (resume 테스트용)
            dry_run=False,
        )
        run_pipeline(args1)

        # 2차 실행 (같은 소스, 다른 출력)
        output2 = e2e_output_dir / "run2.mp4"
        args2 = ValidatedArgs(
            targets=[two_landscape_videos],
            output=output2,
            output_dir=None,
            no_resume=False,
            keep_temp=True,
            dry_run=False,
        )
        run_pipeline(args2)

        # 둘 다 존재해야 함
        assert output1.exists()
        assert output2.exists()

    def test_rerun_retranscodes_after_cleanup(
        self,
        single_landscape_video: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """임시 파일 삭제 후 재실행하면 재트랜스코딩."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        # 1차 실행 (임시 파일 삭제됨 - keep_temp=False)
        output1 = e2e_output_dir / "first.mp4"
        args1 = ValidatedArgs(
            targets=[single_landscape_video],
            output=output1,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )
        run_pipeline(args1)
        assert output1.exists()

        # 2차 실행 (임시 파일이 없으므로 재트랜스코딩)
        output2 = e2e_output_dir / "second.mp4"
        args2 = ValidatedArgs(
            targets=[single_landscape_video],
            output=output2,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )
        run_pipeline(args2)
        assert output2.exists()


class TestMixedInputs:
    """다양한 입력 조합 테스트."""

    def test_mixed_landscape_portrait_merge(
        self,
        mixed_videos: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """가로 + 세로 영상을 병합하면 모두 3840x2160으로 표준화."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        output_file = e2e_output_dir / "mixed.mp4"
        args = ValidatedArgs(
            targets=[mixed_videos],
            output=output_file,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
        )

        result_path = run_pipeline(args)

        assert result_path.exists()

        info = probe_video(result_path)
        video_stream = next(s for s in info["streams"] if s["codec_type"] == "video")

        assert int(video_stream["width"]) == 3840
        assert int(video_stream["height"]) == 2160
