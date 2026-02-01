"""클립 순서 편집 모듈 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tubearchive.core.ordering import (
    SortKey,
    _cmd_move,
    _cmd_remove,
    _cmd_reorder_by_numbers,
    _cmd_swap,
    _matches_any_pattern,
    _process_reorder_command,
    filter_videos,
    interactive_reorder,
    sort_videos,
)
from tubearchive.models.video import VideoFile


def _make_video(
    tmp_path: Path,
    name: str,
    created_at: datetime,
    size_bytes: int = 1024,
) -> VideoFile:
    path = tmp_path / name
    path.write_bytes(b"0" * min(size_bytes, 1))
    return VideoFile(path=path, creation_time=created_at, size_bytes=size_bytes)


class TestMatchesAnyPattern:
    def test_exact_match(self) -> None:
        assert _matches_any_pattern("GH010042.MP4", ["GH010042.MP4"]) is True

    def test_glob_wildcard(self) -> None:
        assert _matches_any_pattern("GH010042.MP4", ["GH*"]) is True

    def test_extension_pattern(self) -> None:
        assert _matches_any_pattern("video.mts", ["*.mts"]) is True

    def test_no_match(self) -> None:
        assert _matches_any_pattern("video.mp4", ["*.mts"]) is False

    def test_case_insensitive(self) -> None:
        assert _matches_any_pattern("VIDEO.MP4", ["*.mp4"]) is True

    def test_multiple_patterns(self) -> None:
        assert _matches_any_pattern("GH010042.MP4", ["*.mts", "GH*"]) is True

    def test_empty_patterns(self) -> None:
        assert _matches_any_pattern("video.mp4", []) is False


class TestFilterVideos:
    def test_empty_list(self) -> None:
        assert filter_videos([]) == []

    def test_no_patterns_returns_original(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        result = filter_videos(videos)
        assert result == videos

    def test_exclude_pattern(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "GH010042.MP4", base)
        v2 = _make_video(tmp_path, "IMG_0001.MOV", base)
        result = filter_videos([v1, v2], exclude_patterns=["GH*"])
        assert result == [v2]

    def test_include_only_pattern(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "clip.mp4", base)
        v2 = _make_video(tmp_path, "clip.mts", base)
        result = filter_videos([v1, v2], include_only_patterns=["*.mp4"])
        assert result == [v1]

    def test_combined_include_and_exclude(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "GH010042.MP4", base)
        v2 = _make_video(tmp_path, "IMG_0001.MP4", base)
        v3 = _make_video(tmp_path, "clip.mts", base)
        result = filter_videos(
            [v1, v2, v3],
            include_only_patterns=["*.MP4"],
            exclude_patterns=["GH*"],
        )
        assert result == [v2]

    def test_exclude_all_returns_empty(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        result = filter_videos(videos, exclude_patterns=["*"])
        assert result == []

    def test_preserves_order(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "b.mp4", base)
        v2 = _make_video(tmp_path, "a.mp4", base)
        v3 = _make_video(tmp_path, "c.mp4", base)
        result = filter_videos([v1, v2, v3])
        assert [v.path.name for v in result] == ["b.mp4", "a.mp4", "c.mp4"]


class TestSortVideos:
    def test_empty_list(self) -> None:
        assert sort_videos([]) == []

    def test_sort_by_time(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base + timedelta(hours=2))
        v2 = _make_video(tmp_path, "b.mp4", base)
        v3 = _make_video(tmp_path, "c.mp4", base + timedelta(hours=1))
        result = sort_videos([v1, v2, v3], SortKey.TIME)
        assert [v.path.name for v in result] == ["b.mp4", "c.mp4", "a.mp4"]

    def test_sort_by_name(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "charlie.mp4", base)
        v2 = _make_video(tmp_path, "alpha.mp4", base)
        v3 = _make_video(tmp_path, "bravo.mp4", base)
        result = sort_videos([v1, v2, v3], SortKey.NAME)
        assert [v.path.name for v in result] == ["alpha.mp4", "bravo.mp4", "charlie.mp4"]

    def test_sort_by_size(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "big.mp4", base, size_bytes=3000)
        v2 = _make_video(tmp_path, "small.mp4", base, size_bytes=100)
        v3 = _make_video(tmp_path, "mid.mp4", base, size_bytes=1500)
        result = sort_videos([v1, v2, v3], SortKey.SIZE)
        assert [v.path.name for v in result] == ["small.mp4", "mid.mp4", "big.mp4"]

    def test_sort_by_device(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base + timedelta(hours=1))
        device_map = {v1.path: "Nikon", v2.path: "GoPro"}

        def detector(p: Path) -> str | None:
            return device_map.get(p)

        result = sort_videos([v1, v2], SortKey.DEVICE, device_detector=detector)
        assert [v.path.name for v in result] == ["b.mp4", "a.mp4"]

    def test_sort_by_time_reverse(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base + timedelta(hours=1))
        result = sort_videos([v1, v2], SortKey.TIME, reverse=True)
        assert [v.path.name for v in result] == ["b.mp4", "a.mp4"]

    def test_returns_new_list(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        result = sort_videos(videos, SortKey.TIME)
        assert result is not videos


class TestCmdSwap:
    def test_swap_valid(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        result = _cmd_swap([v1, v2], "1", "2")
        assert result is not None
        assert result[0] is v2
        assert result[1] is v1

    def test_swap_out_of_range(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        assert _cmd_swap(videos, "1", "5") is None

    def test_swap_non_numeric(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        assert _cmd_swap(videos, "x", "1") is None


class TestCmdMove:
    def test_move_valid(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        v3 = _make_video(tmp_path, "c.mp4", base)
        result = _cmd_move([v1, v2, v3], "3", "1")
        assert result is not None
        assert [v.path.name for v in result] == ["c.mp4", "a.mp4", "b.mp4"]

    def test_move_out_of_range(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        assert _cmd_move(videos, "1", "5") is None

    def test_move_non_numeric(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        assert _cmd_move(videos, "x", "1") is None


class TestCmdRemove:
    def test_remove_valid(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        result = _cmd_remove([v1, v2], "1")
        assert result is not None
        assert len(result) == 1
        assert result[0] is v2

    def test_remove_last_returns_none(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        assert _cmd_remove(videos, "1") is None

    def test_remove_out_of_range(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        assert _cmd_remove(videos, "5") is None

    def test_remove_non_numeric(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        assert _cmd_remove(videos, "x") is None


class TestCmdReorderByNumbers:
    def test_full_reorder(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        v3 = _make_video(tmp_path, "c.mp4", base)
        result = _cmd_reorder_by_numbers([v1, v2, v3], "3,1,2")
        assert result is not None
        assert [v.path.name for v in result] == ["c.mp4", "a.mp4", "b.mp4"]

    def test_space_separated(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        result = _cmd_reorder_by_numbers([v1, v2], "2 1")
        assert result is not None
        assert [v.path.name for v in result] == ["b.mp4", "a.mp4"]

    def test_incomplete_count_returns_none(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        assert _cmd_reorder_by_numbers([v1, v2], "1") is None

    def test_duplicate_numbers_returns_none(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        assert _cmd_reorder_by_numbers([v1, v2], "1,1") is None

    def test_out_of_range_returns_none(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        assert _cmd_reorder_by_numbers(videos, "5") is None

    def test_non_numeric_returns_none(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        assert _cmd_reorder_by_numbers(videos, "abc") is None


class TestProcessReorderCommand:
    def test_swap_command(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        result = _process_reorder_command([v1, v2], "swap 1 2")
        assert result is not None
        assert result[0] is v2

    def test_move_command(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        v3 = _make_video(tmp_path, "c.mp4", base)
        result = _process_reorder_command([v1, v2, v3], "move 3 1")
        assert result is not None

    def test_remove_command(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        result = _process_reorder_command([v1, v2], "remove 1")
        assert result is not None
        assert len(result) == 1

    def test_number_reorder_command(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        result = _process_reorder_command([v1, v2], "2,1")
        assert result is not None

    def test_empty_command(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        assert _process_reorder_command(videos, "") is None

    def test_case_insensitive_commands(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        result = _process_reorder_command([v1, v2], "SWAP 1 2")
        assert result is not None


class TestInteractiveReorder:
    def test_empty_list(self) -> None:
        assert interactive_reorder([]) == []

    def test_done_command(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        inputs = iter(["done"])
        result = interactive_reorder(videos, input_fn=lambda _: next(inputs))
        assert result == videos

    def test_empty_input_exits(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]
        inputs = iter([""])
        result = interactive_reorder(videos, input_fn=lambda _: next(inputs))
        assert result == videos

    def test_swap_then_done(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        v1 = _make_video(tmp_path, "a.mp4", base)
        v2 = _make_video(tmp_path, "b.mp4", base)
        inputs = iter(["swap 1 2", "done"])
        result = interactive_reorder([v1, v2], input_fn=lambda _: next(inputs))
        assert result[0] is v2
        assert result[1] is v1

    def test_eof_returns_original(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]

        def raise_eof(_: str) -> str:
            raise EOFError

        result = interactive_reorder(videos, input_fn=raise_eof)
        assert result == videos

    def test_keyboard_interrupt_returns_original(self, tmp_path: Path) -> None:
        base = datetime(2025, 1, 1)
        videos = [_make_video(tmp_path, "a.mp4", base)]

        def raise_interrupt(_: str) -> str:
            raise KeyboardInterrupt

        result = interactive_reorder(videos, input_fn=raise_interrupt)
        assert result == videos


class TestSortKeyEnum:
    def test_values(self) -> None:
        assert SortKey.TIME.value == "time"
        assert SortKey.NAME.value == "name"
        assert SortKey.SIZE.value == "size"
        assert SortKey.DEVICE.value == "device"

    def test_from_string(self) -> None:
        assert SortKey("time") == SortKey.TIME
        assert SortKey("name") == SortKey.NAME

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            SortKey("invalid")
