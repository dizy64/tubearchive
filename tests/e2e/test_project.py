"""프로젝트 관리 E2E 테스트.

파이프라인 실행 시 프로젝트 자동 생성 및 merge_job 연결을 검증한다.

실행:
    uv run pytest tests/e2e/test_project.py -v
"""

import shutil
from pathlib import Path

import pytest

from tubearchive.cli import run_pipeline
from tubearchive.database.repository import ProjectRepository
from tubearchive.database.schema import init_database

from .conftest import create_test_video, make_pipeline_args

pytestmark = [
    pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed"),
    pytest.mark.e2e_shard1,
]


class TestProject:
    """프로젝트 연결 E2E 테스트."""

    def test_pipeline_with_project(
        self,
        e2e_video_dir: Path,
        e2e_output_dir: Path,
        e2e_db: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """project='테스트프로젝트' → DB에 프로젝트 생성 + merge_job 연결."""
        create_test_video(e2e_video_dir / "clip.mov", duration=2.0)

        args = make_pipeline_args(
            targets=[e2e_video_dir],
            output=e2e_output_dir / "project_output.mp4",
            db_path=e2e_db,
            monkeypatch=monkeypatch,
            project="테스트프로젝트",
        )

        result_path = run_pipeline(args)
        assert result_path.exists()

        # DB에서 프로젝트 확인
        conn = init_database(e2e_db)
        try:
            repo = ProjectRepository(conn)

            # 프로젝트가 생성되었는지 확인
            project = repo.get_by_name("테스트프로젝트")
            assert project is not None, "프로젝트가 생성되어야 함"
            assert project.name == "테스트프로젝트"

            # 프로젝트에 merge_job이 연결되었는지 확인
            merge_jobs = repo.get_merge_jobs(project.id)
            assert len(merge_jobs) >= 1, "최소 1개의 merge_job이 연결되어야 함"
        finally:
            conn.close()
