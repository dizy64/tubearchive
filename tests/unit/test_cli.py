"""CLI 인터페이스 테스트."""

import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.cli import (
    CATALOG_STATUS_SENTINEL,
    ClipInfo,
    TranscodeOptions,
    create_parser,
    database_session,
    main,
    validate_args,
)
from tubearchive.utils import truncate_path


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

    def test_parses_group_flags(self) -> None:
        """--group/--no-group 플래그."""
        parser = create_parser()

        args = parser.parse_args(["--group"])
        assert args.group is True
        assert args.no_group is False

        args = parser.parse_args(["--no-group"])
        assert args.no_group is True
        assert args.group is False

    def test_parses_fade_duration(self) -> None:
        """--fade-duration 옵션."""
        parser = create_parser()
        args = parser.parse_args(["--fade-duration", "0.75"])

        assert args.fade_duration == 0.75

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

    def test_parses_set_thumbnail(self) -> None:
        """--set-thumbnail 경로 파싱."""
        parser = create_parser()
        args = parser.parse_args(["--set-thumbnail", "/path/to/cover.jpg"])

        assert args.set_thumbnail == "/path/to/cover.jpg"

    def test_set_thumbnail_default_is_none(self) -> None:
        """--set-thumbnail 미지정 시 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.set_thumbnail is None

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

    def test_parses_exclude_single(self) -> None:
        """--exclude 단일 패턴."""
        parser = create_parser()
        args = parser.parse_args(["--exclude", "GH*"])

        assert args.exclude == ["GH*"]

    def test_parses_exclude_multiple(self) -> None:
        """--exclude 반복 지정."""
        parser = create_parser()
        args = parser.parse_args(["--exclude", "GH*", "--exclude", "*.mts"])

        assert args.exclude == ["GH*", "*.mts"]

    def test_exclude_default_is_none(self) -> None:
        """--exclude 미지정 시 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.exclude is None

    def test_parses_include_only_single(self) -> None:
        """--include-only 단일 패턴."""
        parser = create_parser()
        args = parser.parse_args(["--include-only", "*.mp4"])

        assert args.include_only == ["*.mp4"]

    def test_parses_include_only_multiple(self) -> None:
        """--include-only 반복 지정."""
        parser = create_parser()
        args = parser.parse_args(["--include-only", "*.mp4", "--include-only", "*.mov"])

        assert args.include_only == ["*.mp4", "*.mov"]

    def test_include_only_default_is_none(self) -> None:
        """--include-only 미지정 시 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.include_only is None

    def test_parses_sort_option(self) -> None:
        """--sort 옵션."""
        parser = create_parser()
        args = parser.parse_args(["--sort", "name"])

        assert args.sort == "name"

    def test_sort_default_is_none(self) -> None:
        """--sort 미지정 시 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.sort is None

    def test_sort_invalid_choice_raises(self) -> None:
        """--sort에 잘못된 값 지정 시 에러."""
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--sort", "invalid"])

    def test_parses_reorder_flag(self) -> None:
        """--reorder 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--reorder"])

        assert args.reorder is True

    def test_reorder_default_is_false(self) -> None:
        """--reorder 미지정 시 False."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.reorder is False

    def test_parses_catalog_flag(self) -> None:
        """--catalog 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--catalog"])

        assert args.catalog is True

    def test_parses_search_pattern(self) -> None:
        """--search 패턴 값."""
        parser = create_parser()
        args = parser.parse_args(["--search", "2026-01"])

        assert args.search == "2026-01"

    def test_parses_search_empty(self) -> None:
        """--search 값 없이 사용."""
        parser = create_parser()
        args = parser.parse_args(["--search"])

        assert args.search == ""

    def test_parses_device_filter(self) -> None:
        """--device 필터."""
        parser = create_parser()
        args = parser.parse_args(["--device", "GoPro"])

        assert args.device == "GoPro"

    def test_parses_status_filter(self) -> None:
        """--status 값 지정."""
        parser = create_parser()
        args = parser.parse_args(["--status", "completed"])

        assert args.status == "completed"

    def test_parses_status_view(self) -> None:
        """--status 단독 사용."""
        parser = create_parser()
        args = parser.parse_args(["--status"])

        assert args.status == CATALOG_STATUS_SENTINEL

    def test_parses_json_flag(self) -> None:
        """--json 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--json"])

        assert args.json is True

    def test_parses_csv_flag(self) -> None:
        """--csv 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--csv"])

        assert args.csv is True

    def test_parses_lut_option(self) -> None:
        """--lut 옵션."""
        parser = create_parser()
        args = parser.parse_args(["--lut", "/path/to/lut.cube"])

        assert args.lut == "/path/to/lut.cube"

    def test_lut_default_is_none(self) -> None:
        """--lut 기본값 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.lut is None

    def test_parses_auto_lut_flag(self) -> None:
        """--auto-lut 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--auto-lut"])

        assert args.auto_lut is True

    def test_parses_no_auto_lut_flag(self) -> None:
        """--no-auto-lut 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--no-auto-lut"])

        assert args.no_auto_lut is True

    def test_parses_lut_before_hdr_flag(self) -> None:
        """--lut-before-hdr 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--lut-before-hdr"])

        assert args.lut_before_hdr is True

    def test_lut_before_hdr_default_false(self) -> None:
        """--lut-before-hdr 기본값 False."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.lut_before_hdr is False


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

    def test_defaults_for_group_and_fade(self, tmp_path: Path) -> None:
        """group/fade 기본값 확인."""
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

        assert result.group_sequences is True
        assert result.fade_duration == 0.5

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

    def test_validates_set_thumbnail_jpeg(self, tmp_path: Path) -> None:
        """유효한 썸네일 파일 경로."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()
        thumbnail = tmp_path / "cover.jpg"
        thumbnail.write_bytes(b"\xff\xd8")

        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            set_thumbnail=str(thumbnail),
        )

        result = validate_args(args)

        assert result.set_thumbnail == thumbnail.resolve()

    def test_set_thumbnail_missing_file_raises(self, tmp_path: Path) -> None:
        """존재하지 않는 썸네일은 에러."""
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
            set_thumbnail=str(tmp_path / "missing.jpg"),
        )

        with pytest.raises(FileNotFoundError, match="Thumbnail file not found"):
            validate_args(args)

    def test_set_thumbnail_unsupported_format(self, tmp_path: Path) -> None:
        """지원하지 않는 썸네일 확장자."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()
        thumbnail = tmp_path / "cover.gif"
        thumbnail.write_text("gif")

        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            set_thumbnail=str(thumbnail),
        )

        with pytest.raises(ValueError, match="Unsupported thumbnail format"):
            validate_args(args)

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

    def test_validates_lut_path(self, tmp_path: Path) -> None:
        """유효한 LUT 파일 경로 검증."""
        lut_file = tmp_path / "test.cube"
        lut_file.write_text("LUT_3D_SIZE 33\n")
        target = tmp_path / "video.mp4"
        target.touch()

        args = argparse.Namespace(
            targets=[str(target)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            denoise=False,
            denoise_level=None,
            normalize_audio=False,
            group=False,
            no_group=False,
            fade_duration=None,
            upload=False,
            thumbnail=False,
            thumbnail_at=None,
            thumbnail_quality=2,
            detect_silence=False,
            trim_silence=False,
            silence_threshold="-30dB",
            silence_duration=2.0,
            bgm=None,
            bgm_volume=None,
            bgm_loop=False,
            exclude=None,
            include_only=None,
            sort=None,
            reorder=False,
            split_duration=None,
            split_size=None,
            archive_originals=None,
            archive_force=False,
            timelapse=None,
            timelapse_audio=False,
            timelapse_resolution=None,
            lut=str(lut_file),
            auto_lut=None,
            no_auto_lut=False,
            lut_before_hdr=False,
        )
        result = validate_args(args)
        assert result.lut_path is not None
        assert result.lut_path.name == "test.cube"

    def test_lut_nonexistent_file_raises(self) -> None:
        """존재하지 않는 LUT 파일 → FileNotFoundError."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            denoise=False,
            denoise_level=None,
            normalize_audio=False,
            group=False,
            no_group=False,
            fade_duration=None,
            upload=False,
            thumbnail=False,
            thumbnail_at=None,
            thumbnail_quality=2,
            detect_silence=False,
            trim_silence=False,
            silence_threshold="-30dB",
            silence_duration=2.0,
            bgm=None,
            bgm_volume=None,
            bgm_loop=False,
            exclude=None,
            include_only=None,
            sort=None,
            reorder=False,
            split_duration=None,
            split_size=None,
            archive_originals=None,
            archive_force=False,
            timelapse=None,
            timelapse_audio=False,
            timelapse_resolution=None,
            lut="/nonexistent/path/test.cube",
            auto_lut=None,
            no_auto_lut=False,
            lut_before_hdr=False,
        )
        with pytest.raises(FileNotFoundError, match="LUT file not found"):
            validate_args(args)

    def test_lut_invalid_extension_raises(self, tmp_path: Path) -> None:
        """잘못된 LUT 확장자 → ValueError."""
        lut_file = tmp_path / "test.png"
        lut_file.write_text("not a lut\n")

        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            denoise=False,
            denoise_level=None,
            normalize_audio=False,
            group=False,
            no_group=False,
            fade_duration=None,
            upload=False,
            thumbnail=False,
            thumbnail_at=None,
            thumbnail_quality=2,
            detect_silence=False,
            trim_silence=False,
            silence_threshold="-30dB",
            silence_duration=2.0,
            bgm=None,
            bgm_volume=None,
            bgm_loop=False,
            exclude=None,
            include_only=None,
            sort=None,
            reorder=False,
            split_duration=None,
            split_size=None,
            archive_originals=None,
            archive_force=False,
            timelapse=None,
            timelapse_audio=False,
            timelapse_resolution=None,
            lut=str(lut_file),
            auto_lut=None,
            no_auto_lut=False,
            lut_before_hdr=False,
        )
        with pytest.raises(ValueError, match="Unsupported LUT format"):
            validate_args(args)

    def test_auto_lut_flag_sets_true(self) -> None:
        """--auto-lut 플래그가 auto_lut=True로 설정."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            denoise=False,
            denoise_level=None,
            normalize_audio=False,
            group=False,
            no_group=False,
            fade_duration=None,
            upload=False,
            thumbnail=False,
            thumbnail_at=None,
            thumbnail_quality=2,
            detect_silence=False,
            trim_silence=False,
            silence_threshold="-30dB",
            silence_duration=2.0,
            bgm=None,
            bgm_volume=None,
            bgm_loop=False,
            exclude=None,
            include_only=None,
            sort=None,
            reorder=False,
            split_duration=None,
            split_size=None,
            archive_originals=None,
            archive_force=False,
            timelapse=None,
            timelapse_audio=False,
            timelapse_resolution=None,
            lut=None,
            auto_lut=True,
            no_auto_lut=False,
            lut_before_hdr=False,
        )
        result = validate_args(args)
        assert result.auto_lut is True

    def test_no_auto_lut_overrides(self) -> None:
        """--no-auto-lut이 환경변수/config보다 우선."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            denoise=False,
            denoise_level=None,
            normalize_audio=False,
            group=False,
            no_group=False,
            fade_duration=None,
            upload=False,
            thumbnail=False,
            thumbnail_at=None,
            thumbnail_quality=2,
            detect_silence=False,
            trim_silence=False,
            silence_threshold="-30dB",
            silence_duration=2.0,
            bgm=None,
            bgm_volume=None,
            bgm_loop=False,
            exclude=None,
            include_only=None,
            sort=None,
            reorder=False,
            split_duration=None,
            split_size=None,
            archive_originals=None,
            archive_force=False,
            timelapse=None,
            timelapse_audio=False,
            timelapse_resolution=None,
            lut=None,
            auto_lut=None,
            no_auto_lut=True,
            lut_before_hdr=False,
        )
        result = validate_args(args)
        assert result.auto_lut is False

    def test_auto_lut_and_no_auto_lut_both_set(self) -> None:
        """--auto-lut + --no-auto-lut 동시 → --no-auto-lut 우선."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            denoise=False,
            denoise_level=None,
            normalize_audio=False,
            group=False,
            no_group=False,
            fade_duration=None,
            upload=False,
            thumbnail=False,
            thumbnail_at=None,
            thumbnail_quality=2,
            detect_silence=False,
            trim_silence=False,
            silence_threshold="-30dB",
            silence_duration=2.0,
            bgm=None,
            bgm_volume=None,
            bgm_loop=False,
            exclude=None,
            include_only=None,
            sort=None,
            reorder=False,
            split_duration=None,
            split_size=None,
            archive_originals=None,
            archive_force=False,
            timelapse=None,
            timelapse_audio=False,
            timelapse_resolution=None,
            lut=None,
            auto_lut=True,
            no_auto_lut=True,
            lut_before_hdr=False,
        )
        result = validate_args(args)
        assert result.auto_lut is False

    def test_device_luts_passed_through(self) -> None:
        """device_luts 파라미터가 ValidatedArgs에 전달된다."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            denoise=False,
            denoise_level=None,
            normalize_audio=False,
            group=False,
            no_group=False,
            fade_duration=None,
            upload=False,
            thumbnail=False,
            thumbnail_at=None,
            thumbnail_quality=2,
            detect_silence=False,
            trim_silence=False,
            silence_threshold="-30dB",
            silence_duration=2.0,
            bgm=None,
            bgm_volume=None,
            bgm_loop=False,
            exclude=None,
            include_only=None,
            sort=None,
            reorder=False,
            split_duration=None,
            split_size=None,
            archive_originals=None,
            archive_force=False,
            timelapse=None,
            timelapse_audio=False,
            timelapse_resolution=None,
            lut=None,
            auto_lut=None,
            no_auto_lut=False,
            lut_before_hdr=False,
        )
        luts = {"nikon": "/path/to/nikon.cube"}
        result = validate_args(args, device_luts=luts)
        assert result.device_luts == luts


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

    @patch("tubearchive.cli.upload_to_youtube")
    @patch("tubearchive.cli.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.cli.init_database")
    def test_upload_after_pipeline_uses_explicit_thumbnail(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """명시 썸네일이 있으면 업로드에 그대로 전달."""
        from tubearchive.cli import _upload_after_pipeline

        mock_conn = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.close = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_latest.return_value = None

        output_path = tmp_path / "output.mp4"
        output_path.touch()
        thumbnail = tmp_path / "explicit.jpg"
        thumbnail.touch()
        args = argparse.Namespace(
            upload_privacy="unlisted",
            playlist=None,
            upload_chunk=32,
        )

        with patch("tubearchive.cli.MergeJobRepository", return_value=mock_repo):
            _upload_after_pipeline(
                output_path,
                args,
                generated_thumbnail_paths=None,
                explicit_thumbnail=thumbnail,
            )

        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["thumbnail"] == thumbnail

    @patch("tubearchive.cli.upload_to_youtube")
    @patch("tubearchive.cli.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.cli.init_database")
    def test_upload_after_pipeline_uses_single_generated_thumbnail(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """생성 썸네일 1개는 자동 선택."""
        from tubearchive.cli import _upload_after_pipeline

        mock_conn = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.close = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_latest.return_value = None

        output_path = tmp_path / "output.mp4"
        output_path.touch()
        generated = tmp_path / "generated.jpg"
        generated.touch()
        args = argparse.Namespace(
            upload_privacy="unlisted",
            playlist=None,
            upload_chunk=32,
        )

        with patch("tubearchive.cli.MergeJobRepository", return_value=mock_repo):
            _upload_after_pipeline(
                output_path,
                args,
                generated_thumbnail_paths=[generated],
            )

        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["thumbnail"] == generated


class TestResolveUploadThumbnail:
    """썸네일 업로드 후보 결정 테스트."""

    def test_resolve_upload_thumbnail_uses_explicit(self, tmp_path: Path) -> None:
        """명시 썸네일이 우선."""
        from tubearchive.cli import _resolve_upload_thumbnail

        explicit = tmp_path / "a.jpg"
        generated = [tmp_path / "b.jpg"]

        assert _resolve_upload_thumbnail(explicit, generated) is explicit

    def test_resolve_upload_thumbnail_single_generated(self, tmp_path: Path) -> None:
        """자동 생성 썸네일 1개는 해당 경로 사용."""
        from tubearchive.cli import _resolve_upload_thumbnail

        generated = [tmp_path / "auto.jpg"]
        generated[0].touch()

        assert _resolve_upload_thumbnail(None, generated) is generated[0]

    @patch("tubearchive.cli._interactive_select", return_value=1)
    def test_resolve_upload_thumbnail_selects_from_multiple(
        self,
        _mock_select: MagicMock,
        tmp_path: Path,
    ) -> None:
        """썸네일이 여러 개면 인터랙티브 선택 결과 사용."""
        from tubearchive.cli import _resolve_upload_thumbnail

        generated = [tmp_path / "auto1.jpg", tmp_path / "auto2.jpg"]
        for path in generated:
            path.touch()

        assert _resolve_upload_thumbnail(None, generated) is generated[1]


class TestUploadSplitFiles:
    """_upload_split_files 분할 업로드 테스트."""

    @patch("tubearchive.cli.upload_to_youtube")
    @patch("tubearchive.cli.probe_duration", return_value=3600.0)
    def test_uploads_each_split_file(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """분할 파일 각각에 대해 upload_to_youtube가 호출된다."""
        from tubearchive.cli import _upload_split_files

        f1 = tmp_path / "video_001.mp4"
        f2 = tmp_path / "video_002.mp4"
        f1.touch()
        f2.touch()

        clips_json = (
            '[{"name":"A.mp4","duration":3600,"start":0,"end":3600,"device":"Nikon","shot_time":"10:00"},'
            '{"name":"B.mp4","duration":3600,"start":3600,"end":7200,"device":"GoPro","shot_time":"11:00"}]'
        )

        _upload_split_files(
            split_files=[f1, f2],
            title="Test",
            clips_info_json=clips_json,
            privacy="unlisted",
            merge_job_id=1,
            playlist_ids=None,
            chunk_mb=32,
        )

        assert mock_upload.call_count == 2

    @patch("tubearchive.cli.upload_to_youtube")
    @patch("tubearchive.cli.probe_duration", return_value=3600.0)
    def test_title_includes_part_numbers(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """제목에 (Part N/M) 형식이 포함된다."""
        from tubearchive.cli import _upload_split_files

        f1 = tmp_path / "video_001.mp4"
        f2 = tmp_path / "video_002.mp4"
        f1.touch()
        f2.touch()

        clips_json = (
            '[{"name":"A.mp4","duration":7200,"start":0,"end":7200,"device":null,"shot_time":null}]'
        )

        _upload_split_files(
            split_files=[f1, f2],
            title="MyVideo",
            clips_info_json=clips_json,
            privacy="unlisted",
            merge_job_id=1,
            playlist_ids=None,
            chunk_mb=None,
        )

        first_call_title = mock_upload.call_args_list[0][1]["title"]
        second_call_title = mock_upload.call_args_list[1][1]["title"]
        assert "(Part 1/2)" in first_call_title
        assert "(Part 2/2)" in second_call_title

    @patch("tubearchive.cli.upload_to_youtube")
    @patch("tubearchive.cli.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.cli.init_database")
    def test_falls_back_when_no_split_files(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """분할 파일이 없으면 단일 파일 업로드로 폴백한다."""
        from tubearchive.cli import _upload_after_pipeline

        mock_conn = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.close = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_latest.return_value = MagicMock(
            id=1,
            title="Video",
            summary_markdown="desc",
            clips_info_json=None,
        )

        mock_split_repo = MagicMock()
        mock_split_repo.get_by_merge_job_id.return_value = []

        output_path = tmp_path / "output.mp4"
        output_path.touch()
        args = argparse.Namespace(
            upload_privacy="unlisted",
            playlist=None,
            upload_chunk=32,
        )

        with (
            patch("tubearchive.cli.MergeJobRepository", return_value=mock_repo),
            patch("tubearchive.cli.SplitJobRepository", return_value=mock_split_repo),
        ):
            _upload_after_pipeline(output_path, args)

        # 단일 파일로 업로드
        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["file_path"] == output_path

    @patch("tubearchive.cli.upload_to_youtube")
    @patch("tubearchive.cli.probe_duration", return_value=3600.0)
    @patch("tubearchive.cli.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.cli.init_database")
    def test_uploads_split_files_when_present(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """분할 파일이 DB에 있고 디스크에 존재하면 분할 파일을 업로드한다."""
        from tubearchive.cli import _upload_after_pipeline

        mock_conn = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.close = MagicMock()

        f1 = tmp_path / "video_001.mp4"
        f2 = tmp_path / "video_002.mp4"
        f1.touch()
        f2.touch()

        clips_json = (
            '[{"name":"A.mp4","duration":3600,"start":0,"end":3600,"device":null,"shot_time":null}]'
        )

        mock_repo = MagicMock()
        mock_repo.get_latest.return_value = MagicMock(
            id=1,
            title="Video",
            summary_markdown="desc",
            clips_info_json=clips_json,
        )

        mock_split_job = MagicMock()
        mock_split_job.output_files = [f1, f2]

        mock_split_repo = MagicMock()
        mock_split_repo.get_by_merge_job_id.return_value = [mock_split_job]

        output_path = tmp_path / "output.mp4"
        output_path.touch()
        args = argparse.Namespace(
            upload_privacy="unlisted",
            playlist=None,
            upload_chunk=32,
        )

        with (
            patch("tubearchive.cli.MergeJobRepository", return_value=mock_repo),
            patch("tubearchive.cli.SplitJobRepository", return_value=mock_split_repo),
        ):
            _upload_after_pipeline(output_path, args)

        # 분할 파일 2개가 업로드됨
        assert mock_upload.call_count == 2

    @patch("tubearchive.cli.upload_to_youtube")
    @patch("tubearchive.cli.probe_duration", return_value=60.0)
    def test_malformed_clips_json_does_not_crash(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """잘못된 clips_info_json이어도 업로드가 진행된다."""
        from tubearchive.cli import _upload_split_files

        f1 = tmp_path / "video_001.mp4"
        f1.touch()

        _upload_split_files(
            split_files=[f1],
            title="Test",
            clips_info_json="not valid json {{{",
            privacy="unlisted",
            merge_job_id=1,
            playlist_ids=None,
            chunk_mb=None,
        )

        assert mock_upload.call_count == 1

    @patch("tubearchive.cli.upload_to_youtube")
    @patch("tubearchive.cli.probe_duration", return_value=60.0)
    def test_none_clips_json_does_not_crash(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """clips_info_json이 None이어도 업로드가 진행된다."""
        from tubearchive.cli import _upload_split_files

        f1 = tmp_path / "video_001.mp4"
        f1.touch()

        _upload_split_files(
            split_files=[f1],
            title="Test",
            clips_info_json=None,
            privacy="unlisted",
            merge_job_id=1,
            playlist_ids=None,
            chunk_mb=None,
        )

        assert mock_upload.call_count == 1

    @patch("tubearchive.cli.upload_to_youtube")
    @patch("tubearchive.cli.probe_duration", return_value=3600.0)
    def test_partial_upload_failure_continues(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """한 파트 업로드 실패 시 나머지 파트는 계속 업로드한다."""
        from tubearchive.cli import _upload_split_files

        f1 = tmp_path / "video_001.mp4"
        f2 = tmp_path / "video_002.mp4"
        f3 = tmp_path / "video_003.mp4"
        f1.touch()
        f2.touch()
        f3.touch()

        # 두 번째 호출만 실패
        mock_upload.side_effect = [None, Exception("network error"), None]

        clips_json = (
            '[{"name":"A.mp4","duration":10800,"start":0,"end":10800,'
            '"device":null,"shot_time":null}]'
        )

        _upload_split_files(
            split_files=[f1, f2, f3],
            title="Test",
            clips_info_json=clips_json,
            privacy="unlisted",
            merge_job_id=1,
            playlist_ids=None,
            chunk_mb=None,
        )

        # 3번 모두 시도 (2번째 실패해도 3번째 진행)
        assert mock_upload.call_count == 3


class TestTruncatePath:
    """truncate_path 유틸리티 테스트."""

    def test_short_path_unchanged(self) -> None:
        """max_len 이하 경로는 그대로 반환."""
        assert truncate_path("/short/path", max_len=40) == "/short/path"

    def test_exact_length_unchanged(self) -> None:
        """max_len과 정확히 같은 길이는 그대로 반환."""
        path = "x" * 40
        assert truncate_path(path, max_len=40) == path

    def test_long_path_truncated(self) -> None:
        """max_len 초과 경로는 '...' 접두사로 말줄임."""
        path = "/very/long/path/that/exceeds/the/maximum/length/limit.mp4"
        result = truncate_path(path, max_len=30)
        assert result.startswith("...")
        assert len(result) == 30

    def test_custom_max_len(self) -> None:
        """다양한 max_len 값에서 정상 동작."""
        path = "a" * 50
        result = truncate_path(path, max_len=20)
        assert len(result) == 20
        assert result == "..." + "a" * 17

    def test_empty_string(self) -> None:
        """빈 문자열은 그대로 반환."""
        assert truncate_path("", max_len=40) == ""


class TestTranscodeOptions:
    """TranscodeOptions 데이터클래스 테스트."""

    def test_default_values(self) -> None:
        """기본값이 올바르게 설정되는지 확인."""
        opts = TranscodeOptions()
        assert opts.denoise is False
        assert opts.denoise_level == "medium"
        assert opts.normalize_audio is False
        assert opts.fade_map is None
        assert opts.fade_duration == 0.5

    def test_custom_values(self) -> None:
        """커스텀 값이 정상 할당되는지 확인."""
        from tubearchive.models.video import FadeConfig

        fade_map = {Path("/a.mp4"): FadeConfig(fade_in=0.3, fade_out=0.7)}
        opts = TranscodeOptions(
            denoise=True,
            denoise_level="heavy",
            normalize_audio=True,
            fade_map=fade_map,
            fade_duration=1.0,
        )
        assert opts.denoise is True
        assert opts.denoise_level == "heavy"
        assert opts.normalize_audio is True
        assert opts.fade_map is not None
        assert opts.fade_duration == 1.0

    def test_frozen_immutable(self) -> None:
        """frozen=True이므로 필드 변경 시 에러 발생."""
        opts = TranscodeOptions()
        with pytest.raises(AttributeError):
            opts.denoise = True  # type: ignore[misc]

    def test_lut_default_values(self) -> None:
        """LUT 관련 기본값 확인."""
        opts = TranscodeOptions()
        assert opts.lut_path is None
        assert opts.auto_lut is False
        assert opts.lut_before_hdr is False
        assert opts.device_luts is None

    def test_lut_custom_values(self) -> None:
        """LUT 관련 커스텀 값 확인."""
        device_luts = {"nikon": "/path/to/nikon.cube"}
        opts = TranscodeOptions(
            lut_path="/path/to/lut.cube",
            auto_lut=True,
            lut_before_hdr=True,
            device_luts=device_luts,
        )
        assert opts.lut_path == "/path/to/lut.cube"
        assert opts.auto_lut is True
        assert opts.lut_before_hdr is True
        assert opts.device_luts == device_luts


class TestDatabaseSession:
    """database_session context manager 테스트."""

    @patch("tubearchive.cli.init_database")
    def test_yields_connection(self, mock_init: MagicMock) -> None:
        """context manager가 DB 연결 객체를 yield한다."""
        mock_conn = MagicMock()
        mock_init.return_value = mock_conn

        with database_session() as conn:
            assert conn is mock_conn

    @patch("tubearchive.cli.init_database")
    def test_closes_connection_on_exit(self, mock_init: MagicMock) -> None:
        """블록 종료 시 DB 연결이 닫힌다."""
        mock_conn = MagicMock()
        mock_init.return_value = mock_conn

        with database_session():
            mock_conn.close.assert_not_called()

        mock_conn.close.assert_called_once()

    @patch("tubearchive.cli.init_database")
    def test_closes_connection_on_exception(self, mock_init: MagicMock) -> None:
        """예외 발생 시에도 DB 연결이 닫힌다."""
        mock_conn = MagicMock()
        mock_init.return_value = mock_conn

        with pytest.raises(ValueError), database_session():
            raise ValueError("test error")

        mock_conn.close.assert_called_once()


class TestClipInfo:
    """ClipInfo NamedTuple 테스트."""

    def test_creation(self) -> None:
        """기본 생성과 필드 접근."""
        info = ClipInfo(
            name="test.mp4",
            duration=120.5,
            device="Nikon Z6III",
            shot_time="14:30:00",
        )
        assert info.name == "test.mp4"
        assert info.duration == 120.5
        assert info.device == "Nikon Z6III"
        assert info.shot_time == "14:30:00"

    def test_optional_fields(self) -> None:
        """device와 shot_time은 None 허용."""
        info = ClipInfo(name="test.mp4", duration=0.0, device=None, shot_time=None)
        assert info.device is None
        assert info.shot_time is None

    def test_tuple_unpacking(self) -> None:
        """기존 tuple 언패킹과 동일하게 동작한다."""
        info = ClipInfo(name="clip.mov", duration=60.0, device="GoPro", shot_time="10:00:00")
        name, duration, device, shot_time = info
        assert name == "clip.mov"
        assert duration == 60.0
        assert device == "GoPro"
        assert shot_time == "10:00:00"

    def test_immutable(self) -> None:
        """NamedTuple이므로 필드 변경 불가."""
        info = ClipInfo(name="a.mp4", duration=1.0, device=None, shot_time=None)
        with pytest.raises(AttributeError):
            info.name = "b.mp4"  # type: ignore[misc]


class TestSaveMergeJobToDb:
    """save_merge_job_to_db 반환값 테스트."""

    @patch("tubearchive.cli.database_session")
    def test_returns_summary_and_merge_job_id(
        self,
        mock_db_session: MagicMock,
        tmp_path: Path,
    ) -> None:
        """summary와 merge_job_id를 tuple로 반환한다."""
        from tubearchive.cli import save_merge_job_to_db

        output_file = tmp_path / "output.mp4"
        output_file.write_bytes(b"\x00" * 100)

        mock_conn = MagicMock()
        mock_repo = MagicMock()
        mock_repo.create.return_value = 42
        mock_db_session.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        clips = [
            ClipInfo(name="a.mp4", duration=10.0, device="Nikon", shot_time="10:00"),
        ]

        with (
            patch("tubearchive.cli.MergeJobRepository", return_value=mock_repo),
            patch(
                "tubearchive.utils.summary_generator.generate_clip_summary",
                return_value="## Summary",
            ),
            patch(
                "tubearchive.utils.summary_generator.generate_youtube_description",
                return_value="desc",
            ),
        ):
            result = save_merge_job_to_db(output_file, clips, [tmp_path], [1])

        assert isinstance(result, tuple)
        assert len(result) == 2
        summary, merge_job_id = result
        assert summary == "## Summary"
        assert merge_job_id == 42

    @patch("tubearchive.cli.database_session")
    def test_returns_none_tuple_on_failure(
        self,
        mock_db_session: MagicMock,
        tmp_path: Path,
    ) -> None:
        """DB 저장 실패 시 (None, None)을 반환한다."""
        from tubearchive.cli import save_merge_job_to_db

        mock_db_session.return_value.__enter__ = MagicMock(side_effect=Exception("DB error"))
        mock_db_session.return_value.__exit__ = MagicMock(return_value=False)

        clips = [
            ClipInfo(name="a.mp4", duration=10.0, device=None, shot_time=None),
        ]

        result = save_merge_job_to_db(tmp_path / "out.mp4", clips, [tmp_path], [1])
        assert result == (None, None)


class TestStabilizeCLI:
    """영상 안정화 CLI 인자 테스트."""

    def test_stabilize_flag_parsed(self) -> None:
        """--stabilize 플래그 파싱."""
        parser = create_parser()
        args = parser.parse_args(["--stabilize", "/tmp"])
        assert args.stabilize is True

    def test_stabilize_strength_parsed(self) -> None:
        """--stabilize-strength 파싱."""
        parser = create_parser()
        args = parser.parse_args(["--stabilize-strength", "heavy", "/tmp"])
        assert args.stabilize_strength == "heavy"

    def test_stabilize_crop_parsed(self) -> None:
        """--stabilize-crop 파싱."""
        parser = create_parser()
        args = parser.parse_args(["--stabilize-crop", "expand", "/tmp"])
        assert args.stabilize_crop == "expand"

    def test_stabilize_strength_choices(self) -> None:
        """--stabilize-strength 유효 선택지만 허용."""
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--stabilize-strength", "extreme", "/tmp"])

    def test_stabilize_crop_choices(self) -> None:
        """--stabilize-crop 유효 선택지만 허용."""
        parser = create_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--stabilize-crop", "zoom", "/tmp"])

    def test_stabilize_flag_enables_in_validate_args(self, tmp_path: Path) -> None:
        """--stabilize → ValidatedArgs.stabilize=True."""
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
            stabilize=True,
            stabilize_strength=None,
            stabilize_crop=None,
        )

        result = validate_args(args)

        assert result.stabilize is True
        assert result.stabilize_strength == "medium"  # 기본값
        assert result.stabilize_crop == "crop"  # 기본값

    def test_strength_implicit_activation(self, tmp_path: Path) -> None:
        """--stabilize-strength만 지정 시 stabilize 암묵적 활성화."""
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
            stabilize=False,
            stabilize_strength="heavy",
            stabilize_crop=None,
        )

        result = validate_args(args)

        assert result.stabilize is True
        assert result.stabilize_strength == "heavy"

    def test_crop_implicit_activation(self, tmp_path: Path) -> None:
        """--stabilize-crop만 지정 시 stabilize 암묵적 활성화."""
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
            stabilize=False,
            stabilize_strength=None,
            stabilize_crop="expand",
        )

        result = validate_args(args)

        assert result.stabilize is True
        assert result.stabilize_crop == "expand"

    def test_env_stabilize_enables(self, tmp_path: Path) -> None:
        """환경변수 TUBEARCHIVE_STABILIZE=true로 활성화."""
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
            stabilize=False,
            stabilize_strength=None,
            stabilize_crop=None,
        )

        with patch.dict("os.environ", {"TUBEARCHIVE_STABILIZE": "true"}):
            result = validate_args(args)

        assert result.stabilize is True
        assert result.stabilize_strength == "medium"

    def test_cli_overrides_env(self, tmp_path: Path) -> None:
        """CLI 인자가 환경변수를 오버라이드."""
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
            stabilize=True,
            stabilize_strength="heavy",
            stabilize_crop="expand",
        )

        with patch.dict(
            "os.environ",
            {
                "TUBEARCHIVE_STABILIZE_STRENGTH": "light",
                "TUBEARCHIVE_STABILIZE_CROP": "crop",
            },
        ):
            result = validate_args(args)

        assert result.stabilize_strength == "heavy"
        assert result.stabilize_crop == "expand"

    def test_transcode_options_contains_stabilize(self) -> None:
        """TranscodeOptions에 stabilize 필드가 있다."""
        opts = TranscodeOptions(
            stabilize=True,
            stabilize_strength="heavy",
            stabilize_crop="expand",
        )
        assert opts.stabilize is True
        assert opts.stabilize_strength == "heavy"
        assert opts.stabilize_crop == "expand"
