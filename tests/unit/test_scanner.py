"""파일 스캐너 테스트."""

import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock

import pytest

from tubearchive.core import scanner
from tubearchive.core.scanner import scan_videos
from tubearchive.models.video import FadeConfig, VideoFile, VideoMetadata


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

    def test_resolves_network_root(self) -> None:
        """SMB/NFS 경로에서 최상위 마운트 경로를 추출한다."""
        path = Path("/Volumes/Backup/Trip2026/clip.mp4")

        remote_root = scanner._get_remote_source_root(path)

        assert remote_root == Path("/Volumes/Backup")

    def test_resolves_no_remote_root(self, temp_video_dir: Path) -> None:
        """로컬 경로는 네트워크 루트를 반환하지 않는다."""
        remote_root = scanner._get_remote_source_root(temp_video_dir)

        assert remote_root is None

    def test_warns_slow_remote_source(
        self,
        temp_video_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """느린 원격/외장 경로에서는 복사 권장 경고를 출력한다."""
        mock_warning = MagicMock()
        monkeypatch.setattr(scanner.logger, "warning", mock_warning)

        def fake_remote_root(_: Path) -> Path | None:
            return Path("/Volumes/RemoteNAS")

        def no_remote_check(*_args: object, **_kwargs: object) -> None:
            return None

        def measure_remote_speed(*_args: object, **_kwargs: object) -> int:
            return 2 * 1024 * 1024

        monkeypatch.setattr(scanner, "_get_remote_source_root", fake_remote_root)
        monkeypatch.setattr(scanner, "_check_remote_source", no_remote_check)
        monkeypatch.setattr(scanner, "_measure_source_read_speed", measure_remote_speed)

        target = temp_video_dir / "video1.mp4"
        scan_videos([target])

        assert any("로컬 복사" in str(call.args[0]) for call in mock_warning.call_args_list)


class TestVideoMetadataProperties:
    """VideoMetadata 프로퍼티 테스트."""

    def _make_metadata(self, **kwargs: object) -> VideoMetadata:
        """테스트용 VideoMetadata 생성 헬퍼."""
        defaults: dict[str, object] = {
            "width": 3840,
            "height": 2160,
            "duration_seconds": 60.0,
            "fps": 29.97,
            "codec": "hevc",
            "pixel_format": "yuv420p10le",
            "is_portrait": False,
            "is_vfr": False,
            "device_model": None,
            "color_space": None,
            "color_transfer": None,
            "color_primaries": None,
        }
        defaults.update(kwargs)
        return VideoMetadata(**defaults)  # type: ignore[arg-type]

    def test_aspect_ratio_landscape(self) -> None:
        """가로 영상 종횡비 (16:9)."""
        meta = self._make_metadata(width=3840, height=2160)
        assert meta.aspect_ratio == pytest.approx(16 / 9, rel=0.01)

    def test_aspect_ratio_portrait(self) -> None:
        """세로 영상 종횡비 (9:16)."""
        meta = self._make_metadata(width=1080, height=1920)
        assert meta.aspect_ratio == pytest.approx(9 / 16, rel=0.01)

    def test_aspect_ratio_square(self) -> None:
        """정사각형 영상 종횡비 (1:1)."""
        meta = self._make_metadata(width=1920, height=1920)
        assert meta.aspect_ratio == pytest.approx(1.0)

    def test_resolution_returns_tuple(self) -> None:
        """resolution 프로퍼티가 (width, height) 튜플을 반환."""
        meta = self._make_metadata(width=1920, height=1080)
        assert meta.resolution == (1920, 1080)


class TestFadeConfig:
    """FadeConfig 기본값 및 커스텀 값 테스트."""

    def test_default_values(self) -> None:
        """기본 페이드 0.5초."""
        config = FadeConfig()
        assert config.fade_in == 0.5
        assert config.fade_out == 0.5

    def test_custom_values(self) -> None:
        """커스텀 페이드 값."""
        config = FadeConfig(fade_in=1.0, fade_out=2.0)
        assert config.fade_in == 1.0
        assert config.fade_out == 2.0

    def test_zero_fade(self) -> None:
        """페이드 없음 (0.0)."""
        config = FadeConfig(fade_in=0.0, fade_out=0.0)
        assert config.fade_in == 0.0
        assert config.fade_out == 0.0
