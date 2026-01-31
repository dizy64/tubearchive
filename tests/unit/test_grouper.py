"""연속 파일 그룹핑 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta

from tubearchive.core.grouper import (
    compute_fade_map,
    detect_sequence_key,
    group_sequences,
    reorder_with_groups,
)
from tubearchive.models.video import FadeConfig, VideoFile


def _make_video(
    tmp_path,
    name: str,
    created_at: datetime,
    size_bytes: int = 0,
) -> VideoFile:
    path = tmp_path / name
    path.write_bytes(b"0")
    return VideoFile(path=path, creation_time=created_at, size_bytes=size_bytes)


class TestDetectSequenceKey:
    """파일명 시퀀스 감지 테스트."""

    def test_gopro_new_pattern(self) -> None:
        key = detect_sequence_key("GH010128.MP4")
        assert key is not None
        assert key.group_id == "gopro_0128"
        assert key.order == 1

    def test_gopro_old_pattern(self) -> None:
        key = detect_sequence_key("GOPR0128.MP4")
        assert key is not None
        assert key.group_id == "gopro_0128"
        assert key.order == 0

    def test_gopro_old_continuation(self) -> None:
        key = detect_sequence_key("GP020128.MP4")
        assert key is not None
        assert key.group_id == "gopro_0128"
        assert key.order == 2

    def test_dji_pattern(self) -> None:
        key = detect_sequence_key("DJI_20250920194830_0001_D.MP4")
        assert key is not None
        assert key.group_id == "dji"
        assert key.order == 1

    def test_non_matching(self) -> None:
        assert detect_sequence_key("IMG_0001.MOV") is None

    def test_case_insensitive(self) -> None:
        key = detect_sequence_key("gh020128.mp4")
        assert key is not None
        assert key.group_id == "gopro_0128"
        assert key.order == 2


class TestGroupSequences:
    """group_sequences 테스트."""

    def test_empty_list(self) -> None:
        assert group_sequences([]) == []

    def test_gopro_single_group(self, tmp_path) -> None:
        base = datetime(2025, 1, 1, 10, 0, 0)
        files = [
            _make_video(tmp_path, "GH010128.MP4", base),
            _make_video(tmp_path, "GH020128.MP4", base + timedelta(minutes=10)),
            _make_video(tmp_path, "GH030128.MP4", base + timedelta(minutes=20)),
        ]
        groups = group_sequences(files)
        gopro_groups = [g for g in groups if g.group_id == "gopro_0128"]
        assert len(gopro_groups) == 1
        assert len(gopro_groups[0].files) == 3

    def test_gopro_two_sessions(self, tmp_path) -> None:
        base = datetime(2025, 1, 1, 10, 0, 0)
        files = [
            _make_video(tmp_path, "GH010128.MP4", base),
            _make_video(tmp_path, "GH020128.MP4", base + timedelta(minutes=10)),
            _make_video(tmp_path, "GH010129.MP4", base + timedelta(minutes=20)),
        ]
        groups = group_sequences(files)
        ids = {g.group_id for g in groups if g.group_id.startswith("gopro_")}
        assert ids == {"gopro_0128", "gopro_0129"}

    def test_dji_continuous_group(self, tmp_path) -> None:
        base = datetime(2025, 9, 20, 19, 48, 30)
        near_16g = int(16 * 1024**3 * 0.98)
        files = [
            _make_video(tmp_path, "DJI_20250920194830_0001_D.MP4", base, near_16g),
            _make_video(
                tmp_path,
                "DJI_20250920204450_0002_D.MP4",
                base + timedelta(minutes=55),
                near_16g,
            ),
            _make_video(
                tmp_path,
                "DJI_20250920213924_0003_D.MP4",
                base + timedelta(minutes=110),
                2 * 1024**3,
            ),
        ]
        groups = group_sequences(files)
        dji_groups = [g for g in groups if g.group_id.startswith("dji_")]
        assert len(dji_groups) == 1
        assert len(dji_groups[0].files) == 3

    def test_dji_separate_recordings(self, tmp_path) -> None:
        base = datetime(2025, 9, 20, 19, 48, 30)
        files = [
            _make_video(tmp_path, "DJI_20250920194830_0001_D.MP4", base, 2 * 1024**3),
            _make_video(
                tmp_path,
                "DJI_20250920204450_0002_D.MP4",
                base + timedelta(minutes=5),
                2 * 1024**3,
            ),
        ]
        groups = group_sequences(files)
        dji_groups = [g for g in groups if g.group_id.startswith("dji_")]
        assert len(dji_groups) == 2
        assert all(len(g.files) == 1 for g in dji_groups)

    def test_dji_sequence_reset(self, tmp_path) -> None:
        base = datetime(2025, 9, 20, 19, 48, 30)
        near_16g = int(16 * 1024**3 * 0.98)
        files = [
            _make_video(tmp_path, "DJI_20250920194830_0001_D.MP4", base, near_16g),
            _make_video(
                tmp_path,
                "DJI_20250920204450_0001_D.MP4",
                base + timedelta(hours=1),
                near_16g,
            ),
        ]
        groups = group_sequences(files)
        dji_groups = [g for g in groups if g.group_id.startswith("dji_")]
        assert len(dji_groups) == 2

    def test_gopro_with_interleaved_files(self, tmp_path) -> None:
        base = datetime(2025, 1, 1, 9, 0, 0)
        files = [
            _make_video(tmp_path, "iPhone_001.MOV", base),
            _make_video(tmp_path, "GH010128.MP4", base + timedelta(minutes=60)),
            _make_video(tmp_path, "iPhone_002.MOV", base + timedelta(minutes=65)),
            _make_video(tmp_path, "GH020128.MP4", base + timedelta(minutes=70)),
            _make_video(tmp_path, "GH030128.MP4", base + timedelta(minutes=80)),
            _make_video(tmp_path, "Nikon_001.MOV", base + timedelta(minutes=120)),
        ]
        groups = group_sequences(files)
        reordered = reorder_with_groups(files, groups)
        names = [vf.path.name for vf in reordered]
        assert names == [
            "iPhone_001.MOV",
            "GH010128.MP4",
            "GH020128.MP4",
            "GH030128.MP4",
            "iPhone_002.MOV",
            "Nikon_001.MOV",
        ]


class TestFadeMap:
    """compute_fade_map 테스트."""

    def test_standalone_fade(self, tmp_path) -> None:
        base = datetime(2025, 1, 1, 10, 0, 0)
        vf = _make_video(tmp_path, "clip.mp4", base)
        groups = group_sequences([vf])
        fade_map = compute_fade_map(groups)
        assert fade_map[vf.path] == FadeConfig(fade_in=0.5, fade_out=0.5)

    def test_group_fade_rules(self, tmp_path) -> None:
        base = datetime(2025, 1, 1, 10, 0, 0)
        files = [
            _make_video(tmp_path, "GH010128.MP4", base),
            _make_video(tmp_path, "GH020128.MP4", base + timedelta(minutes=10)),
            _make_video(tmp_path, "GH030128.MP4", base + timedelta(minutes=20)),
        ]
        groups = group_sequences(files)
        fade_map = compute_fade_map(groups, default_fade=0.75)
        assert fade_map[files[0].path] == FadeConfig(fade_in=0.75, fade_out=0.0)
        assert fade_map[files[1].path] == FadeConfig(fade_in=0.0, fade_out=0.0)
        assert fade_map[files[2].path] == FadeConfig(fade_in=0.0, fade_out=0.75)
