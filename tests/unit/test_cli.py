"""CLI 인터페이스 테스트."""

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.cli import create_parser, main, validate_args


class TestCreateParser:
    """argparse 파서 테스트."""

    def test_creates_parser(self) -> None:
        """파서 생성."""
        parser = create_parser()

        assert isinstance(parser, argparse.ArgumentParser)
        assert parser.prog == "tubearchive"

    def test_parses_no_arguments(self) -> None:
        """인자 없이 파싱 (Case 1: cwd)."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.targets == []
        assert args.output is None
        assert args.no_resume is False
        assert args.keep_temp is False
        assert args.dry_run is False

    def test_parses_file_arguments(self) -> None:
        """파일 인자 파싱 (Case 2: 특정 파일)."""
        parser = create_parser()
        args = parser.parse_args(["video1.mp4", "video2.mov"])

        assert args.targets == ["video1.mp4", "video2.mov"]

    def test_parses_directory_argument(self) -> None:
        """디렉토리 인자 파싱 (Case 3: 디렉토리)."""
        parser = create_parser()
        args = parser.parse_args(["/path/to/videos/"])

        assert args.targets == ["/path/to/videos/"]

    def test_parses_output_option(self) -> None:
        """--output 옵션."""
        parser = create_parser()
        args = parser.parse_args(["--output", "merged.mp4"])

        assert args.output == "merged.mp4"

    def test_parses_short_output_option(self) -> None:
        """-o 옵션."""
        parser = create_parser()
        args = parser.parse_args(["-o", "merged.mp4"])

        assert args.output == "merged.mp4"

    def test_parses_no_resume_flag(self) -> None:
        """--no-resume 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--no-resume"])

        assert args.no_resume is True

    def test_parses_keep_temp_flag(self) -> None:
        """--keep-temp 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--keep-temp"])

        assert args.keep_temp is True

    def test_parses_dry_run_flag(self) -> None:
        """--dry-run 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--dry-run"])

        assert args.dry_run is True

    def test_parses_denoise_flag(self) -> None:
        """--denoise 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--denoise"])

        assert args.denoise is True

    def test_parses_denoise_level(self) -> None:
        """--denoise-level 옵션."""
        parser = create_parser()
        args = parser.parse_args(["--denoise-level", "heavy"])

        assert args.denoise_level == "heavy"

    def test_parses_thumbnail_flag(self) -> None:
        """--thumbnail 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--thumbnail"])

        assert args.thumbnail is True

    def test_thumbnail_flag_default_false(self) -> None:
        """--thumbnail 기본값은 False."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.thumbnail is False

    def test_parses_thumbnail_at_single(self) -> None:
        """--thumbnail-at 단일 값."""
        parser = create_parser()
        args = parser.parse_args(["--thumbnail-at", "00:01:30"])

        assert args.thumbnail_at == ["00:01:30"]

    def test_parses_thumbnail_at_multiple(self) -> None:
        """--thumbnail-at 반복 지정."""
        parser = create_parser()
        args = parser.parse_args(
            [
                "--thumbnail-at",
                "00:01:30",
                "--thumbnail-at",
                "00:05:00",
            ]
        )

        assert args.thumbnail_at == ["00:01:30", "00:05:00"]

    def test_thumbnail_at_default_none(self) -> None:
        """--thumbnail-at 기본값은 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.thumbnail_at is None

    def test_parses_thumbnail_quality(self) -> None:
        """--thumbnail-quality 값."""
        parser = create_parser()
        args = parser.parse_args(["--thumbnail-quality", "5"])

        assert args.thumbnail_quality == 5

    def test_thumbnail_quality_default(self) -> None:
        """--thumbnail-quality 기본값 2."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.thumbnail_quality == 2

    def test_parses_config_option(self) -> None:
        """--config 옵션."""
        parser = create_parser()
        args = parser.parse_args(["--config", "/tmp/custom.toml"])

        assert args.config == "/tmp/custom.toml"

    def test_config_default_is_none(self) -> None:
        """--config 미지정 시 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.config is None

    def test_parses_init_config_flag(self) -> None:
        """--init-config 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--init-config"])

        assert args.init_config is True

    def test_init_config_default_is_false(self) -> None:
        """--init-config 미지정 시 False."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.init_config is False

    def test_upload_privacy_default_is_none(self) -> None:
        """upload_privacy 기본값은 None (config 통합 위해)."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.upload_privacy is None


class TestValidateArgs:
    """인자 검증 테스트."""

    def test_validates_existing_files(self, tmp_path: Path) -> None:
        """존재하는 파일 검증."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()

        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
        )

        result = validate_args(args)

        assert result.targets == [video_file]

    def test_validates_existing_directory(self, tmp_path: Path) -> None:
        """존재하는 디렉토리 검증."""
        args = argparse.Namespace(
            targets=[str(tmp_path)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
        )

        result = validate_args(args)

        assert result.targets == [tmp_path]

    def test_validates_empty_targets_uses_cwd(self) -> None:
        """빈 targets는 cwd 사용."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
        )

        result = validate_args(args)

        assert result.targets == [Path.cwd()]

    def test_raises_for_nonexistent_file(self) -> None:
        """존재하지 않는 파일은 에러."""
        args = argparse.Namespace(
            targets=["/nonexistent/video.mp4"],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
        )

        with pytest.raises(FileNotFoundError):
            validate_args(args)

    def test_validates_output_parent_exists(self, tmp_path: Path) -> None:
        """출력 파일 부모 디렉토리 존재 확인."""
        args = argparse.Namespace(
            targets=[],
            output=str(tmp_path / "output.mp4"),
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
        )

        result = validate_args(args)

        assert result.output == tmp_path / "output.mp4"

    def test_denoise_level_enables_denoise(self, tmp_path: Path) -> None:
        """--denoise-level 지정 시 denoise 자동 활성화."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()

        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            denoise=False,
            denoise_level="heavy",
        )

        result = validate_args(args)

        assert result.denoise is True
        assert result.denoise_level == "heavy"

    def test_env_denoise_defaults(self, tmp_path: Path) -> None:
        """환경 변수로 denoise 기본 활성화."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()

        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            denoise=False,
            denoise_level=None,
        )

        with patch.dict("os.environ", {"TUBEARCHIVE_DENOISE": "true"}):
            result = validate_args(args)

        assert result.denoise is True
        assert result.denoise_level == "medium"

    def test_env_denoise_level_defaults(self, tmp_path: Path) -> None:
        """환경 변수 denoise level 지정 시 자동 활성화."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()

        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            denoise=False,
            denoise_level=None,
        )

        with patch.dict("os.environ", {"TUBEARCHIVE_DENOISE_LEVEL": "heavy"}):
            result = validate_args(args)

        assert result.denoise is True
        assert result.denoise_level == "heavy"

    def test_raises_for_invalid_output_parent(self) -> None:
        """출력 파일 부모 디렉토리 없으면 에러."""
        args = argparse.Namespace(
            targets=[],
            output="/nonexistent/dir/output.mp4",
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
        )

        with pytest.raises(FileNotFoundError, match="Output directory"):
            validate_args(args)


class TestCmdInitConfig:
    """cmd_init_config 테스트."""

    @patch("tubearchive.config.get_default_config_path")
    def test_creates_config_file(self, mock_path: MagicMock, tmp_path: Path) -> None:
        """설정 파일 생성."""
        from tubearchive.cli import cmd_init_config

        config_path = tmp_path / ".tubearchive" / "config.toml"
        mock_path.return_value = config_path

        cmd_init_config()

        assert config_path.exists()
        content = config_path.read_text()
        assert "[general]" in content
        assert "[youtube]" in content

    @patch("tubearchive.cli.safe_input", return_value="n")
    @patch("tubearchive.config.get_default_config_path")
    def test_skips_overwrite_when_declined(
        self, mock_path: MagicMock, mock_input: MagicMock, tmp_path: Path
    ) -> None:
        """덮어쓰기 거부 시 스킵."""
        from tubearchive.cli import cmd_init_config

        config_path = tmp_path / "config.toml"
        config_path.write_text("existing content")
        mock_path.return_value = config_path

        cmd_init_config()

        assert config_path.read_text() == "existing content"

    @patch("tubearchive.cli.safe_input", return_value="y")
    @patch("tubearchive.config.get_default_config_path")
    def test_overwrites_when_confirmed(
        self, mock_path: MagicMock, mock_input: MagicMock, tmp_path: Path
    ) -> None:
        """덮어쓰기 확인 시 덮어씀."""
        from tubearchive.cli import cmd_init_config

        config_path = tmp_path / "config.toml"
        config_path.write_text("old content")
        mock_path.return_value = config_path

        cmd_init_config()

        content = config_path.read_text()
        assert "[general]" in content


class TestMain:
    """main 함수 테스트."""

    @patch("tubearchive.cli.run_pipeline")
    def test_main_calls_pipeline(
        self,
        mock_pipeline: MagicMock,
        tmp_path: Path,
    ) -> None:
        """main이 파이프라인 호출."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()
        output_file = tmp_path / "output.mp4"
        summary_file = tmp_path / "output_summary.md"

        # run_pipeline은 (output_path, summary_path) 튜플 반환
        mock_pipeline.return_value = (output_file, summary_file)

        with patch("sys.argv", ["tubearchive", str(video_file)]):
            main()

        mock_pipeline.assert_called_once()

    @patch("tubearchive.cli.run_pipeline")
    def test_main_dry_run_skips_pipeline(
        self,
        mock_pipeline: MagicMock,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """--dry-run은 파이프라인 스킵."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()

        with patch("sys.argv", ["tubearchive", "--dry-run", str(video_file)]):
            main()

        mock_pipeline.assert_not_called()
        captured = capsys.readouterr()
        assert "Dry run" in captured.out or "dry" in captured.out.lower()


class TestUploadAfterPipeline:
    """_upload_after_pipeline 테스트."""

    @patch("tubearchive.cli.upload_to_youtube")
    @patch("tubearchive.cli.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.cli.init_database")
    def test_upload_after_pipeline_passes_privacy(
        self,
        mock_db: MagicMock,
        mock_playlist: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """privacy 파라미터 전달 확인."""
        from tubearchive.cli import _upload_after_pipeline

        mock_conn = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.close = MagicMock()

        # MergeJobRepository mock
        mock_repo = MagicMock()
        mock_repo.get_latest.return_value = None

        output_path = tmp_path / "output.mp4"
        output_path.touch()
        args = argparse.Namespace(
            upload_privacy="private",
            playlist=None,
            upload_chunk=32,
        )

        with patch("tubearchive.cli.MergeJobRepository", return_value=mock_repo):
            _upload_after_pipeline(output_path, args)

        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["privacy"] == "private"
