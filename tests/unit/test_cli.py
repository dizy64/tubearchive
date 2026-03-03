"""CLI 인터페이스 테스트."""

import argparse
import os
import signal
import threading
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tubearchive.app.cli.main import (
    CATALOG_STATUS_SENTINEL,
    ClipInfo,
    TranscodeOptions,
    _make_watermark_text,
    _run_watch_mode,
    create_parser,
    database_session,
    main,
    validate_args,
)
from tubearchive.config import (
    ENV_TEMPLATE_INTRO,
    ENV_TEMPLATE_OUTRO,
    ENV_WATCH_LOG,
    ENV_WATCH_PATHS,
    ENV_WATCH_POLL_INTERVAL,
    ENV_WATCH_STABILITY_CHECKS,
    AppConfig,
    HooksConfig,
)
from tubearchive.domain.models.video import VideoFile, VideoMetadata
from tubearchive.shared import truncate_path
from tubearchive.shared.validators import ValidationError


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

    def test_parses_watch_paths(self) -> None:
        """--watch는 반복 지정 가능."""
        parser = create_parser()
        args = parser.parse_args(["--watch", "/tmp/inbox", "--watch", "/tmp/archive"])

        assert args.watch == ["/tmp/inbox", "/tmp/archive"]

    def test_parses_watch_log(self) -> None:
        """--watch-log 경로."""
        parser = create_parser()
        args = parser.parse_args(["--watch-log", "/tmp/watch.log"])

        assert args.watch_log == "/tmp/watch.log"

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

    def test_parses_subtitle_flag(self) -> None:
        """--subtitle 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--subtitle"])

        assert args.subtitle is True

    def test_parses_subtitle_model(self) -> None:
        """--subtitle-model 옵션."""
        parser = create_parser()
        args = parser.parse_args(["--subtitle-model", "base"])

        assert args.subtitle_model == "base"

    def test_parses_subtitle_format(self) -> None:
        """--subtitle-format 옵션."""
        parser = create_parser()
        args = parser.parse_args(["--subtitle-format", "vtt"])

        assert args.subtitle_format == "vtt"

    def test_parses_subtitle_lang_and_burn(self) -> None:
        """자막 언어/하드코딩 옵션 파싱."""
        parser = create_parser()
        args = parser.parse_args(["--subtitle-lang", "EN", "--subtitle-burn"])

        assert args.subtitle_lang == "EN"
        assert args.subtitle_burn is True

    def test_parses_quality_report_flag(self) -> None:
        """--quality-report 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--quality-report"])

        assert args.quality_report is True

    def test_quality_report_default_is_false(self) -> None:
        """--quality-report 미지정 시 False."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.quality_report is False

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

    def test_parses_template_intro_legacy(self) -> None:
        """--template-intro 파싱."""
        parser = create_parser()
        args = parser.parse_args(["--template-intro", "/tmp/intro.mov"])

        assert args.template_intro == "/tmp/intro.mov"

    def test_template_intro_default_is_none_legacy(self) -> None:
        """--template-intro 미지정 시 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.template_intro is None

    def test_parses_template_outro_legacy(self) -> None:
        """--template-outro 파싱."""
        parser = create_parser()
        args = parser.parse_args(["--template-outro", "/tmp/outro.mov"])

        assert args.template_outro == "/tmp/outro.mov"

    def test_template_outro_default_is_none_legacy(self) -> None:
        """--template-outro 미지정 시 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.template_outro is None

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

    def test_parses_run_hook_option(self) -> None:
        """--run-hook 옵션이 이벤트명으로 파싱된다."""
        parser = create_parser()
        args = parser.parse_args(["--run-hook", "on_merge"])

        assert args.run_hook == "on_merge"

    def test_run_hook_invalid_value_raises(self) -> None:
        """알 수 없는 --run-hook 값은 argparse에서 거부한다."""
        parser = create_parser()

        with pytest.raises(SystemExit):
            parser.parse_args(["--run-hook", "invalid"])

    def test_parses_template_intro(self) -> None:
        """--template-intro 옵션."""
        parser = create_parser()
        args = parser.parse_args(["--template-intro", "/path/to/intro.mp4"])

        assert args.template_intro == "/path/to/intro.mp4"

    def test_template_intro_default_is_none(self) -> None:
        """--template-intro 미지정 시 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.template_intro is None

    def test_parses_template_outro(self) -> None:
        """--template-outro 옵션."""
        parser = create_parser()
        args = parser.parse_args(["--template-outro", "/path/to/outro.mp4"])

        assert args.template_outro == "/path/to/outro.mp4"

    def test_template_outro_default_is_none(self) -> None:
        """--template-outro 미지정 시 None."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.template_outro is None

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

    def test_parses_watermark_flag(self) -> None:
        """--watermark 플래그."""
        parser = create_parser()
        args = parser.parse_args(["--watermark"])

        assert args.watermark is True

    def test_parses_watermark_options(self) -> None:
        """워터마크 옵션 값."""
        parser = create_parser()
        args = parser.parse_args(
            [
                "--watermark",
                "--watermark-pos",
                "top-left",
                "--watermark-size",
                "36",
                "--watermark-color",
                "yellow",
                "--watermark-alpha",
                "0.6",
            ]
        )

        assert args.watermark is True
        assert args.watermark_pos == "top-left"
        assert args.watermark_size == 36
        assert args.watermark_color == "yellow"
        assert args.watermark_alpha == 0.6

    def test_watermark_defaults(self) -> None:
        """--watermark 기본값."""
        parser = create_parser()
        args = parser.parse_args([])

        assert args.watermark is False
        assert args.watermark_pos == "bottom-right"
        assert args.watermark_size == 48
        assert args.watermark_color == "white"
        assert args.watermark_alpha == 0.85


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

    def test_validates_with_custom_hooks(self, tmp_path: Path) -> None:
        """validate_args에서 HooksConfig가 전달되면 유지된다."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()

        hooks = HooksConfig(on_merge=("echo merged",), on_error=("echo err",), timeout_sec=90)
        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
        )

        result = validate_args(args, hooks=hooks)

        assert result.hooks == hooks

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

    def test_template_intro_path_legacy(self, tmp_path: Path) -> None:
        """템플릿 intro 경로를 Path로 변환한다."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()
        intro = tmp_path / "intro.mp4"
        intro.write_text("intro")

        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            template_intro=str(intro),
        )

        result = validate_args(args)
        assert result.template_intro == intro.resolve()

    def test_template_outro_path_legacy(self, tmp_path: Path) -> None:
        """템플릿 outro 경로를 Path로 변환한다."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()
        outro = tmp_path / "outro.mp4"
        outro.write_text("outro")

        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            template_outro=str(outro),
        )

        result = validate_args(args)
        assert result.template_outro == outro.resolve()

    def test_template_intro_cli_precedence_legacy(self, tmp_path: Path) -> None:
        """CLI로 지정한 템플릿이 환경변수보다 우선한다."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()

        cli_intro = tmp_path / "cli_intro.mp4"
        cli_intro.write_text("cli")
        env_intro = tmp_path / "env_intro.mp4"
        env_intro.write_text("env")

        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            template_intro=str(cli_intro),
        )

        with patch.dict("os.environ", {"TUBEARCHIVE_TEMPLATE_INTRO": str(env_intro)}):
            result = validate_args(args)

        assert result.template_intro == cli_intro.resolve()

    def test_template_outro_from_env(self, tmp_path: Path) -> None:
        """템플릿 outro는 환경변수 기본값을 적용한다."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()
        env_outro = tmp_path / "env_outro.mp4"
        env_outro.write_text("env")

        args = argparse.Namespace(
            targets=[str(video_file)],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
        )

        with patch.dict("os.environ", {"TUBEARCHIVE_TEMPLATE_OUTRO": str(env_outro)}):
            result = validate_args(args)

        assert result.template_outro == env_outro.resolve()

    def test_template_path_not_found_raises(self, tmp_path: Path) -> None:
        """존재하지 않는 템플릿 경로는 FileNotFoundError."""
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
            template_intro=str(tmp_path / "missing_intro.mp4"),
            template_outro=str(tmp_path / "missing_outro.mp4"),
        )

        with pytest.raises(FileNotFoundError):
            validate_args(args)

    def test_default_subtitle_options(self) -> None:
        """자막 기본값이 ValidatedArgs에 반영된다."""
        env_snapshot = {
            k: os.environ.get(k)
            for k in (
                "TUBEARCHIVE_SUBTITLE_MODEL",
                "TUBEARCHIVE_SUBTITLE_FORMAT",
                "TUBEARCHIVE_SUBTITLE_LANG",
                "TUBEARCHIVE_SUBTITLE_BURN",
            )
        }
        for key in env_snapshot:
            os.environ.pop(key, None)
        try:
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

            assert result.subtitle is False
            assert result.subtitle_model == "tiny"
            assert result.subtitle_format == "srt"
            assert result.subtitle_lang is None
            assert result.subtitle_burn is False
        finally:
            for key, value in env_snapshot.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_custom_subtitle_options_are_normalized(self) -> None:
        """자막 사용자 옵션이 전달되며 언어는 소문자 정규화."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            subtitle=True,
            subtitle_model="base",
            subtitle_format="vtt",
            subtitle_lang="EN",
            subtitle_burn=True,
        )

        result = validate_args(args)

        assert result.subtitle is True
        assert result.subtitle_model == "base"
        assert result.subtitle_format == "vtt"
        assert result.subtitle_lang == "en"
        assert result.subtitle_burn is True

    def test_rejects_invalid_subtitle_model(self) -> None:
        """지원하지 않는 자막 모델은 에러."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            subtitle=True,
            subtitle_model="invalid",
        )

        with pytest.raises(ValidationError, match="Invalid subtitle model"):
            validate_args(args)

    def test_rejects_invalid_subtitle_format(self) -> None:
        """지원하지 않는 자막 포맷은 에러."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            subtitle=True,
            subtitle_format="invalid",
        )

        with pytest.raises(ValidationError, match="Invalid subtitle format"):
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

    def test_watch_paths_from_cli(self, tmp_path: Path) -> None:
        """--watch 경로는 watch 모드로 해석."""
        watch_dir_1 = tmp_path / "watch1"
        watch_dir_1.mkdir()
        watch_dir_2 = tmp_path / "watch2"
        watch_dir_2.mkdir()

        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            watch=[str(watch_dir_1), str(watch_dir_2)],
        )

        result = validate_args(args)

        assert result.watch is True
        assert result.watch_paths == [watch_dir_1, watch_dir_2]
        assert result.watch_poll_interval == 1.0
        assert result.watch_stability_checks == 2
        assert result.watch_log is None

    def test_watch_paths_from_env(self, tmp_path: Path) -> None:
        """watch 경로 미설정 시 env 기본값 사용."""
        watch_dir_1 = tmp_path / "env_watch1"
        watch_dir_1.mkdir()
        watch_dir_2 = tmp_path / "env_watch2"
        watch_dir_2.mkdir()
        watch_log = tmp_path / "watch.log"

        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
        )

        with patch.dict(
            "os.environ",
            {
                ENV_WATCH_PATHS: f"{watch_dir_1},{watch_dir_2}",
                ENV_WATCH_POLL_INTERVAL: "1.5",
                ENV_WATCH_STABILITY_CHECKS: "4",
                ENV_WATCH_LOG: str(watch_log),
            },
        ):
            result = validate_args(args)

        assert result.watch is True
        assert result.watch_paths == [watch_dir_1, watch_dir_2]
        assert result.watch_poll_interval == 1.5
        assert result.watch_stability_checks == 4
        assert result.watch_log == watch_log

    def test_watch_path_missing_raises(self, tmp_path: Path) -> None:
        """watch 경로가 존재하지 않으면 FileNotFoundError."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            watch=[str(tmp_path / "missing_watch_dir")],
        )

        with pytest.raises(FileNotFoundError, match="Watch path not found"):
            validate_args(args)

    def test_watch_defaults_when_not_configured(self) -> None:
        """watch 설정 없음 시 비활성화 및 기본 값 반환."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
        )

        with patch.dict(os.environ, {}, clear=True):
            result = validate_args(args)

        assert result.watch is False
        assert result.watch_paths == []
        assert result.watch_poll_interval == 1.0
        assert result.watch_stability_checks == 2
        assert result.watch_log is None

    def test_watch_mode_reload_uses_updated_hook_config(self, tmp_path: Path) -> None:
        """SIGHUP 시 재로딩된 config의 hook 설정을 사용."""
        if not hasattr(signal, "SIGHUP"):
            pytest.skip("SIGHUP is not supported on this platform.")

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        args = create_parser().parse_args(["--watch", str(watch_dir)])
        baseline_args = replace(validate_args(args), watch_poll_interval=0.01)
        initial_hooks = HooksConfig(on_merge=("echo-initial",))
        reloaded_hooks = HooksConfig(on_merge=("echo-reloaded",))
        validated_args = replace(
            baseline_args,
            watch=True,
            watch_paths=[watch_dir],
            hooks=initial_hooks,
        )

        signal_handlers: dict[int, object] = {}

        class _DummyObserver:
            def stop(self) -> None:
                pass

            def join(self) -> None:
                pass

        captured_calls: list[HooksConfig] = []

        def _register_signal(signum: int, handler: object) -> object:
            signal_handlers[signum] = handler
            return object()

        def _capture_validate_args(
            parsed_args: argparse.Namespace,
            device_luts: dict[str, str] | None = None,
            hooks: HooksConfig | None = None,
        ) -> object:
            # reload 시점에 validate_args로 들어오는 hook 설정을 기록
            if hooks is not None:
                captured_calls.append(hooks)
            return replace(validated_args, hooks=hooks)

        with (
            patch("tubearchive.app.cli.main.signal.signal", side_effect=_register_signal),
            patch(
                "tubearchive.app.cli.main._setup_file_observer",
                return_value=(_DummyObserver(), object()),
            ),
            patch(
                "tubearchive.app.cli.main.load_config", return_value=AppConfig(hooks=reloaded_hooks)
            ),
            patch("tubearchive.app.cli.main.validate_args", side_effect=_capture_validate_args),
        ):
            watch_thread = threading.Thread(
                target=_run_watch_mode,
                args=(args, validated_args),
                kwargs={
                    "config_path": tmp_path / "config.toml",
                    "hooks": initial_hooks,
                    "verbose": False,
                },
            )
            watch_thread.start()

            for _ in range(100):
                if signal.SIGINT in signal_handlers and signal.SIGHUP in signal_handlers:
                    break
                time.sleep(0.01)

            assert signal.SIGINT in signal_handlers, "SIGINT handler not registered"
            assert signal.SIGHUP in signal_handlers, "SIGHUP handler not registered"

            signal_handlers[signal.SIGHUP](signal.SIGHUP, None)

            for _ in range(100):
                if captured_calls:
                    break
                time.sleep(0.01)

            assert captured_calls
            assert captured_calls[0] == reloaded_hooks
            signal_handlers[signal.SIGINT](signal.SIGINT, None)
            watch_thread.join(timeout=2.0)

        assert not watch_thread.is_alive()

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

    def test_quality_report_default_is_false(self) -> None:
        """--quality-report 미지정 시 False."""
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

        assert result.quality_report is False

    def test_quality_report_true_enables_reporting(self) -> None:
        """--quality-report True가 전달되면 ValidatedArgs에 반영."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            quality_report=True,
        )

        result = validate_args(args)

        assert result.quality_report is True

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

    def test_watermark_defaults(self, tmp_path: Path) -> None:
        """워터마크 기본값은 False/기본값 유지."""
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

        assert result.watermark is False
        assert result.watermark_pos == "bottom-right"
        assert result.watermark_size == 48
        assert result.watermark_color == "white"
        assert result.watermark_alpha == 0.85

    def test_watermark_options(self, tmp_path: Path) -> None:
        """워터마크 인자 값이 ValidatedArgs에 반영."""
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
            watermark=True,
            watermark_pos="top-left",
            watermark_size=36,
            watermark_color="yellow",
            watermark_alpha=0.6,
        )

        result = validate_args(args)

        assert result.watermark is True
        assert result.watermark_pos == "top-left"
        assert result.watermark_size == 36
        assert result.watermark_color == "yellow"
        assert result.watermark_alpha == 0.6

    def test_watermark_invalid_size_raises(self, tmp_path: Path) -> None:
        """워터마크 크기 0 이하면 ValueError."""
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
            watermark=True,
            watermark_pos="bottom-right",
            watermark_size=0,
            watermark_color="white",
            watermark_alpha=0.8,
        )

        with pytest.raises(ValueError, match="Watermark size must be > 0"):
            validate_args(args)

    def test_watermark_invalid_alpha_raises(self, tmp_path: Path) -> None:
        """워터마크 투명도 범위 초과 시 ValueError."""
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
            watermark=True,
            watermark_pos="bottom-right",
            watermark_size=24,
            watermark_color="white",
            watermark_alpha=1.2,
        )

        with pytest.raises(ValueError, match="Watermark alpha must be in"):
            validate_args(args)

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

    def test_template_intro_path(self, tmp_path: Path) -> None:
        """--template-intro는 존재 파일만 수용."""
        template = tmp_path / "intro.mov"
        template.touch()

        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            template_intro=str(template),
            template_outro=None,
        )

        result = validate_args(args)
        assert result.template_intro == template

    def test_template_intro_path_missing_raises(self, tmp_path: Path) -> None:
        """없는 template 경로는 FileNotFoundError."""
        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            template_intro=str(tmp_path / "missing.mov"),
            template_outro=None,
        )

        with pytest.raises(FileNotFoundError, match="Template file not found"):
            validate_args(args)

    def test_template_outro_env_fallback(self, tmp_path: Path) -> None:
        """템플릿 아웃트로는 env/config 기본값을 따름."""
        template = tmp_path / "outro.mov"
        template.touch()

        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            template_intro=None,
            template_outro=None,
        )

        with patch.dict("os.environ", {ENV_TEMPLATE_OUTRO: str(template)}):
            result = validate_args(args)

        assert result.template_outro == template

    def test_template_intro_cli_precedence(self, tmp_path: Path) -> None:
        """template_intro CLI > env/template config."""
        cli_template = tmp_path / "cli_intro.mov"
        cli_template.touch()
        env_template = tmp_path / "env_intro.mov"
        env_template.touch()

        args = argparse.Namespace(
            targets=[],
            output=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            output_dir=None,
            parallel=None,
            template_intro=str(cli_template),
            template_outro=None,
        )

        with patch.dict("os.environ", {ENV_TEMPLATE_INTRO: str(env_template)}):
            result = validate_args(args)

        assert result.template_intro == cli_template


class TestCmdInitConfig:
    """cmd_init_config 테스트."""

    @patch("tubearchive.config.get_default_config_path")
    def test_creates_config_file(self, mock_path: MagicMock, tmp_path: Path) -> None:
        """설정 파일 생성."""
        from tubearchive.app.cli.main import cmd_init_config

        config_path = tmp_path / ".tubearchive" / "config.toml"
        mock_path.return_value = config_path

        cmd_init_config()

        assert config_path.exists()
        content = config_path.read_text()
        assert "[general]" in content
        assert "[youtube]" in content

    @patch("tubearchive.app.cli.main.safe_input", return_value="n")
    @patch("tubearchive.config.get_default_config_path")
    def test_skips_overwrite_when_declined(
        self, mock_path: MagicMock, mock_input: MagicMock, tmp_path: Path
    ) -> None:
        """덮어쓰기 거부 시 스킵."""
        from tubearchive.app.cli.main import cmd_init_config

        config_path = tmp_path / "config.toml"
        config_path.write_text("existing content")
        mock_path.return_value = config_path

        cmd_init_config()

        assert config_path.read_text() == "existing content"

    @patch("tubearchive.app.cli.main.safe_input", return_value="y")
    @patch("tubearchive.config.get_default_config_path")
    def test_overwrites_when_confirmed(
        self, mock_path: MagicMock, mock_input: MagicMock, tmp_path: Path
    ) -> None:
        """덮어쓰기 확인 시 덮어씀."""
        from tubearchive.app.cli.main import cmd_init_config

        config_path = tmp_path / "config.toml"
        config_path.write_text("old content")
        mock_path.return_value = config_path

        cmd_init_config()

        content = config_path.read_text()
        assert "[general]" in content


class TestMain:
    """main 함수 테스트."""

    @patch("tubearchive.app.cli.main.run_pipeline")
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

    @patch("tubearchive.app.cli.main._run_watch_mode")
    def test_main_calls_watch_mode(
        self,
        mock_watch_mode: MagicMock,
        tmp_path: Path,
    ) -> None:
        """--watch 사용 시 watch 모드 진입."""
        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()

        with patch("sys.argv", ["tubearchive", "--watch", str(watch_dir)]):
            main()

        mock_watch_mode.assert_called_once()

    @patch("tubearchive.app.cli.main.run_pipeline")
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

    def test_main_runs_run_hook(self, tmp_path: Path) -> None:
        """--run-hook 지정 시 run_hooks가 호출된다."""
        config = AppConfig(hooks=HooksConfig(on_merge=("echo merged",)))

        with (
            patch("tubearchive.app.cli.main.load_config", return_value=config),
            patch("tubearchive.app.cli.main.run_hooks") as mock_run_hooks,
            patch("sys.argv", ["tubearchive", "--run-hook", "on_merge"]),
        ):
            main()

        mock_run_hooks.assert_called_once()
        assert mock_run_hooks.call_args.args[1] == "on_merge"
        assert mock_run_hooks.call_args.args[0] == config.hooks

    @patch("tubearchive.app.cli.main.run_pipeline", side_effect=RuntimeError("pipeline failed"))
    def test_main_invokes_error_hook_on_exception(
        self,
        _mock_pipeline: MagicMock,
        tmp_path: Path,
    ) -> None:
        """파이프라인 예외 발생 시 on_error 훅이 실행된다."""
        video_file = tmp_path / "video.mp4"
        video_file.touch()
        config = AppConfig(hooks=HooksConfig(on_error=("echo error",)))

        with (
            patch("tubearchive.app.cli.main.load_config", return_value=config),
            patch("tubearchive.app.cli.main.run_hooks") as mock_run_hooks,
            patch("sys.argv", ["tubearchive", str(video_file)]),
            pytest.raises(SystemExit),
        ):
            main()

        assert mock_run_hooks.call_count >= 1
        events = [args.args[1] for args in mock_run_hooks.call_args_list]
        assert "on_error" in events


class TestUploadAfterPipeline:
    """_upload_after_pipeline 테스트."""

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.app.cli.main.init_database")
    def test_upload_after_pipeline_passes_privacy(
        self,
        mock_db: MagicMock,
        mock_playlist: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """privacy 파라미터 전달 확인."""
        from tubearchive.app.cli.main import _upload_after_pipeline

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

        with patch("tubearchive.app.cli.main.MergeJobRepository", return_value=mock_repo):
            _upload_after_pipeline(output_path, args)

        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["privacy"] == "private"

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.app.cli.main.init_database")
    def test_upload_after_pipeline_uses_explicit_thumbnail(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """명시 썸네일이 있으면 업로드에 그대로 전달."""
        from tubearchive.app.cli.main import _upload_after_pipeline

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

        with patch("tubearchive.app.cli.main.MergeJobRepository", return_value=mock_repo):
            _upload_after_pipeline(
                output_path,
                args,
                generated_thumbnail_paths=None,
                explicit_thumbnail=thumbnail,
            )

        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["thumbnail"] == thumbnail

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.app.cli.main.init_database")
    def test_upload_after_pipeline_passes_subtitle_args(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """자막 경로와 언어가 업로드 인자로 전달된다."""
        from tubearchive.app.cli.main import _upload_after_pipeline

        mock_conn = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.close = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_latest.return_value = None

        output_path = tmp_path / "output.mp4"
        output_path.touch()
        subtitle_path = tmp_path / "subtitle.srt"
        subtitle_path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n테스트\n")
        args = argparse.Namespace(
            upload_privacy="unlisted",
            playlist=None,
            upload_chunk=32,
        )

        with patch("tubearchive.app.cli.main.MergeJobRepository", return_value=mock_repo):
            _upload_after_pipeline(
                output_path,
                args,
                subtitle_path=subtitle_path,
                subtitle_language="ko",
            )

        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args.kwargs
        assert call_kwargs["subtitle_path"] == subtitle_path
        assert call_kwargs["subtitle_language"] == "ko"

    @patch("tubearchive.app.cli.main._upload_split_files")
    @patch("tubearchive.app.cli.main.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.app.cli.main.init_database")
    def test_upload_after_pipeline_passes_subtitle_args_to_split_upload(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        mock_upload_split: MagicMock,
        tmp_path: Path,
    ) -> None:
        """분할 업로드 시 자막 경로/언어가 split 업로더에 전달된다."""
        from tubearchive.app.cli.main import _upload_after_pipeline

        mock_conn = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.close = MagicMock()

        output_path = tmp_path / "output.mp4"
        output_path.touch()
        split_file = tmp_path / "part1.mp4"
        split_file.write_bytes(b"segment")
        subtitle_path = tmp_path / "subtitle.srt"
        subtitle_path.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n테스트\n")

        args = argparse.Namespace(
            upload_privacy="unlisted",
            playlist=None,
            upload_chunk=32,
        )

        with (
            patch(
                "tubearchive.app.cli.main.MergeJobRepository",
                return_value=MagicMock(
                    get_latest=MagicMock(
                        return_value=MagicMock(
                            id=2,
                            title="title",
                            summary_markdown="",
                            clips_info_json=None,
                        ),
                    ),
                ),
            ),
            patch(
                "tubearchive.app.cli.main.SplitJobRepository",
                return_value=MagicMock(
                    get_by_merge_job_id=MagicMock(
                        return_value=[MagicMock(id=5, output_files=[split_file])]
                    )
                ),
            ),
        ):
            _upload_after_pipeline(
                output_path,
                args,
                subtitle_path=subtitle_path,
                subtitle_language="ko",
            )

        mock_upload_split.assert_called_once()
        call_kwargs = mock_upload_split.call_args.kwargs
        assert call_kwargs["subtitle_path"] == subtitle_path
        assert call_kwargs["subtitle_language"] == "ko"

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.app.cli.main.init_database")
    def test_upload_after_pipeline_uses_single_generated_thumbnail(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """생성 썸네일 1개는 자동 선택."""
        from tubearchive.app.cli.main import _upload_after_pipeline

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

        with patch("tubearchive.app.cli.main.MergeJobRepository", return_value=mock_repo):
            _upload_after_pipeline(
                output_path,
                args,
                generated_thumbnail_paths=[generated],
            )

        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["thumbnail"] == generated

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main._resolve_upload_thumbnail")
    @patch("tubearchive.app.cli.main.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.app.cli.main.init_database")
    def test_upload_after_pipeline_logs_selected_thumbnail(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        mock_resolve_thumbnail: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """썸네일 선택 결과를 INFO 로그로 남긴다."""
        from tubearchive.app.cli.main import _upload_after_pipeline

        mock_conn = MagicMock()
        mock_db.return_value = mock_conn
        mock_conn.close = MagicMock()

        mock_repo = MagicMock()
        mock_repo.get_latest.return_value = None

        output_path = tmp_path / "output.mp4"
        output_path.touch()
        thumbnail = tmp_path / "selected.jpg"
        thumbnail.touch()
        args = argparse.Namespace(
            upload_privacy="unlisted",
            playlist=None,
            upload_chunk=32,
        )
        mock_resolve_thumbnail.return_value = thumbnail

        with (
            patch("tubearchive.app.cli.main.MergeJobRepository", return_value=mock_repo),
            caplog.at_level("INFO"),
        ):
            _upload_after_pipeline(
                output_path,
                args,
                generated_thumbnail_paths=[tmp_path / "generated.jpg"],
            )

        assert "Using thumbnail for upload" in caplog.text
        assert thumbnail.name in caplog.text


class TestResolveUploadThumbnail:
    """썸네일 업로드 후보 결정 테스트."""

    def test_resolve_upload_thumbnail_uses_explicit(self, tmp_path: Path) -> None:
        """명시 썸네일이 우선."""
        from tubearchive.app.cli.main import _resolve_upload_thumbnail

        explicit = tmp_path / "a.jpg"
        generated = [tmp_path / "b.jpg"]

        assert _resolve_upload_thumbnail(explicit, generated) is explicit

    def test_resolve_upload_thumbnail_single_generated(self, tmp_path: Path) -> None:
        """자동 생성 썸네일 1개는 해당 경로 사용."""
        from tubearchive.app.cli.main import _resolve_upload_thumbnail

        generated = [tmp_path / "auto.jpg"]
        generated[0].touch()

        assert _resolve_upload_thumbnail(None, generated) is generated[0]

    @patch("tubearchive.app.cli.main._interactive_select", return_value=1)
    def test_resolve_upload_thumbnail_selects_from_multiple(
        self,
        _mock_select: MagicMock,
        tmp_path: Path,
    ) -> None:
        """썸네일이 여러 개면 인터랙티브 선택 결과 사용."""
        from tubearchive.app.cli.main import _resolve_upload_thumbnail

        generated = [tmp_path / "auto1.jpg", tmp_path / "auto2.jpg"]
        for path in generated:
            path.touch()

        assert _resolve_upload_thumbnail(None, generated) is generated[1]

    @patch("tubearchive.app.cli.main._interactive_select", return_value=None)
    def test_resolve_upload_thumbnail_skips_when_user_cancels(
        self,
        _mock_select: MagicMock,
        tmp_path: Path,
    ) -> None:
        """사용자가 0번으로 건너뛰면 None을 반환한다."""
        from tubearchive.app.cli.main import _resolve_upload_thumbnail

        generated = [tmp_path / "auto1.jpg", tmp_path / "auto2.jpg"]
        for path in generated:
            path.touch()

        assert _resolve_upload_thumbnail(None, generated) is None


class TestUploadSplitFiles:
    """_upload_split_files 분할 업로드 테스트."""

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.probe_duration", return_value=3600.0)
    def test_uploads_each_split_file(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """분할 파일 각각에 대해 upload_to_youtube가 호출된다."""
        from tubearchive.app.cli.main import _upload_split_files

        f1 = tmp_path / "video_001.mp4"
        f2 = tmp_path / "video_002.mp4"
        f1.touch()
        f2.touch()

        clips_json = (
            '[{"name":"A.mp4","duration":3600,"start":0,"end":3600,'
            '"device":"Nikon","shot_time":"10:00"},'
            '{"name":"B.mp4","duration":3600,"start":3600,"end":7200,'
            '"device":"GoPro","shot_time":"11:00"}]'
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

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.probe_duration", return_value=3600.0)
    def test_title_includes_part_numbers(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """제목에 (Part N/M) 형식이 포함된다."""
        from tubearchive.app.cli.main import _upload_split_files

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

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.app.cli.main.init_database")
    def test_falls_back_when_no_split_files(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """분할 파일이 없으면 단일 파일 업로드로 폴백한다."""
        from tubearchive.app.cli.main import _upload_after_pipeline

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
            patch("tubearchive.app.cli.main.MergeJobRepository", return_value=mock_repo),
            patch("tubearchive.app.cli.main.SplitJobRepository", return_value=mock_split_repo),
        ):
            _upload_after_pipeline(output_path, args)

        # 단일 파일로 업로드
        mock_upload.assert_called_once()
        call_kwargs = mock_upload.call_args[1]
        assert call_kwargs["file_path"] == output_path

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.probe_duration", return_value=3600.0)
    @patch("tubearchive.app.cli.main.resolve_playlist_ids", return_value=[])
    @patch("tubearchive.app.cli.main.init_database")
    def test_uploads_split_files_when_present(
        self,
        mock_db: MagicMock,
        _mock_playlist: MagicMock,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """분할 파일이 DB에 있는 경우 분할 파일을 업로드한다."""
        from tubearchive.app.cli.main import _upload_after_pipeline

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
            patch("tubearchive.app.cli.main.MergeJobRepository", return_value=mock_repo),
            patch("tubearchive.app.cli.main.SplitJobRepository", return_value=mock_split_repo),
        ):
            _upload_after_pipeline(output_path, args)

        # 분할 파일 2개가 업로드됨
        assert mock_upload.call_count == 2


class TestUploadOnly:
    """--upload-only 처리 테스트."""

    @patch("tubearchive.app.cli.main.run_hooks")
    @patch("tubearchive.app.cli.main.upload_to_youtube", return_value="yt123")
    def test_upload_only_calls_upload_hook(
        self,
        mock_upload: MagicMock,
        mock_run_hooks: MagicMock,
        tmp_path: Path,
    ) -> None:
        """--upload-only 완료 후 on_upload 훅이 실행된다."""
        from tubearchive.app.cli.main import cmd_upload_only

        file_path = tmp_path / "output.mp4"
        file_path.write_bytes(b"dummy")

        args = argparse.Namespace(
            upload_only=str(file_path),
            playlist=None,
            upload_title=None,
            upload_privacy="unlisted",
            upload_chunk=32,
            schedule=None,
            set_thumbnail=None,
        )

        with (
            patch(
                "tubearchive.app.cli.main.MergeJobRepository",
                return_value=MagicMock(get_by_output_path=MagicMock(return_value=None)),
            ),
            patch("tubearchive.app.cli.main.resolve_playlist_ids", return_value=[]),
            patch("tubearchive.app.cli.main._resolve_set_thumbnail_path", return_value=None),
        ):
            result = cmd_upload_only(args, hooks=HooksConfig(on_upload=("echo upload",)))

        assert result == "yt123"
        mock_upload.assert_called_once()
        mock_run_hooks.assert_called_once()
        assert mock_run_hooks.call_args.args[1] == "on_upload"
        context = mock_run_hooks.call_args.kwargs["context"]
        assert context.output_path == file_path
        assert context.youtube_id == "yt123"

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.probe_duration", return_value=3600.0)
    def test_split_upload_reuses_thumbnail_for_all_parts(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """분할 업로드는 모든 파트에 동일한 썸네일을 전달한다."""
        from tubearchive.app.cli.main import _upload_split_files

        f1 = tmp_path / "video_001.mp4"
        f2 = tmp_path / "video_002.mp4"
        f1.touch()
        f2.touch()

        thumbnail = tmp_path / "thumb.jpg"
        thumbnail.touch()

        _upload_split_files(
            split_files=[f1, f2],
            title="Test",
            clips_info_json=(
                '[{"name":"A.mp4","duration":3600,"start":0,"end":3600,'
                '"device":null,"shot_time":null}]'
            ),
            privacy="unlisted",
            merge_job_id=1,
            playlist_ids=None,
            chunk_mb=32,
            thumbnail=thumbnail,
        )

        assert mock_upload.call_count == 2
        assert all(call.kwargs["thumbnail"] == thumbnail for call in mock_upload.call_args_list)

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.probe_duration", return_value=60.0)
    def test_malformed_clips_json_does_not_crash(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """잘못된 clips_info_json이어도 업로드가 진행된다."""
        from tubearchive.app.cli.main import _upload_split_files

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

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.probe_duration", return_value=60.0)
    def test_none_clips_json_does_not_crash(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """clips_info_json이 None이어도 업로드가 진행된다."""
        from tubearchive.app.cli.main import _upload_split_files

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

    @patch("tubearchive.app.cli.main.upload_to_youtube")
    @patch("tubearchive.app.cli.main.probe_duration", return_value=3600.0)
    def test_partial_upload_failure_continues(
        self,
        _mock_probe: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
    ) -> None:
        """한 파트 업로드 실패 시 나머지 파트는 계속 업로드한다."""
        from tubearchive.app.cli.main import _upload_split_files

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
        from tubearchive.domain.models.video import FadeConfig

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

    def test_watermark_default_values(self) -> None:
        """워터마크 기본값 확인."""
        opts = TranscodeOptions()
        assert opts.watermark is False
        assert opts.watermark_pos == "bottom-right"
        assert opts.watermark_size == 48
        assert opts.watermark_color == "white"
        assert opts.watermark_alpha == 0.85

    def test_watermark_custom_values(self) -> None:
        """워터마크 커스텀 값 확인."""
        opts = TranscodeOptions(
            watermark=True,
            watermark_pos="center",
            watermark_size=32,
            watermark_color="yellow",
            watermark_alpha=0.7,
            watermark_text="sample",
        )
        assert opts.watermark is True
        assert opts.watermark_pos == "center"
        assert opts.watermark_size == 32
        assert opts.watermark_color == "yellow"
        assert opts.watermark_alpha == 0.7
        assert opts.watermark_text == "sample"


class TestDatabaseSession:
    """database_session context manager 테스트."""

    @patch("tubearchive.app.cli.main.init_database")
    def test_yields_connection(self, mock_init: MagicMock) -> None:
        """context manager가 DB 연결 객체를 yield한다."""
        mock_conn = MagicMock()
        mock_init.return_value = mock_conn

        with database_session() as conn:
            assert conn is mock_conn

    @patch("tubearchive.app.cli.main.init_database")
    def test_closes_connection_on_exit(self, mock_init: MagicMock) -> None:
        """블록 종료 시 DB 연결이 닫힌다."""
        mock_conn = MagicMock()
        mock_init.return_value = mock_conn

        with database_session():
            mock_conn.close.assert_not_called()

        mock_conn.close.assert_called_once()

    @patch("tubearchive.app.cli.main.init_database")
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


class TestWatermarkText:
    """워터마크 텍스트 생성."""

    def test_make_watermark_text_includes_location(self, tmp_path: Path) -> None:
        """위치 문자열이 있으면 날짜와 합쳐서 반환."""
        video_path = tmp_path / "a.mp4"
        video_path.write_text("")
        video = VideoFile(
            path=video_path,
            creation_time=datetime(2025, 1, 2),
            size_bytes=10,
        )
        metadata = VideoMetadata(
            width=1920,
            height=1080,
            duration_seconds=12.5,
            fps=30.0,
            codec="h264",
            pixel_format="yuv420p",
            is_portrait=False,
            is_vfr=False,
            device_model=None,
            color_space=None,
            color_transfer=None,
            color_primaries=None,
            location="Seoul Downtown",
        )

        assert _make_watermark_text(video, metadata) == "2025.01.02 | Seoul Downtown"

    def test_make_watermark_text_uses_coordinates_when_no_location(self, tmp_path: Path) -> None:
        """location이 없으면 위도/경도 문자열로 fallback."""
        video_path = tmp_path / "a.mp4"
        video_path.write_text("")
        video = VideoFile(
            path=video_path,
            creation_time=datetime(2025, 1, 2),
            size_bytes=10,
        )
        metadata = VideoMetadata(
            width=1920,
            height=1080,
            duration_seconds=12.5,
            fps=30.0,
            codec="h264",
            pixel_format="yuv420p",
            is_portrait=False,
            is_vfr=False,
            device_model=None,
            color_space=None,
            color_transfer=None,
            color_primaries=None,
            location_latitude=37.5665,
            location_longitude=126.9780,
        )

        assert _make_watermark_text(video, metadata) == "2025.01.02 | 37.566500, 126.978000"

    def test_make_watermark_text_without_location(self, tmp_path: Path) -> None:
        """location 정보가 없으면 날짜만 반환."""
        video_path = tmp_path / "a.mp4"
        video_path.write_text("")
        video = VideoFile(
            path=video_path,
            creation_time=datetime(2025, 1, 2),
            size_bytes=10,
        )
        metadata = VideoMetadata(
            width=1920,
            height=1080,
            duration_seconds=12.5,
            fps=30.0,
            codec="h264",
            pixel_format="yuv420p",
            is_portrait=False,
            is_vfr=False,
            device_model=None,
            color_space=None,
            color_transfer=None,
            color_primaries=None,
            location=None,
            location_latitude=None,
            location_longitude=None,
        )

        assert _make_watermark_text(video, metadata) == "2025.01.02"


class TestSaveMergeJobToDb:
    """save_merge_job_to_db 반환값 테스트."""

    @patch("tubearchive.app.cli.main.database_session")
    def test_returns_summary_and_merge_job_id(
        self,
        mock_db_session: MagicMock,
        tmp_path: Path,
    ) -> None:
        """summary와 merge_job_id를 tuple로 반환한다."""
        from tubearchive.app.cli.main import save_merge_job_to_db

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
            patch("tubearchive.app.cli.main.MergeJobRepository", return_value=mock_repo),
            patch(
                "tubearchive.shared.summary_generator.generate_clip_summary",
                return_value="## Summary",
            ),
            patch(
                "tubearchive.shared.summary_generator.generate_youtube_description",
                return_value="desc",
            ),
        ):
            result = save_merge_job_to_db(output_file, clips, [tmp_path], [1])

        assert isinstance(result, tuple)
        assert len(result) == 2
        summary, merge_job_id = result
        assert summary == "## Summary"
        assert merge_job_id == 42

    @patch("tubearchive.app.cli.main.database_session")
    def test_returns_none_tuple_on_failure(
        self,
        mock_db_session: MagicMock,
        tmp_path: Path,
    ) -> None:
        """DB 저장 실패 시 (None, None)을 반환한다."""
        from tubearchive.app.cli.main import save_merge_job_to_db

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
