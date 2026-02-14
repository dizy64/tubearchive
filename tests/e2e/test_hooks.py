"""훅 통합 E2E 테스트."""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import ValidatedArgs, run_pipeline
from tubearchive.config import HooksConfig

from .conftest import create_test_video

# ffmpeg 없으면 E2E 모듈 스킵
pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard1,
]


def _write_hook_script(path: Path, marker_path: Path, event_label: str) -> Path:
    """훅에서 실행할 셸 스크립트를 생성한다."""
    path.write_text(
        "#!/bin/sh\n"
        f'printf "{event_label}|%s|%s|%s|%s\\n" '
        '"$TUBEARCHIVE_OUTPUT_PATH" "$TUBEARCHIVE_INPUT_PATHS" '
        '"$TUBEARCHIVE_INPUT_COUNT" "$TUBEARCHIVE_YOUTUBE_ID" '
        f'>> "{marker_path}"\n'
    )
    path.chmod(0o755)
    return path


class TestPipelineHooks:
    """파이프라인 훅이 실제로 실행되는지 검증."""

    def test_run_pipeline_executes_transcode_and_merge_hooks(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """run_pipeline 진입 시 on_transcode, on_merge 훅이 환경변수와 함께 실행된다."""
        monkeypatch.setenv("TUBEARCHIVE_DB_PATH", str(e2e_db))

        marker = tmp_path / "hook_events.log"
        transcode_hook = _write_hook_script(
            tmp_path / "on_transcode.sh",
            marker,
            "on_transcode",
        )
        merge_hook = _write_hook_script(
            tmp_path / "on_merge.sh",
            marker,
            "on_merge",
        )

        source = create_test_video(e2e_video_dir / "source.mov", duration=2.0)
        output = e2e_output_dir / "output.mp4"
        args = ValidatedArgs(
            targets=[source],
            output=output,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            hooks=HooksConfig(
                on_transcode=(str(transcode_hook),),
                on_merge=(str(merge_hook),),
            ),
        )

        result_path = run_pipeline(args)
        assert result_path == output
        assert result_path.exists()
        assert marker.exists()

        lines = [line for line in marker.read_text().splitlines() if line]
        assert any(line.startswith("on_transcode|") for line in lines)
        assert any(line.startswith("on_merge|") for line in lines)

        transcode_line = next(line for line in lines if line.startswith("on_transcode|"))
        (
            _,
            transcode_output,
            _trans_inputs,
            transcode_count,
            _trans_youtube,
        ) = transcode_line.split("|", 4)
        assert transcode_output
        assert transcode_count == "1"

        merge_line = next(line for line in lines if line.startswith("on_merge|"))
        _, merge_output, _merge_inputs, merge_count, _merge_youtube = merge_line.split("|", 4)
        assert merge_output == str(result_path)
        assert merge_count == "1"
