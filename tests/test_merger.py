"""영상 병합기 테스트."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.core.merger import Merger, create_concat_file


class TestCreateConcatFile:
    """concat 파일 생성 테스트."""

    def test_creates_concat_file_with_paths(self, tmp_path: Path) -> None:
        """파일 경로 목록으로 concat 파일 생성."""
        video_paths = [
            tmp_path / "video1.mp4",
            tmp_path / "video2.mp4",
            tmp_path / "video3.mp4",
        ]
        for p in video_paths:
            p.touch()

        concat_file = create_concat_file(video_paths, tmp_path)

        assert concat_file.exists()
        content = concat_file.read_text()
        assert f"file '{video_paths[0]}'" in content
        assert f"file '{video_paths[1]}'" in content
        assert f"file '{video_paths[2]}'" in content

    def test_concat_file_preserves_order(self, tmp_path: Path) -> None:
        """파일 순서 보존."""
        video_paths = [
            tmp_path / "first.mp4",
            tmp_path / "second.mp4",
            tmp_path / "third.mp4",
        ]
        for p in video_paths:
            p.touch()

        concat_file = create_concat_file(video_paths, tmp_path)

        lines = concat_file.read_text().strip().split("\n")
        assert "first.mp4" in lines[0]
        assert "second.mp4" in lines[1]
        assert "third.mp4" in lines[2]

    def test_handles_paths_with_spaces(self, tmp_path: Path) -> None:
        """공백이 포함된 경로 처리."""
        video_path = tmp_path / "my video file.mp4"
        video_path.touch()

        concat_file = create_concat_file([video_path], tmp_path)

        content = concat_file.read_text()
        assert "my video file.mp4" in content

    def test_empty_list_raises_error(self, tmp_path: Path) -> None:
        """빈 리스트는 에러."""
        with pytest.raises(ValueError, match="No video files"):
            create_concat_file([], tmp_path)


class TestMerger:
    """Merger 클래스 테스트."""

    @pytest.fixture
    def merger(self, tmp_path: Path) -> Merger:
        """Merger 인스턴스."""
        return Merger(temp_dir=tmp_path)

    def test_build_merge_command(self, merger: Merger, tmp_path: Path) -> None:
        """병합 명령어 빌드."""
        concat_file = tmp_path / "concat.txt"
        concat_file.touch()
        output_path = tmp_path / "merged.mp4"

        cmd = merger.build_merge_command(concat_file, output_path)

        assert "ffmpeg" in cmd[0]
        assert "-f" in cmd
        assert "concat" in cmd
        assert "-safe" in cmd
        assert "0" in cmd
        assert "-c" in cmd
        assert "copy" in cmd
        assert str(concat_file) in cmd
        assert str(output_path) in cmd

    def test_build_merge_command_with_overwrite(
        self, merger: Merger, tmp_path: Path
    ) -> None:
        """덮어쓰기 옵션."""
        concat_file = tmp_path / "concat.txt"
        concat_file.touch()
        output_path = tmp_path / "merged.mp4"

        cmd = merger.build_merge_command(concat_file, output_path, overwrite=True)

        assert "-y" in cmd

    @patch("tubearchive.core.merger.subprocess.run")
    def test_merge_videos(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
    ) -> None:
        """영상 병합 실행."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        video_paths = [
            tmp_path / "video1.mp4",
            tmp_path / "video2.mp4",
        ]
        for p in video_paths:
            p.touch()

        output_path = tmp_path / "merged.mp4"
        result = merger.merge(video_paths, output_path)

        assert result == output_path
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "ffmpeg" in cmd[0]
        assert "-f" in cmd
        assert "concat" in cmd

    @patch("tubearchive.core.merger.subprocess.run")
    def test_merge_cleans_up_concat_file(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
    ) -> None:
        """병합 후 concat 파일 정리."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        video_paths = [tmp_path / "video1.mp4"]
        video_paths[0].touch()
        output_path = tmp_path / "merged.mp4"

        merger.merge(video_paths, output_path)

        # concat 파일이 삭제되었는지 확인
        concat_files = list(merger.temp_dir.glob("concat_*.txt"))
        assert len(concat_files) == 0

    @patch("tubearchive.core.merger.subprocess.run")
    def test_merge_failure_raises_error(
        self,
        mock_run: MagicMock,
        merger: Merger,
        tmp_path: Path,
    ) -> None:
        """병합 실패 시 에러."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stderr="Error: invalid input",
        )

        # 2개 이상의 파일이 필요 (단일 파일은 복사만 함)
        video_paths = [tmp_path / "video1.mp4", tmp_path / "video2.mp4"]
        for p in video_paths:
            p.touch()
        output_path = tmp_path / "merged.mp4"

        with pytest.raises(RuntimeError, match="FFmpeg merge failed"):
            merger.merge(video_paths, output_path)

    def test_merge_with_single_file(self, merger: Merger, tmp_path: Path) -> None:
        """단일 파일은 복사만."""
        video_path = tmp_path / "single.mp4"
        video_path.write_bytes(b"fake video content")
        output_path = tmp_path / "output.mp4"

        with patch("shutil.copy2") as mock_copy:
            result = merger.merge([video_path], output_path)

            mock_copy.assert_called_once_with(video_path, output_path)
            assert result == output_path
