"""메타데이터 카탈로그 커맨드 테스트."""

from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from tubearchive.commands.catalog import (
    CATALOG_STATUS_SENTINEL,
    CATALOG_UNKNOWN_DEVICE,
    CATALOG_UNKNOWN_STATUS,
    STATUS_ICONS,
    CatalogSummary,
    VideoCatalogItem,
    build_catalog_summary,
    cmd_catalog,
    fetch_catalog_items,
    format_catalog_status,
    format_duration,
    normalize_device_label,
    normalize_status_filter,
    output_catalog,
    parse_creation_date,
    render_table,
)
from tubearchive.database.schema import init_database


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    """테스트용 DB 연결."""
    db_path = tmp_path / "test_catalog.db"
    conn = init_database(db_path)
    yield conn
    conn.close()


def _insert_video(
    conn: sqlite3.Connection,
    *,
    path: str = "/test/video.mp4",
    creation_time: str = "2026-01-15T10:30:00",
    duration: float | None = 120.5,
    device: str | None = "GoPro HERO12",
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO videos (original_path, creation_time, duration_seconds, device_model)
        VALUES (?, ?, ?, ?)
        """,
        (path, creation_time, duration, device),
    )
    conn.commit()
    return cursor.lastrowid or 0


def _insert_transcoding_job(
    conn: sqlite3.Connection,
    video_id: int,
    *,
    status: str = "completed",
    progress: int = 100,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO transcoding_jobs (video_id, status, progress_percent)
        VALUES (?, ?, ?)
        """,
        (video_id, status, progress),
    )
    conn.commit()
    return cursor.lastrowid or 0


class TestFormatDuration:
    """format_duration 테스트."""

    def test_seconds_only(self) -> None:
        assert format_duration(45) == "45s"

    def test_zero_seconds(self) -> None:
        assert format_duration(0) == "0s"

    def test_minutes_and_seconds(self) -> None:
        assert format_duration(90) == "1m 30s"

    def test_exact_minute(self) -> None:
        assert format_duration(60) == "1m 0s"

    def test_hours_and_minutes(self) -> None:
        assert format_duration(3720) == "1h 2m"

    def test_exact_hour(self) -> None:
        assert format_duration(3600) == "1h 0m"

    def test_float_truncated(self) -> None:
        assert format_duration(45.9) == "45s"

    def test_large_value(self) -> None:
        assert format_duration(7200) == "2h 0m"


class TestParseCreationDate:
    """parse_creation_date 테스트."""

    def test_valid_iso_datetime(self) -> None:
        assert parse_creation_date("2026-01-15T10:30:00") == "2026-01-15"

    def test_empty_string_returns_dash(self) -> None:
        assert parse_creation_date("") == "-"

    def test_invalid_format_returns_first_10_chars(self) -> None:
        assert parse_creation_date("not-a-date-string") == "not-a-date"

    def test_date_only_string(self) -> None:
        assert parse_creation_date("2026-01-15") == "2026-01-15"

    def test_datetime_with_timezone(self) -> None:
        assert parse_creation_date("2026-01-15T10:30:00+09:00") == "2026-01-15"


class TestNormalizeDeviceLabel:
    """normalize_device_label 테스트."""

    def test_none_returns_unknown(self) -> None:
        assert normalize_device_label(None) == CATALOG_UNKNOWN_DEVICE

    def test_empty_string_returns_unknown(self) -> None:
        assert normalize_device_label("") == CATALOG_UNKNOWN_DEVICE

    def test_whitespace_only_returns_unknown(self) -> None:
        assert normalize_device_label("   ") == CATALOG_UNKNOWN_DEVICE

    def test_normal_device_name(self) -> None:
        assert normalize_device_label("GoPro HERO12") == "GoPro HERO12"

    def test_strips_whitespace(self) -> None:
        assert normalize_device_label("  Nikon Z8  ") == "Nikon Z8"


class TestNormalizeStatusFilter:
    """normalize_status_filter 테스트."""

    def test_none_returns_none(self) -> None:
        assert normalize_status_filter(None) is None

    def test_sentinel_returns_none(self) -> None:
        assert normalize_status_filter(CATALOG_STATUS_SENTINEL) is None

    def test_lowercase_conversion(self) -> None:
        assert normalize_status_filter("COMPLETED") == "completed"

    def test_strips_whitespace(self) -> None:
        assert normalize_status_filter("  pending  ") == "pending"

    def test_normal_status(self) -> None:
        assert normalize_status_filter("failed") == "failed"


class TestFormatCatalogStatus:
    """format_catalog_status 테스트."""

    def test_untracked_returns_dash(self) -> None:
        assert format_catalog_status(CATALOG_UNKNOWN_STATUS) == "-"

    def test_known_statuses_return_icon(self) -> None:
        for status, icon in STATUS_ICONS.items():
            assert format_catalog_status(status) == icon

    def test_unknown_status_returns_raw(self) -> None:
        assert format_catalog_status("custom_status") == "custom_status"


class TestVideoCatalogItem:
    """VideoCatalogItem dataclass 테스트."""

    def test_creation(self) -> None:
        item = VideoCatalogItem(
            video_id=1,
            creation_time="2026-01-15T10:30:00",
            creation_date="2026-01-15",
            device="GoPro HERO12",
            duration_seconds=120.5,
            status="completed",
            progress_percent=100,
            path="/test/video.mp4",
        )
        assert item.video_id == 1
        assert item.device == "GoPro HERO12"

    def test_frozen(self) -> None:
        item = VideoCatalogItem(
            video_id=1,
            creation_time="",
            creation_date="-",
            device=CATALOG_UNKNOWN_DEVICE,
            duration_seconds=None,
            status=CATALOG_UNKNOWN_STATUS,
            progress_percent=None,
            path="/test/v.mp4",
        )
        with pytest.raises(AttributeError):
            item.video_id = 2  # type: ignore[misc]

    def test_optional_fields_none(self) -> None:
        item = VideoCatalogItem(
            video_id=1,
            creation_time="",
            creation_date="-",
            device=CATALOG_UNKNOWN_DEVICE,
            duration_seconds=None,
            status=CATALOG_UNKNOWN_STATUS,
            progress_percent=None,
            path="/test/v.mp4",
        )
        assert item.duration_seconds is None
        assert item.progress_percent is None


class TestFetchCatalogItems:
    """fetch_catalog_items DB 조회 테스트."""

    def test_empty_database(self, db_conn: sqlite3.Connection) -> None:
        items = fetch_catalog_items(db_conn, None, None, None, group_by_device=False)
        assert items == []

    def test_returns_all_videos(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/a.mp4", creation_time="2026-01-10T09:00:00")
        _insert_video(db_conn, path="/b.mp4", creation_time="2026-01-15T10:00:00")

        items = fetch_catalog_items(db_conn, None, None, None, group_by_device=False)
        assert len(items) == 2

    def test_ordered_by_creation_time_desc(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/old.mp4", creation_time="2026-01-01T00:00:00")
        _insert_video(db_conn, path="/new.mp4", creation_time="2026-01-31T00:00:00")

        items = fetch_catalog_items(db_conn, None, None, None, group_by_device=False)
        assert items[0].creation_date == "2026-01-31"
        assert items[1].creation_date == "2026-01-01"

    def test_search_pattern_filter(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/jan.mp4", creation_time="2026-01-15T10:00:00")
        _insert_video(db_conn, path="/feb.mp4", creation_time="2026-02-20T10:00:00")

        items = fetch_catalog_items(
            db_conn,
            search_pattern="2026-01",
            device_filter=None,
            status_filter=None,
            group_by_device=False,
        )
        assert len(items) == 1
        assert items[0].creation_date == "2026-01-15"

    def test_device_filter_case_insensitive(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/gopro.mp4", device="GoPro HERO12")
        _insert_video(db_conn, path="/nikon.mp4", device="Nikon Z8")

        items = fetch_catalog_items(
            db_conn,
            search_pattern=None,
            device_filter="gopro",
            status_filter=None,
            group_by_device=False,
        )
        assert len(items) == 1
        assert items[0].device == "GoPro HERO12"

    def test_status_filter_completed(self, db_conn: sqlite3.Connection) -> None:
        vid1 = _insert_video(db_conn, path="/done.mp4")
        vid2 = _insert_video(db_conn, path="/pending.mp4")
        _insert_transcoding_job(db_conn, vid1, status="completed")
        _insert_transcoding_job(db_conn, vid2, status="pending")

        items = fetch_catalog_items(
            db_conn,
            search_pattern=None,
            device_filter=None,
            status_filter="completed",
            group_by_device=False,
        )
        assert len(items) == 1
        assert items[0].status == "completed"

    def test_status_filter_untracked(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/no_job.mp4")
        vid2 = _insert_video(db_conn, path="/has_job.mp4")
        _insert_transcoding_job(db_conn, vid2, status="completed")

        items = fetch_catalog_items(
            db_conn,
            search_pattern=None,
            device_filter=None,
            status_filter=CATALOG_UNKNOWN_STATUS,
            group_by_device=False,
        )
        assert len(items) == 1
        assert items[0].status == CATALOG_UNKNOWN_STATUS

    def test_group_by_device_ordering(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/z.mp4", device="Nikon", creation_time="2026-01-01T00:00:00")
        _insert_video(db_conn, path="/a.mp4", device="GoPro", creation_time="2026-01-02T00:00:00")
        _insert_video(db_conn, path="/b.mp4", device="GoPro", creation_time="2026-01-03T00:00:00")

        items = fetch_catalog_items(
            db_conn,
            search_pattern=None,
            device_filter=None,
            status_filter=None,
            group_by_device=True,
        )
        assert items[0].device == "GoPro"
        assert items[1].device == "GoPro"
        assert items[2].device == "Nikon"

    def test_null_device_sorted_last(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/known.mp4", device="GoPro")
        _insert_video(db_conn, path="/unknown.mp4", device=None)

        items = fetch_catalog_items(
            db_conn,
            search_pattern=None,
            device_filter=None,
            status_filter=None,
            group_by_device=True,
        )
        assert items[0].device == "GoPro"
        assert items[1].device == CATALOG_UNKNOWN_DEVICE

    def test_latest_transcoding_job_used(self, db_conn: sqlite3.Connection) -> None:
        vid = _insert_video(db_conn, path="/multi_job.mp4")
        _insert_transcoding_job(db_conn, vid, status="failed", progress=0)
        _insert_transcoding_job(db_conn, vid, status="completed", progress=100)

        items = fetch_catalog_items(
            db_conn,
            search_pattern=None,
            device_filter=None,
            status_filter=None,
            group_by_device=False,
        )
        assert len(items) == 1
        assert items[0].status == "completed"
        assert items[0].progress_percent == 100

    def test_combined_filters(self, db_conn: sqlite3.Connection) -> None:
        vid1 = _insert_video(
            db_conn,
            path="/jan_gopro.mp4",
            creation_time="2026-01-15T10:00:00",
            device="GoPro",
        )
        _insert_video(
            db_conn,
            path="/jan_nikon.mp4",
            creation_time="2026-01-20T10:00:00",
            device="Nikon",
        )
        vid3 = _insert_video(
            db_conn,
            path="/feb_gopro.mp4",
            creation_time="2026-02-10T10:00:00",
            device="GoPro",
        )
        _insert_transcoding_job(db_conn, vid1, status="completed")
        _insert_transcoding_job(db_conn, vid3, status="pending")

        items = fetch_catalog_items(
            db_conn,
            search_pattern="2026-01",
            device_filter="GoPro",
            status_filter="completed",
            group_by_device=False,
        )
        assert len(items) == 1
        assert items[0].path == "/jan_gopro.mp4"

    def test_video_without_duration(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/no_dur.mp4", duration=None)

        items = fetch_catalog_items(
            db_conn,
            None,
            None,
            None,
            group_by_device=False,
        )
        assert len(items) == 1
        assert items[0].duration_seconds is None


class TestBuildCatalogSummary:
    """build_catalog_summary 테스트."""

    def test_empty_items(self) -> None:
        summary = build_catalog_summary([])
        assert summary["total"] == 0
        assert summary["devices"] == []
        assert summary["date_range"] is None

    def test_single_item(self) -> None:
        items = [
            VideoCatalogItem(
                video_id=1,
                creation_time="2026-01-15T10:00:00",
                creation_date="2026-01-15",
                device="GoPro",
                duration_seconds=60.0,
                status="completed",
                progress_percent=100,
                path="/a.mp4",
            )
        ]
        summary = build_catalog_summary(items)
        assert summary["total"] == 1
        assert len(summary["devices"]) == 1
        assert summary["devices"][0] == {"device": "GoPro", "count": 1}
        assert summary["date_range"] == {"start": "2026-01-15", "end": "2026-01-15"}

    def test_multiple_devices_sorted_by_count_desc(self) -> None:
        items = [
            VideoCatalogItem(
                video_id=1,
                creation_time="2026-01-10T00:00:00",
                creation_date="2026-01-10",
                device="GoPro",
                duration_seconds=60.0,
                status="completed",
                progress_percent=100,
                path="/a.mp4",
            ),
            VideoCatalogItem(
                video_id=2,
                creation_time="2026-01-11T00:00:00",
                creation_date="2026-01-11",
                device="GoPro",
                duration_seconds=60.0,
                status="completed",
                progress_percent=100,
                path="/b.mp4",
            ),
            VideoCatalogItem(
                video_id=3,
                creation_time="2026-01-12T00:00:00",
                creation_date="2026-01-12",
                device="Nikon",
                duration_seconds=60.0,
                status="completed",
                progress_percent=100,
                path="/c.mp4",
            ),
        ]
        summary = build_catalog_summary(items)
        assert summary["total"] == 3
        assert summary["devices"][0] == {"device": "GoPro", "count": 2}
        assert summary["devices"][1] == {"device": "Nikon", "count": 1}

    def test_date_range_min_max(self) -> None:
        items = [
            VideoCatalogItem(
                video_id=1,
                creation_time="",
                creation_date="2026-01-01",
                device="A",
                duration_seconds=None,
                status="",
                progress_percent=None,
                path="/a.mp4",
            ),
            VideoCatalogItem(
                video_id=2,
                creation_time="",
                creation_date="2026-03-15",
                device="A",
                duration_seconds=None,
                status="",
                progress_percent=None,
                path="/b.mp4",
            ),
            VideoCatalogItem(
                video_id=3,
                creation_time="",
                creation_date="2026-02-10",
                device="A",
                duration_seconds=None,
                status="",
                progress_percent=None,
                path="/c.mp4",
            ),
        ]
        summary = build_catalog_summary(items)
        assert summary["date_range"] is not None
        assert summary["date_range"]["start"] == "2026-01-01"
        assert summary["date_range"]["end"] == "2026-03-15"

    def test_invalid_dates_ignored(self) -> None:
        items = [
            VideoCatalogItem(
                video_id=1,
                creation_time="",
                creation_date="-",
                device="A",
                duration_seconds=None,
                status="",
                progress_percent=None,
                path="/a.mp4",
            ),
            VideoCatalogItem(
                video_id=2,
                creation_time="",
                creation_date="2026-01-15",
                device="A",
                duration_seconds=None,
                status="",
                progress_percent=None,
                path="/b.mp4",
            ),
        ]
        summary = build_catalog_summary(items)
        assert summary["date_range"] == {"start": "2026-01-15", "end": "2026-01-15"}

    def test_all_invalid_dates_gives_none_range(self) -> None:
        items = [
            VideoCatalogItem(
                video_id=1,
                creation_time="",
                creation_date="-",
                device="A",
                duration_seconds=None,
                status="",
                progress_percent=None,
                path="/a.mp4",
            ),
            VideoCatalogItem(
                video_id=2,
                creation_time="",
                creation_date="invalid",
                device="A",
                duration_seconds=None,
                status="",
                progress_percent=None,
                path="/b.mp4",
            ),
        ]
        summary = build_catalog_summary(items)
        assert summary["date_range"] is None


class TestRenderTable:
    """render_table 테스트."""

    def test_empty_rows(self, capsys: pytest.CaptureFixture[str]) -> None:
        render_table(["A", "B"], [])
        captured = capsys.readouterr()
        assert "결과 없음" in captured.out

    def test_basic_table(self, capsys: pytest.CaptureFixture[str]) -> None:
        render_table(["Name", "Value"], [["foo", "bar"], ["baz", "qux"]])
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert len(lines) == 4
        assert "Name" in lines[0]
        assert "---" in lines[1]
        assert "foo" in lines[2]

    def test_right_alignment(self, capsys: pytest.CaptureFixture[str]) -> None:
        render_table(["ID"], [["1"], ["10"]], aligns=["right"])
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert lines[2].strip() == "1"
        assert lines[3].strip() == "10"


class TestOutputCatalog:
    """output_catalog 출력 형식 테스트."""

    @pytest.fixture
    def sample_items(self) -> list[VideoCatalogItem]:
        return [
            VideoCatalogItem(
                video_id=1,
                creation_time="2026-01-15T10:30:00",
                creation_date="2026-01-15",
                device="GoPro HERO12",
                duration_seconds=120.5,
                status="completed",
                progress_percent=100,
                path="/test/video1.mp4",
            ),
            VideoCatalogItem(
                video_id=2,
                creation_time="2026-01-16T11:00:00",
                creation_date="2026-01-16",
                device="Nikon Z8",
                duration_seconds=300.0,
                status="pending",
                progress_percent=0,
                path="/test/video2.mp4",
            ),
        ]

    @pytest.fixture
    def sample_summary(self) -> CatalogSummary:
        return {
            "total": 2,
            "devices": [
                {"device": "GoPro HERO12", "count": 1},
                {"device": "Nikon Z8", "count": 1},
            ],
            "date_range": {"start": "2026-01-15", "end": "2026-01-16"},
        }

    def test_json_output(
        self,
        sample_items: list[VideoCatalogItem],
        sample_summary: CatalogSummary,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output_catalog(sample_items, sample_summary, "json", group_by_device=False)
        captured = capsys.readouterr()
        data = json.loads(captured.out)

        assert data["summary"]["total"] == 2
        assert len(data["items"]) == 2
        assert data["items"][0]["id"] == 1
        assert data["items"][0]["device"] == "GoPro HERO12"
        assert "grouped_by_device" not in data

    def test_json_output_with_group_flag(
        self,
        sample_items: list[VideoCatalogItem],
        sample_summary: CatalogSummary,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output_catalog(sample_items, sample_summary, "json", group_by_device=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)

        assert data["grouped_by_device"] is True

    def test_csv_output(
        self,
        sample_items: list[VideoCatalogItem],
        sample_summary: CatalogSummary,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output_catalog(sample_items, sample_summary, "csv", group_by_device=False)
        captured = capsys.readouterr()

        reader = csv.reader(io.StringIO(captured.out))
        rows = list(reader)
        assert len(rows) == 3
        assert rows[0][0] == "id"
        assert rows[1][0] == "1"
        assert rows[2][0] == "2"

        assert "총 영상 2개" in captured.err

    def test_table_output(
        self,
        sample_items: list[VideoCatalogItem],
        sample_summary: CatalogSummary,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output_catalog(sample_items, sample_summary, "table", group_by_device=False)
        captured = capsys.readouterr()

        assert "ID" in captured.out
        assert "Date" in captured.out
        assert "GoPro HERO12" in captured.out
        assert "Nikon Z8" in captured.out

    def test_table_grouped_output(
        self,
        sample_items: list[VideoCatalogItem],
        sample_summary: CatalogSummary,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output_catalog(sample_items, sample_summary, "table", group_by_device=True)
        captured = capsys.readouterr()

        assert "기기: GoPro HERO12" in captured.out
        assert "기기: Nikon Z8" in captured.out

    def test_empty_items_table(
        self,
        sample_summary: CatalogSummary,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        output_catalog([], sample_summary, "table", group_by_device=False)
        captured = capsys.readouterr()

        assert "결과 없음" in captured.out


class TestCmdCatalog:
    """cmd_catalog 통합 테스트."""

    def _make_args(
        self,
        *,
        catalog: bool = False,
        search: str | None = None,
        device: str | None = None,
        status: str | None = None,
        output_json: bool = False,
        output_csv: bool = False,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            catalog=catalog,
            search=search,
            device=device,
            status=status,
            json=output_json,
            csv=output_csv,
        )

    def test_catalog_with_empty_db(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        with patch("tubearchive.commands.catalog.init_database", return_value=db_conn):
            args = self._make_args(catalog=True)
            cmd_catalog(args)

        captured = capsys.readouterr()
        assert "결과 없음" in captured.out

    def test_catalog_shows_videos(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _insert_video(db_conn, path="/test/v1.mp4", device="GoPro")
        _insert_video(db_conn, path="/test/v2.mp4", device="Nikon")

        with patch("tubearchive.commands.catalog.init_database", return_value=db_conn):
            args = self._make_args(catalog=True)
            cmd_catalog(args)

        captured = capsys.readouterr()
        assert "GoPro" in captured.out
        assert "Nikon" in captured.out

    def test_search_filters_by_date(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _insert_video(db_conn, path="/jan.mp4", creation_time="2026-01-15T10:00:00")
        _insert_video(db_conn, path="/feb.mp4", creation_time="2026-02-20T10:00:00")

        with patch("tubearchive.commands.catalog.init_database", return_value=db_conn):
            args = self._make_args(search="2026-01")
            cmd_catalog(args)

        captured = capsys.readouterr()
        assert "2026-01-15" in captured.out
        assert "2026-02-20" not in captured.out

    def test_search_with_device_filter(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _insert_video(db_conn, path="/gopro.mp4", device="GoPro")
        _insert_video(db_conn, path="/nikon.mp4", device="Nikon")

        with patch("tubearchive.commands.catalog.init_database", return_value=db_conn):
            args = self._make_args(search="", device="GoPro")
            cmd_catalog(args)

        captured = capsys.readouterr()
        assert "GoPro" in captured.out
        assert "Nikon" not in captured.out

    def test_json_output_format(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _insert_video(db_conn, path="/test.mp4", device="iPhone")

        with patch("tubearchive.commands.catalog.init_database", return_value=db_conn):
            args = self._make_args(catalog=True, output_json=True)
            cmd_catalog(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["summary"]["total"] == 1
        assert data["items"][0]["device"] == "iPhone"

    def test_csv_output_format(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _insert_video(db_conn, path="/test.mp4", device="DJI")

        with patch("tubearchive.commands.catalog.init_database", return_value=db_conn):
            args = self._make_args(catalog=True, output_csv=True)
            cmd_catalog(args)

        captured = capsys.readouterr()
        reader = csv.reader(io.StringIO(captured.out))
        rows = list(reader)
        assert rows[0][0] == "id"
        assert len(rows) == 2

    def test_invalid_status_raises_error(self, db_conn: sqlite3.Connection) -> None:
        with patch("tubearchive.commands.catalog.init_database", return_value=db_conn):
            args = self._make_args(search="", status="invalid_status")
            with pytest.raises(ValueError, match="잘못된 상태 필터"):
                cmd_catalog(args)

    def test_status_filter_completed(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        vid1 = _insert_video(db_conn, path="/done.mp4")
        vid2 = _insert_video(db_conn, path="/wait.mp4")
        _insert_transcoding_job(db_conn, vid1, status="completed")
        _insert_transcoding_job(db_conn, vid2, status="pending")

        with patch("tubearchive.commands.catalog.init_database", return_value=db_conn):
            args = self._make_args(search="", status="completed", output_json=True)
            cmd_catalog(args)

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["summary"]["total"] == 1
        assert data["items"][0]["status"] == "completed"

    def test_search_empty_string_shows_all(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _insert_video(db_conn, path="/a.mp4")
        _insert_video(db_conn, path="/b.mp4")

        with patch("tubearchive.commands.catalog.init_database", return_value=db_conn):
            args = self._make_args(search="")
            cmd_catalog(args)

        captured = capsys.readouterr()
        assert "/a.mp4" in captured.out
        assert "/b.mp4" in captured.out
