"""status.py CLI 커맨드 단위 테스트."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.app.cli.status import (
    _delete_build_records,
    cmd_reset_build,
    cmd_reset_upload,
    cmd_status,
    cmd_status_detail,
)
from tubearchive.domain.models.job import JobStatus, MergeJob


def _make_merge_job(
    *,
    job_id: int = 1,
    path: str = "/out/video.mp4",
    youtube_id: str | None = None,
    title: str | None = "Test Video",
    clips_info_json: str | None = None,
) -> MergeJob:
    return MergeJob(
        id=job_id,
        output_path=Path(path),
        video_ids=[10, 11],
        status=JobStatus.COMPLETED,
        youtube_id=youtube_id,
        created_at=datetime(2024, 1, 15, 10, 0),
        title=title,
        date="2024-01-15",
        total_duration_seconds=120.0,
        total_size_bytes=1024 * 1024 * 50,
        clips_info_json=clips_info_json,
    )


def _make_db_session_mock(conn: MagicMock) -> MagicMock:
    mock_session = MagicMock()
    mock_session.__enter__ = MagicMock(return_value=conn)
    mock_session.__exit__ = MagicMock(return_value=False)
    return mock_session


class TestDeleteBuildRecords:
    """_delete_build_records 단위 테스트."""

    def test_empty_video_ids_does_nothing(self) -> None:
        conn = MagicMock()
        _delete_build_records(conn, [])
        conn.execute.assert_not_called()

    def test_deletes_transcoding_then_video(self) -> None:
        conn = MagicMock()
        mock_tc_repo = MagicMock()
        mock_video_repo = MagicMock()

        with (
            patch(
                "tubearchive.app.cli.status.TranscodingJobRepository",
                return_value=mock_tc_repo,
            ),
            patch("tubearchive.app.cli.status.VideoRepository", return_value=mock_video_repo),
        ):
            _delete_build_records(conn, [1, 2, 3])

        mock_tc_repo.delete_by_video_ids.assert_called_once_with([1, 2, 3])
        mock_video_repo.delete_by_ids.assert_called_once_with([1, 2, 3])


class TestCmdStatus:
    """cmd_status 출력 테스트."""

    def _setup_mocks(
        self,
        *,
        transcoding_jobs: list | None = None,
        merge_jobs: list | None = None,
        video_count: int = 0,
        total_jobs: int = 0,
        uploaded_count: int = 0,
    ) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
        conn = MagicMock()
        mock_video_repo = MagicMock()
        mock_video_repo.count_all.return_value = video_count
        mock_tc_repo = MagicMock()
        mock_tc_repo.get_active_with_paths.return_value = transcoding_jobs or []
        mock_merge_repo = MagicMock()
        mock_merge_repo.get_recent.return_value = merge_jobs or []
        mock_merge_repo.count_all.return_value = total_jobs
        mock_merge_repo.count_uploaded.return_value = uploaded_count
        return conn, mock_video_repo, mock_tc_repo, mock_merge_repo

    @patch("tubearchive.app.cli.main.database_session")
    def test_shows_no_jobs_message(
        self, mock_db_ctx: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        conn, mock_video_repo, mock_tc_repo, mock_merge_repo = self._setup_mocks()
        mock_db_ctx.return_value = _make_db_session_mock(conn)

        with (
            patch("tubearchive.app.cli.status.VideoRepository", return_value=mock_video_repo),
            patch("tubearchive.app.cli.status.TranscodingJobRepository", return_value=mock_tc_repo),
            patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_merge_repo),
        ):
            cmd_status()

        out = capsys.readouterr().out
        assert "병합 작업 없음" in out
        assert "통계" in out

    @patch("tubearchive.app.cli.main.database_session")
    def test_shows_merge_jobs(
        self, mock_db_ctx: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        job = _make_merge_job(job_id=1, youtube_id="abc123xyz")
        conn, mock_video_repo, mock_tc_repo, mock_merge_repo = self._setup_mocks(
            merge_jobs=[job], video_count=3, total_jobs=1, uploaded_count=1
        )
        mock_db_ctx.return_value = _make_db_session_mock(conn)

        with (
            patch("tubearchive.app.cli.status.VideoRepository", return_value=mock_video_repo),
            patch("tubearchive.app.cli.status.TranscodingJobRepository", return_value=mock_tc_repo),
            patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_merge_repo),
        ):
            cmd_status()

        out = capsys.readouterr().out
        assert "최근 병합 작업" in out
        assert "Test Video" in out
        assert "abc123" in out
        assert "영상 3개 등록" in out

    @patch("tubearchive.app.cli.main.database_session")
    def test_shows_active_transcoding(
        self, mock_db_ctx: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        tc_row = {"original_path": "/tmp/clip.mp4", "status": "processing", "progress_percent": 42}
        conn, mock_video_repo, mock_tc_repo, mock_merge_repo = self._setup_mocks(
            transcoding_jobs=[tc_row]
        )
        mock_db_ctx.return_value = _make_db_session_mock(conn)

        with (
            patch("tubearchive.app.cli.status.VideoRepository", return_value=mock_video_repo),
            patch("tubearchive.app.cli.status.TranscodingJobRepository", return_value=mock_tc_repo),
            patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_merge_repo),
        ):
            cmd_status()

        out = capsys.readouterr().out
        assert "진행 중인 트랜스코딩" in out
        assert "42" in out


class TestCmdStatusDetail:
    """cmd_status_detail 출력 테스트."""

    @patch("tubearchive.app.cli.main.database_session")
    def test_not_found_prints_error(
        self, mock_db_ctx: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = None

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_status_detail(999)

        out = capsys.readouterr().out
        assert "찾을 수 없습니다" in out

    @patch("tubearchive.app.cli.main.database_session")
    def test_prints_job_info(
        self, mock_db_ctx: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        job = _make_merge_job(job_id=5, youtube_id="yt123")
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = job

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_status_detail(5)

        out = capsys.readouterr().out
        assert "Test Video" in out
        assert "yt123" in out
        assert "2024-01-15" in out

    @patch("tubearchive.app.cli.main.database_session")
    def test_prints_clips_info(
        self, mock_db_ctx: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        clips = [{"name": "a.mp4", "duration": 30.5, "device": "GoPro", "shot_time": "10:00"}]
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        job = _make_merge_job(job_id=3, clips_info_json=json.dumps(clips))
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = job

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_status_detail(3)

        out = capsys.readouterr().out
        assert "클립" in out
        assert "a.mp4" in out
        assert "GoPro" in out

    @patch("tubearchive.app.cli.main.database_session")
    def test_invalid_clips_json_skipped(
        self, mock_db_ctx: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        job = _make_merge_job(job_id=7, clips_info_json="{invalid json}")
        mock_repo = MagicMock()
        mock_repo.get_by_id.return_value = job

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_status_detail(7)

        out = capsys.readouterr().out
        assert "Test Video" in out
        assert "클립" not in out


class TestCmdResetBuild:
    """cmd_reset_build 테스트."""

    @patch("tubearchive.app.cli.main.database_session")
    def test_with_path_found_and_deleted(
        self,
        mock_db_ctx: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "video.mp4"
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        job = _make_merge_job(path=str(target))
        mock_repo = MagicMock()
        mock_repo.get_by_output_path.return_value = job
        mock_repo.delete_by_output_path.return_value = 1

        with (
            patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo),
            patch("tubearchive.app.cli.status._delete_build_records") as mock_del,
        ):
            cmd_reset_build(str(target))

        mock_del.assert_called_once_with(conn, [10, 11])
        out = capsys.readouterr().out
        assert "삭제됨" in out

    @patch("tubearchive.app.cli.main.database_session")
    def test_with_path_not_found(
        self,
        mock_db_ctx: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "missing.mp4"
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        mock_repo = MagicMock()
        mock_repo.get_by_output_path.return_value = None
        mock_repo.delete_by_output_path.return_value = 0

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_reset_build(str(target))

        out = capsys.readouterr().out
        assert "기록이 없습니다" in out

    @patch("tubearchive.app.cli.main.database_session")
    @patch("tubearchive.app.cli.main._interactive_select", return_value=None)
    def test_interactive_cancel(
        self,
        mock_select: MagicMock,
        mock_db_ctx: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        mock_repo = MagicMock()
        mock_repo.get_all.return_value = [_make_merge_job()]

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_reset_build("")

        mock_select.assert_called_once()
        mock_repo.delete.assert_not_called()

    @patch("tubearchive.app.cli.main.database_session")
    def test_interactive_no_jobs(
        self,
        mock_db_ctx: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        mock_repo = MagicMock()
        mock_repo.get_all.return_value = []

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_reset_build("")

        out = capsys.readouterr().out
        assert "빌드 기록이 없습니다" in out

    @patch("tubearchive.app.cli.main.database_session")
    @patch("tubearchive.app.cli.main._interactive_select", return_value=0)
    def test_interactive_selects_and_deletes(
        self,
        mock_select: MagicMock,
        mock_db_ctx: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        job = _make_merge_job(job_id=3)
        mock_repo = MagicMock()
        mock_repo.get_all.return_value = [job]

        with (
            patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo),
            patch("tubearchive.app.cli.status._delete_build_records") as mock_del,
        ):
            cmd_reset_build("")

        mock_del.assert_called_once_with(conn, [10, 11])
        mock_repo.delete.assert_called_once_with(3)
        out = capsys.readouterr().out
        assert "삭제됨" in out


class TestCmdResetUpload:
    """cmd_reset_upload 테스트."""

    @patch("tubearchive.app.cli.main.database_session")
    def test_with_youtube_id_clears_it(
        self,
        mock_db_ctx: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "video.mp4"
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        job = _make_merge_job(path=str(target), youtube_id="oldYtId")
        mock_repo = MagicMock()
        mock_repo.get_by_output_path.return_value = job

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_reset_upload(str(target))

        mock_repo.clear_youtube_id.assert_called_once_with(1)
        out = capsys.readouterr().out
        assert "초기화됨" in out
        assert "oldYtId" in out

    @patch("tubearchive.app.cli.main.database_session")
    def test_with_no_youtube_id_warns(
        self,
        mock_db_ctx: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "video.mp4"
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        job = _make_merge_job(path=str(target), youtube_id=None)
        mock_repo = MagicMock()
        mock_repo.get_by_output_path.return_value = job

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_reset_upload(str(target))

        mock_repo.clear_youtube_id.assert_not_called()
        out = capsys.readouterr().out
        assert "이미 업로드 기록이 없습니다" in out

    @patch("tubearchive.app.cli.main.database_session")
    def test_with_path_not_found(
        self,
        mock_db_ctx: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "missing.mp4"
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        mock_repo = MagicMock()
        mock_repo.get_by_output_path.return_value = None

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_reset_upload(str(target))

        out = capsys.readouterr().out
        assert "기록이 없습니다" in out

    @patch("tubearchive.app.cli.main.database_session")
    def test_interactive_no_uploaded_jobs(
        self,
        mock_db_ctx: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        mock_repo = MagicMock()
        mock_repo.get_uploaded.return_value = []

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_reset_upload("")

        out = capsys.readouterr().out
        assert "업로드된 영상이 없습니다" in out

    @patch("tubearchive.app.cli.main.database_session")
    @patch("tubearchive.app.cli.main._interactive_select", return_value=0)
    def test_interactive_selects_and_clears(
        self,
        mock_select: MagicMock,
        mock_db_ctx: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        conn = MagicMock()
        mock_db_ctx.return_value = _make_db_session_mock(conn)
        job = _make_merge_job(job_id=7, youtube_id="ytAbc")
        mock_repo = MagicMock()
        mock_repo.get_uploaded.return_value = [job]

        with patch("tubearchive.app.cli.status.MergeJobRepository", return_value=mock_repo):
            cmd_reset_upload("")

        mock_repo.clear_youtube_id.assert_called_once_with(7)
        out = capsys.readouterr().out
        assert "초기화됨" in out
