"""파일 스캐너 테스트."""

import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from tubearchive.core.scanner import scan_videos
from tubearchive.models.video import VideoFile


class TestScanner:
    """파일 스캐너 테스트."""

    @pytest.fixture
    def temp_video_dir(self) -> Path:
        """임시 영상 디렉토리 생성."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # 영상 파일 생성 (생성 시간 다르게)
            files = [
                ("video1.mp4", 1.0),
                ("video2.mov", 2.0),
                ("video3.mts", 3.0),
                ("readme.txt", 4.0),  # 비영상 파일
            ]

            created_files = []
            for filename, _delay in files:
                filepath = tmp_path / filename
                filepath.write_text("")
                time.sleep(0.01)  # 파일 생성 시간 차이 보장
                created_files.append(filepath)

            yield tmp_path

    @pytest.fixture
    def nested_video_dir(self) -> Path:
        """중첩된 디렉토리 구조."""
        with TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # 중첩 구조 생성
            (tmp_path / "subdir1").mkdir()
            (tmp_path / "subdir1" / "video1.mp4").write_text("")
            time.sleep(0.01)
            (tmp_path / "subdir2").mkdir()
            (tmp_path / "subdir2" / "video2.mov").write_text("")
            time.sleep(0.01)
            (tmp_path / "video3.mts").write_text("")

            yield tmp_path

    def test_empty_args_scans_cwd(
        self, temp_video_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """인자 없이 호출하면 현재 디렉토리 스캔."""
        monkeypatch.chdir(temp_video_dir)

        videos = scan_videos([])

        assert len(videos) == 3  # .mp4, .mov, .mts만
        assert all(isinstance(v, VideoFile) for v in videos)
        assert all(v.path.parent.resolve() == temp_video_dir.resolve() for v in videos)

    def test_sorts_by_creation_time(self, temp_video_dir: Path) -> None:
        """파일 생성 시간 순으로 정렬."""
        videos = scan_videos([temp_video_dir])

        # 생성 시간 순서 확인
        for i in range(len(videos) - 1):
            assert videos[i].creation_time <= videos[i + 1].creation_time

        # 파일명 순서 확인 (생성 순서와 동일하게 만들었음)
        filenames = [v.path.name for v in videos]
        assert filenames == ["video1.mp4", "video2.mov", "video3.mts"]

    def test_filters_video_extensions(self, temp_video_dir: Path) -> None:
        """영상 확장자만 필터링."""
        videos = scan_videos([temp_video_dir])

        extensions = {v.path.suffix.lower() for v in videos}
        assert extensions == {".mp4", ".mov", ".mts"}
        assert not any(v.path.suffix == ".txt" for v in videos)

    def test_specific_files(self, temp_video_dir: Path) -> None:
        """특정 파일들만 스캔."""
        target_files = [
            temp_video_dir / "video1.mp4",
            temp_video_dir / "video3.mts",
        ]

        videos = scan_videos(target_files)

        assert len(videos) == 2
        assert {v.path.name for v in videos} == {"video1.mp4", "video3.mts"}

    def test_directory_scan_recursive(self, nested_video_dir: Path) -> None:
        """디렉토리 재귀 스캔."""
        videos = scan_videos([nested_video_dir])

        assert len(videos) == 3
        filenames = {v.path.name for v in videos}
        assert filenames == {"video1.mp4", "video2.mov", "video3.mts"}

    def test_nonexistent_file_raises_error(self) -> None:
        """존재하지 않는 파일은 에러."""
        with pytest.raises(FileNotFoundError):
            scan_videos([Path("/nonexistent/video.mp4")])

    def test_mixed_files_and_dirs(self, temp_video_dir: Path) -> None:
        """파일과 디렉토리 혼합."""
        file_target = temp_video_dir / "video1.mp4"

        videos = scan_videos([file_target, temp_video_dir])

        # video1.mp4가 중복되지 않도록 처리되어야 함
        assert len(videos) >= 3
        filenames = [v.path.name for v in videos]
        assert "video1.mp4" in filenames
