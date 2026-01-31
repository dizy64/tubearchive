"""임시 파일 관리 테스트."""

from pathlib import Path

from tubearchive.utils.temp_manager import TempManager


class TestTempManager:
    """TempManager 테스트."""

    def test_creates_temp_directory(self, tmp_path: Path) -> None:
        """임시 디렉토리 생성."""
        with TempManager(base_dir=tmp_path) as manager:
            assert manager.temp_dir.exists()
            assert manager.temp_dir.is_dir()

    def test_cleanup_on_exit(self, tmp_path: Path) -> None:
        """종료 시 정리."""
        with TempManager(base_dir=tmp_path) as manager:
            temp_dir = manager.temp_dir
            test_file = manager.create_temp_file("test.txt")
            test_file.write_text("test content")

        assert not temp_dir.exists()

    def test_keep_on_exit(self, tmp_path: Path) -> None:
        """keep=True면 보존."""
        with TempManager(base_dir=tmp_path, keep=True) as manager:
            temp_dir = manager.temp_dir
            test_file = manager.create_temp_file("test.txt")
            test_file.write_text("test content")

        assert temp_dir.exists()

    def test_create_temp_file(self, tmp_path: Path) -> None:
        """임시 파일 경로 생성."""
        with TempManager(base_dir=tmp_path) as manager:
            path = manager.create_temp_file("video.mp4")

            assert path.parent == manager.temp_dir
            assert path.name == "video.mp4"

    def test_create_temp_file_with_prefix(self, tmp_path: Path) -> None:
        """접두사 포함 임시 파일."""
        with TempManager(base_dir=tmp_path) as manager:
            path = manager.create_temp_file("video.mp4", prefix="transcoded_")

            assert path.name == "transcoded_video.mp4"

    def test_register_file_for_cleanup(self, tmp_path: Path) -> None:
        """파일 정리 등록."""
        with TempManager(base_dir=tmp_path) as manager:
            external_file = tmp_path / "external.txt"
            external_file.write_text("external")

            manager.register_for_cleanup(external_file)

        assert not external_file.exists()

    def test_get_temp_path(self, tmp_path: Path) -> None:
        """임시 경로 반환."""
        with TempManager(base_dir=tmp_path) as manager:
            path = manager.get_temp_path("subdir", "file.txt")

            assert path == manager.temp_dir / "subdir" / "file.txt"

    def test_ensure_subdirectory(self, tmp_path: Path) -> None:
        """서브디렉토리 생성."""
        with TempManager(base_dir=tmp_path) as manager:
            subdir = manager.ensure_subdir("outputs")

            assert subdir.exists()
            assert subdir.is_dir()
            assert subdir == manager.temp_dir / "outputs"
