"""설정 파일(TOML) 지원 테스트."""

import os
import tomllib
from pathlib import Path

import pytest

from tubearchive.config import (
    AppConfig,
    GeneralConfig,
    YouTubeConfig,
    apply_config_to_env,
    generate_default_config,
    get_default_config_path,
    load_config,
)


class TestGetDefaultConfigPath:
    """기본 설정 파일 경로 테스트."""

    def test_returns_home_config_path(self) -> None:
        """~/.tubearchive/config.toml 반환."""
        path = get_default_config_path()
        assert path == Path.home() / ".tubearchive" / "config.toml"

    def test_returns_path_type(self) -> None:
        """Path 타입 반환."""
        assert isinstance(get_default_config_path(), Path)


class TestLoadConfigNormal:
    """load_config 정상 케이스."""

    def test_loads_full_config(self, tmp_path: Path) -> None:
        """전체 필드 파싱."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
output_dir = "/tmp/output"
parallel = 4
db_path = "/tmp/test.db"
group_sequences = true
fade_duration = 0.75

[youtube]
client_secrets = "/tmp/secrets.json"
token = "/tmp/token.json"
playlist = ["PL111", "PL222"]
upload_chunk_mb = 64
upload_privacy = "private"
""")
        config = load_config(config_file)

        assert config.general.output_dir == "/tmp/output"
        assert config.general.parallel == 4
        assert config.general.db_path == "/tmp/test.db"
        assert config.general.group_sequences is True
        assert config.general.fade_duration == 0.75
        assert config.youtube.client_secrets == "/tmp/secrets.json"
        assert config.youtube.token == "/tmp/token.json"
        assert config.youtube.playlist == ["PL111", "PL222"]
        assert config.youtube.upload_chunk_mb == 64
        assert config.youtube.upload_privacy == "private"

    def test_loads_partial_general_only(self, tmp_path: Path) -> None:
        """[general] 섹션만 있는 경우."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
parallel = 2
""")
        config = load_config(config_file)

        assert config.general.parallel == 2
        assert config.general.output_dir is None
        assert config.youtube == YouTubeConfig()

    def test_loads_partial_youtube_only(self, tmp_path: Path) -> None:
        """[youtube] 섹션만 있는 경우."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[youtube]
upload_privacy = "public"
""")
        config = load_config(config_file)

        assert config.general == GeneralConfig()
        assert config.youtube.upload_privacy == "public"

    def test_loads_empty_file(self, tmp_path: Path) -> None:
        """빈 파일 → 빈 AppConfig."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("")

        config = load_config(config_file)

        assert config == AppConfig()

    def test_loads_default_path_when_none(self) -> None:
        """path=None이면 기본 경로 사용 (파일 없으면 빈 config)."""
        config = load_config(None)
        assert isinstance(config, AppConfig)

    def test_playlist_single_string(self, tmp_path: Path) -> None:
        """playlist 단일 문자열도 허용."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[youtube]
playlist = "PLsingle"
""")
        config = load_config(config_file)

        assert config.youtube.playlist == ["PLsingle"]

    def test_upload_privacy_all_values(self, tmp_path: Path) -> None:
        """upload_privacy 허용 값 테스트."""
        for privacy in ("public", "unlisted", "private"):
            config_file = tmp_path / "config.toml"
            config_file.write_text(f"""\
[youtube]
upload_privacy = "{privacy}"
""")
            config = load_config(config_file)
            assert config.youtube.upload_privacy == privacy


class TestLoadConfigBoundary:
    """load_config 경계 케이스."""

    def test_file_not_found_returns_empty(self, tmp_path: Path) -> None:
        """파일 없음 → 빈 AppConfig."""
        config = load_config(tmp_path / "nonexistent.toml")
        assert config == AppConfig()

    def test_unknown_keys_ignored(self, tmp_path: Path) -> None:
        """미지 키 무시."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
parallel = 2
unknown_key = "value"

[youtube]
unknown_section_key = true

[unknown_section]
foo = "bar"
""")
        config = load_config(config_file)

        assert config.general.parallel == 2

    def test_empty_sections(self, tmp_path: Path) -> None:
        """빈 섹션."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]

[youtube]
""")
        config = load_config(config_file)

        assert config.general == GeneralConfig()
        assert config.youtube == YouTubeConfig()

    def test_none_fields_skipped_in_env(self, tmp_path: Path) -> None:
        """None 필드는 환경변수에 주입 안 됨."""
        config = AppConfig()
        env_snapshot = os.environ.copy()

        apply_config_to_env(config)

        # 새 환경변수가 추가되지 않았는지 확인
        for key in (
            "TUBEARCHIVE_OUTPUT_DIR",
            "TUBEARCHIVE_PARALLEL",
            "TUBEARCHIVE_DB_PATH",
            "TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS",
            "TUBEARCHIVE_YOUTUBE_TOKEN",
            "TUBEARCHIVE_YOUTUBE_PLAYLIST",
            "TUBEARCHIVE_UPLOAD_CHUNK_MB",
            "TUBEARCHIVE_GROUP_SEQUENCES",
            "TUBEARCHIVE_FADE_DURATION",
        ):
            if key not in env_snapshot:
                assert key not in os.environ, f"{key} should not be set"


class TestLoadConfigError:
    """load_config 오류 케이스."""

    def test_invalid_toml_syntax(self, tmp_path: Path) -> None:
        """잘못된 TOML 문법 → warning + 빈 AppConfig."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("invalid [[ toml syntax !!!")

        config = load_config(config_file)

        assert config == AppConfig()

    def test_type_error_parallel_string(self, tmp_path: Path) -> None:
        """parallel 타입 오류 (str) → 해당 필드 무시."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
parallel = "abc"
output_dir = "/valid/path"
""")
        config = load_config(config_file)

        assert config.general.parallel is None
        assert config.general.output_dir == "/valid/path"

    def test_type_error_output_dir_int(self, tmp_path: Path) -> None:
        """output_dir 타입 오류 (int) → 해당 필드 무시."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
output_dir = 123
""")
        config = load_config(config_file)

        assert config.general.output_dir is None

    def test_type_error_upload_chunk_string(self, tmp_path: Path) -> None:
        """upload_chunk_mb 타입 오류 (str) → 해당 필드 무시."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[youtube]
upload_chunk_mb = "big"
client_secrets = "/valid/path"
""")
        config = load_config(config_file)

        assert config.youtube.upload_chunk_mb is None
        assert config.youtube.client_secrets == "/valid/path"

    def test_invalid_upload_privacy_value(self, tmp_path: Path) -> None:
        """upload_privacy 허용되지 않는 값 → 무시."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[youtube]
upload_privacy = "secret"
""")
        config = load_config(config_file)

        assert config.youtube.upload_privacy is None

    def test_permission_error(self, tmp_path: Path) -> None:
        """권한 오류 → 빈 AppConfig."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("[general]\nparallel = 2\n")
        config_file.chmod(0o000)

        try:
            config = load_config(config_file)
            assert config == AppConfig()
        finally:
            config_file.chmod(0o644)

    def test_parallel_bool_ignored(self, tmp_path: Path) -> None:
        """parallel에 bool 값 → 무시 (TOML에서 bool은 int 서브클래스)."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
parallel = true
""")
        config = load_config(config_file)

        assert config.general.parallel is None


class TestApplyConfigToEnv:
    """apply_config_to_env 테스트."""

    def test_injects_all_fields(self) -> None:
        """모든 필드 환경변수 주입."""
        config = AppConfig(
            general=GeneralConfig(
                output_dir="/tmp/out",
                parallel=4,
                db_path="/tmp/db.sqlite",
                group_sequences=False,
                fade_duration=0.25,
            ),
            youtube=YouTubeConfig(
                client_secrets="/tmp/secrets.json",
                token="/tmp/token.json",
                playlist=["PL111", "PL222"],
                upload_chunk_mb=64,
            ),
        )

        # 테스트 대상 환경변수 모두 제거
        env_keys = [
            "TUBEARCHIVE_OUTPUT_DIR",
            "TUBEARCHIVE_PARALLEL",
            "TUBEARCHIVE_DB_PATH",
            "TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS",
            "TUBEARCHIVE_YOUTUBE_TOKEN",
            "TUBEARCHIVE_YOUTUBE_PLAYLIST",
            "TUBEARCHIVE_UPLOAD_CHUNK_MB",
            "TUBEARCHIVE_GROUP_SEQUENCES",
            "TUBEARCHIVE_FADE_DURATION",
        ]
        saved = {}
        for key in env_keys:
            saved[key] = os.environ.pop(key, None)

        try:
            apply_config_to_env(config)

            assert os.environ.get("TUBEARCHIVE_OUTPUT_DIR") == "/tmp/out"
            assert os.environ.get("TUBEARCHIVE_PARALLEL") == "4"
            assert os.environ.get("TUBEARCHIVE_DB_PATH") == "/tmp/db.sqlite"
            assert os.environ.get("TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS") == "/tmp/secrets.json"
            assert os.environ.get("TUBEARCHIVE_YOUTUBE_TOKEN") == "/tmp/token.json"
            assert os.environ.get("TUBEARCHIVE_YOUTUBE_PLAYLIST") == "PL111,PL222"
            assert os.environ.get("TUBEARCHIVE_UPLOAD_CHUNK_MB") == "64"
            assert os.environ.get("TUBEARCHIVE_GROUP_SEQUENCES") == "false"
            assert os.environ.get("TUBEARCHIVE_FADE_DURATION") == "0.25"
        finally:
            # 환경변수 복원
            for key in env_keys:
                os.environ.pop(key, None)
                if saved[key] is not None:
                    os.environ[key] = saved[key]

    def test_preserves_existing_env(self) -> None:
        """기존 환경변수 보존 (config 값으로 덮어쓰지 않음)."""
        config = AppConfig(
            general=GeneralConfig(parallel=8),
        )

        saved = os.environ.get("TUBEARCHIVE_PARALLEL")
        os.environ["TUBEARCHIVE_PARALLEL"] = "2"

        try:
            apply_config_to_env(config)

            # 기존 값 "2"가 보존되어야 함
            assert os.environ.get("TUBEARCHIVE_PARALLEL") == "2"
        finally:
            if saved is not None:
                os.environ["TUBEARCHIVE_PARALLEL"] = saved
            else:
                os.environ.pop("TUBEARCHIVE_PARALLEL", None)

    def test_playlist_csv_conversion(self) -> None:
        """playlist 리스트 → CSV 변환."""
        config = AppConfig(
            youtube=YouTubeConfig(playlist=["PL1", "PL2", "PL3"]),
        )

        saved = os.environ.pop("TUBEARCHIVE_YOUTUBE_PLAYLIST", None)
        try:
            apply_config_to_env(config)
            assert os.environ.get("TUBEARCHIVE_YOUTUBE_PLAYLIST") == "PL1,PL2,PL3"
        finally:
            os.environ.pop("TUBEARCHIVE_YOUTUBE_PLAYLIST", None)
            if saved is not None:
                os.environ["TUBEARCHIVE_YOUTUBE_PLAYLIST"] = saved

    def test_empty_playlist_not_injected(self) -> None:
        """빈 playlist는 환경변수에 주입 안 됨."""
        config = AppConfig(
            youtube=YouTubeConfig(playlist=[]),
        )

        saved = os.environ.pop("TUBEARCHIVE_YOUTUBE_PLAYLIST", None)
        try:
            apply_config_to_env(config)
            assert "TUBEARCHIVE_YOUTUBE_PLAYLIST" not in os.environ
        finally:
            if saved is not None:
                os.environ["TUBEARCHIVE_YOUTUBE_PLAYLIST"] = saved


class TestGenerateDefaultConfig:
    """generate_default_config 테스트."""

    def test_returns_string(self) -> None:
        """문자열 반환."""
        result = generate_default_config()
        assert isinstance(result, str)

    def test_contains_sections(self) -> None:
        """[general]과 [youtube] 섹션 포함."""
        result = generate_default_config()
        assert "[general]" in result
        assert "[youtube]" in result

    def test_contains_all_keys_as_comments(self) -> None:
        """모든 설정 키가 주석으로 포함."""
        result = generate_default_config()
        expected_keys = [
            "output_dir",
            "parallel",
            "db_path",
            "denoise",
            "denoise_level",
            "normalize_audio",
            "group_sequences",
            "fade_duration",
            "client_secrets",
            "token",
            "playlist",
            "upload_chunk_mb",
            "upload_privacy",
        ]
        for key in expected_keys:
            assert key in result, f"Missing key: {key}"

    def test_template_is_valid_toml_when_uncommented(self) -> None:
        """주석 해제 시 유효한 TOML."""
        template = generate_default_config()
        # 주석 해제
        lines = []
        for line in template.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("# ") and "=" in stripped:
                # "# key = value" → "key = value"
                lines.append(stripped[2:])
            elif stripped.startswith("#"):
                # 순수 주석은 스킵
                continue
            else:
                lines.append(line)

        toml_str = "\n".join(lines)
        # 파싱 가능해야 함
        parsed = tomllib.loads(toml_str)
        assert "general" in parsed
        assert "youtube" in parsed


class TestDenoiseConfig:
    """denoise 설정 테스트."""

    def test_loads_denoise_config(self, tmp_path: Path) -> None:
        """denoise=true, denoise_level="heavy" 파싱."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
denoise = true
denoise_level = "heavy"
""")
        config = load_config(config_file)

        assert config.general.denoise is True
        assert config.general.denoise_level == "heavy"

    def test_denoise_default_none(self, tmp_path: Path) -> None:
        """미설정 시 None."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
parallel = 2
""")
        config = load_config(config_file)

        assert config.general.denoise is None
        assert config.general.denoise_level is None

    def test_denoise_type_error(self, tmp_path: Path) -> None:
        """denoise="yes" → 타입 경고 + None."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
denoise = "yes"
""")
        config = load_config(config_file)

        assert config.general.denoise is None

    def test_denoise_level_invalid_value(self, tmp_path: Path) -> None:
        """denoise_level="extreme" → 경고 + None."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
denoise_level = "extreme"
""")
        config = load_config(config_file)

        assert config.general.denoise_level is None

    def test_denoise_env_injection(self) -> None:
        """apply_config_to_env로 환경변수 주입 확인."""
        config = AppConfig(
            general=GeneralConfig(denoise=True, denoise_level="heavy"),
        )

        env_keys = ["TUBEARCHIVE_DENOISE", "TUBEARCHIVE_DENOISE_LEVEL"]
        saved = {k: os.environ.pop(k, None) for k in env_keys}

        try:
            apply_config_to_env(config)
            assert os.environ.get("TUBEARCHIVE_DENOISE") == "true"
            assert os.environ.get("TUBEARCHIVE_DENOISE_LEVEL") == "heavy"
        finally:
            for k in env_keys:
                os.environ.pop(k, None)
                if saved[k] is not None:
                    os.environ[k] = saved[k]

    def test_denoise_env_preserves_existing(self) -> None:
        """기존 환경변수 미덮어쓰기."""
        config = AppConfig(
            general=GeneralConfig(denoise=True, denoise_level="heavy"),
        )

        saved_denoise = os.environ.get("TUBEARCHIVE_DENOISE")
        saved_level = os.environ.get("TUBEARCHIVE_DENOISE_LEVEL")
        os.environ["TUBEARCHIVE_DENOISE"] = "false"
        os.environ["TUBEARCHIVE_DENOISE_LEVEL"] = "light"

        try:
            apply_config_to_env(config)
            assert os.environ.get("TUBEARCHIVE_DENOISE") == "false"
            assert os.environ.get("TUBEARCHIVE_DENOISE_LEVEL") == "light"
        finally:
            for k, v in [
                ("TUBEARCHIVE_DENOISE", saved_denoise),
                ("TUBEARCHIVE_DENOISE_LEVEL", saved_level),
            ]:
                if v is not None:
                    os.environ[k] = v
                else:
                    os.environ.pop(k, None)

    def test_denoise_false_env_injection(self) -> None:
        """denoise=False → "false" 주입."""
        config = AppConfig(
            general=GeneralConfig(denoise=False),
        )

        saved = os.environ.pop("TUBEARCHIVE_DENOISE", None)
        try:
            apply_config_to_env(config)
            assert os.environ.get("TUBEARCHIVE_DENOISE") == "false"
        finally:
            os.environ.pop("TUBEARCHIVE_DENOISE", None)
            if saved is not None:
                os.environ["TUBEARCHIVE_DENOISE"] = saved

    def test_denoise_level_all_values(self, tmp_path: Path) -> None:
        """denoise_level 허용 값 테스트 (light/medium/heavy)."""
        for level in ("light", "medium", "heavy"):
            config_file = tmp_path / "config.toml"
            config_file.write_text(f"""\
[general]
denoise_level = "{level}"
""")
            config = load_config(config_file)
            assert config.general.denoise_level == level


class TestNormalizeAudioConfig:
    """normalize_audio 설정 테스트."""

    def test_loads_normalize_audio_true(self, tmp_path: Path) -> None:
        """normalize_audio=true 파싱."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
normalize_audio = true
""")
        config = load_config(config_file)

        assert config.general.normalize_audio is True

    def test_loads_normalize_audio_false(self, tmp_path: Path) -> None:
        """normalize_audio=false 파싱."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
normalize_audio = false
""")
        config = load_config(config_file)

        assert config.general.normalize_audio is False

    def test_normalize_audio_default_none(self, tmp_path: Path) -> None:
        """미설정 시 None."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
parallel = 2
""")
        config = load_config(config_file)

        assert config.general.normalize_audio is None

    def test_normalize_audio_type_error(self, tmp_path: Path) -> None:
        """normalize_audio="yes" → 타입 경고 + None."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[general]
normalize_audio = "yes"
""")
        config = load_config(config_file)

        assert config.general.normalize_audio is None

    def test_normalize_audio_env_injection(self) -> None:
        """apply_config_to_env로 환경변수 주입 확인."""
        config = AppConfig(
            general=GeneralConfig(normalize_audio=True),
        )

        saved = os.environ.pop("TUBEARCHIVE_NORMALIZE_AUDIO", None)
        try:
            apply_config_to_env(config)
            assert os.environ.get("TUBEARCHIVE_NORMALIZE_AUDIO") == "true"
        finally:
            os.environ.pop("TUBEARCHIVE_NORMALIZE_AUDIO", None)
            if saved is not None:
                os.environ["TUBEARCHIVE_NORMALIZE_AUDIO"] = saved

    def test_normalize_audio_env_preserves_existing(self) -> None:
        """기존 환경변수 미덮어쓰기."""
        config = AppConfig(
            general=GeneralConfig(normalize_audio=True),
        )

        saved = os.environ.get("TUBEARCHIVE_NORMALIZE_AUDIO")
        os.environ["TUBEARCHIVE_NORMALIZE_AUDIO"] = "false"

        try:
            apply_config_to_env(config)
            assert os.environ.get("TUBEARCHIVE_NORMALIZE_AUDIO") == "false"
        finally:
            if saved is not None:
                os.environ["TUBEARCHIVE_NORMALIZE_AUDIO"] = saved
            else:
                os.environ.pop("TUBEARCHIVE_NORMALIZE_AUDIO", None)

    def test_normalize_audio_default_true(self) -> None:
        """환경변수 미설정 시 기본값 True."""
        from tubearchive.config import get_default_normalize_audio

        saved = os.environ.pop("TUBEARCHIVE_NORMALIZE_AUDIO", None)
        try:
            assert get_default_normalize_audio() is True
        finally:
            if saved is not None:
                os.environ["TUBEARCHIVE_NORMALIZE_AUDIO"] = saved

    def test_normalize_audio_env_override_false(self) -> None:
        """환경변수로 기본값 비활성화 가능."""
        from tubearchive.config import get_default_normalize_audio

        saved = os.environ.get("TUBEARCHIVE_NORMALIZE_AUDIO")
        try:
            os.environ["TUBEARCHIVE_NORMALIZE_AUDIO"] = "false"
            assert get_default_normalize_audio() is False
        finally:
            if saved is not None:
                os.environ["TUBEARCHIVE_NORMALIZE_AUDIO"] = saved
            else:
                os.environ.pop("TUBEARCHIVE_NORMALIZE_AUDIO", None)

    def test_normalize_audio_false_env_injection(self) -> None:
        """normalize_audio=False → "false" 주입."""
        config = AppConfig(
            general=GeneralConfig(normalize_audio=False),
        )

        saved = os.environ.pop("TUBEARCHIVE_NORMALIZE_AUDIO", None)
        try:
            apply_config_to_env(config)
            assert os.environ.get("TUBEARCHIVE_NORMALIZE_AUDIO") == "false"
        finally:
            os.environ.pop("TUBEARCHIVE_NORMALIZE_AUDIO", None)
            if saved is not None:
                os.environ["TUBEARCHIVE_NORMALIZE_AUDIO"] = saved


class TestConfigValidation:
    """PR 리뷰 반영 검증 테스트."""

    def test_playlist_mixed_type_warns(self, tmp_path: Path) -> None:
        """playlist 혼합 타입 → 비문자열 항목 무시 경고."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[youtube]
playlist = ["PL111", 123, "PL222"]
""")
        config = load_config(config_file)

        assert config.youtube.playlist == ["PL111", "PL222"]

    def test_section_not_table_warns(self, tmp_path: Path) -> None:
        """비-dict 섹션 → 경고 + 기본값."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
general = "not a table"
""")
        config = load_config(config_file)

        assert config.general == GeneralConfig()

    def test_upload_chunk_mb_out_of_range(self, tmp_path: Path) -> None:
        """upload_chunk_mb 범위 초과 → 경고 + None."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[youtube]
upload_chunk_mb = 999
""")
        config = load_config(config_file)

        assert config.youtube.upload_chunk_mb is None

    def test_upload_chunk_mb_zero_out_of_range(self, tmp_path: Path) -> None:
        """upload_chunk_mb = 0 → 범위 초과."""
        config_file = tmp_path / "config.toml"
        config_file.write_text("""\
[youtube]
upload_chunk_mb = 0
""")
        config = load_config(config_file)

        assert config.youtube.upload_chunk_mb is None

    def test_upload_chunk_mb_boundary_valid(self, tmp_path: Path) -> None:
        """upload_chunk_mb 경계값 1, 256 정상."""
        for val in (1, 256):
            config_file = tmp_path / "config.toml"
            config_file.write_text(f"""\
[youtube]
upload_chunk_mb = {val}
""")
            config = load_config(config_file)
            assert config.youtube.upload_chunk_mb == val


class TestDataclassFrozen:
    """dataclass frozen 속성 테스트."""

    def test_general_config_frozen(self) -> None:
        """GeneralConfig는 불변."""
        config = GeneralConfig(parallel=2)
        with pytest.raises(AttributeError):
            config.parallel = 4  # type: ignore[misc]

    def test_youtube_config_frozen(self) -> None:
        """YouTubeConfig는 불변."""
        config = YouTubeConfig(upload_privacy="public")
        with pytest.raises(AttributeError):
            config.upload_privacy = "private"  # type: ignore[misc]

    def test_app_config_frozen(self) -> None:
        """AppConfig는 불변."""
        config = AppConfig()
        with pytest.raises(AttributeError):
            config.general = GeneralConfig()  # type: ignore[misc]
