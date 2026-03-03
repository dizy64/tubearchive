"""통계 대시보드 커맨드 테스트."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tubearchive.app.queries.stats import (
    BAR_CHAR,
    ArchiveStats,
    DeviceStat,
    MergeStats,
    StatsData,
    TranscodingStats,
    cmd_stats,
    fetch_stats,
    render_bar_chart,
    render_stats,
)
from tubearchive.infra.db.repository import (
    ArchiveHistoryRepository,
    MergeJobRepository,
    TranscodingJobRepository,
    VideoRepository,
)
from tubearchive.infra.db.schema import init_database


@pytest.fixture
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    """테스트용 DB 연결."""
    db_path = tmp_path / "test_stats.db"
    conn = init_database(db_path)
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# 헬퍼: 테스트 데이터 삽입
# ---------------------------------------------------------------------------


def _insert_video(
    conn: sqlite3.Connection,
    *,
    path: str = "/test/video.mp4",
    creation_time: str = "2026-01-15T10:30:00",
    duration: float | None = 120.0,
    device: str | None = "GoPro HERO12",
) -> int:
    cursor = conn.execute(
        """INSERT INTO videos (original_path, creation_time, duration_seconds, device_model)
           VALUES (?, ?, ?, ?)""",
        (path, creation_time, duration, device),
    )
    conn.commit()
    return cursor.lastrowid or 0


def _insert_transcoding_job(
    conn: sqlite3.Connection,
    video_id: int,
    *,
    status: str = "completed",
    started_at: str | None = None,
    completed_at: str | None = None,
) -> int:
    cursor = conn.execute(
        """INSERT INTO transcoding_jobs (video_id, status, started_at, completed_at)
           VALUES (?, ?, ?, ?)""",
        (video_id, status, started_at, completed_at),
    )
    conn.commit()
    return cursor.lastrowid or 0


def _insert_merge_job(
    conn: sqlite3.Connection,
    *,
    output_path: str = "/out/merged.mp4",
    video_ids: str = "[1]",
    status: str = "completed",
    youtube_id: str | None = None,
    total_size_bytes: int | None = None,
    total_duration: float | None = None,
    created_at: str | None = None,
) -> int:
    if created_at is None:
        created_at = datetime.now().isoformat()
    cursor = conn.execute(
        """INSERT INTO merge_jobs
           (output_path, video_ids, status, youtube_id, total_size_bytes,
            total_duration_seconds, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (output_path, video_ids, status, youtube_id, total_size_bytes, total_duration, created_at),
    )
    conn.commit()
    return cursor.lastrowid or 0


def _insert_archive_history(
    conn: sqlite3.Connection,
    video_id: int,
    *,
    operation: str = "move",
    original_path: str = "/test/video.mp4",
    destination_path: str | None = "/archive/video.mp4",
    archived_at: str | None = None,
) -> int:
    if archived_at is None:
        archived_at = datetime.now().isoformat()
    cursor = conn.execute(
        """INSERT INTO archive_history
           (video_id, operation, original_path, destination_path, archived_at)
           VALUES (?, ?, ?, ?, ?)""",
        (video_id, operation, original_path, destination_path, archived_at),
    )
    conn.commit()
    return cursor.lastrowid or 0


# ---------------------------------------------------------------------------
# DeviceStat 모델 테스트
# ---------------------------------------------------------------------------


class TestDeviceStat:
    """DeviceStat dataclass 테스트."""

    def test_creation(self) -> None:
        stat = DeviceStat(device="GoPro HERO12", count=10)
        assert stat.device == "GoPro HERO12"
        assert stat.count == 10

    def test_frozen(self) -> None:
        stat = DeviceStat(device="GoPro", count=5)
        with pytest.raises(AttributeError):
            stat.count = 10  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TranscodingStats 모델 테스트
# ---------------------------------------------------------------------------


class TestTranscodingStats:
    """TranscodingStats dataclass 테스트."""

    def test_creation_with_speed(self) -> None:
        stats = TranscodingStats(
            completed=10, failed=2, pending=1, processing=0, total=13, avg_encoding_speed=2.5
        )
        assert stats.completed == 10
        assert stats.avg_encoding_speed == 2.5

    def test_no_encoding_speed(self) -> None:
        stats = TranscodingStats(
            completed=0, failed=0, pending=0, processing=0, total=0, avg_encoding_speed=None
        )
        assert stats.avg_encoding_speed is None


# ---------------------------------------------------------------------------
# StatsData 모델 테스트
# ---------------------------------------------------------------------------


class TestStatsData:
    """StatsData dataclass 테스트."""

    def test_creation_full(self) -> None:
        data = StatsData(
            period="2026-01",
            total_videos=50,
            total_duration=36000.0,
            devices=[DeviceStat("GoPro", 30), DeviceStat("Nikon", 20)],
            transcoding=TranscodingStats(40, 5, 3, 2, 50, 3.0),
            merge=MergeStats(10, 9, 1, 5, 1_000_000_000, 18000.0),
            archive=ArchiveStats(15, 3),
        )
        assert data.period == "2026-01"
        assert data.total_videos == 50
        assert len(data.devices) == 2

    def test_creation_empty(self) -> None:
        data = StatsData(
            period=None,
            total_videos=0,
            total_duration=0.0,
            devices=[],
            transcoding=TranscodingStats(0, 0, 0, 0, 0, None),
            merge=MergeStats(0, 0, 0, 0, 0, 0.0),
            archive=ArchiveStats(0, 0),
        )
        assert data.total_videos == 0
        assert data.devices == []


# ---------------------------------------------------------------------------
# render_bar_chart 테스트
# ---------------------------------------------------------------------------


class TestRenderBarChart:
    """render_bar_chart 텍스트 막대 차트 테스트."""

    def test_empty_items(self) -> None:
        assert render_bar_chart([]) == []

    def test_single_item(self) -> None:
        items = [DeviceStat("GoPro", 10)]
        lines = render_bar_chart(items)
        assert len(lines) == 1
        assert "GoPro" in lines[0]
        assert "10" in lines[0]
        assert "100%" in lines[0]
        assert BAR_CHAR in lines[0]

    def test_multiple_items_proportional_bars(self) -> None:
        items = [DeviceStat("GoPro", 60), DeviceStat("Nikon", 30), DeviceStat("DJI", 10)]
        lines = render_bar_chart(items, max_width=20)
        assert len(lines) == 3
        # GoPro는 가장 긴 막대 (max_width)
        gopro_bar_count = lines[0].count(BAR_CHAR)
        nikon_bar_count = lines[1].count(BAR_CHAR)
        dji_bar_count = lines[2].count(BAR_CHAR)
        assert gopro_bar_count == 20  # max
        assert nikon_bar_count == 10  # 30/60 * 20
        assert dji_bar_count >= 1  # 최소 1

    def test_percentage_values(self) -> None:
        items = [DeviceStat("A", 75), DeviceStat("B", 25)]
        lines = render_bar_chart(items)
        assert "75%" in lines[0]
        assert "25%" in lines[1]

    def test_all_zero_counts(self) -> None:
        items = [DeviceStat("A", 0)]
        assert render_bar_chart(items) == []

    def test_label_alignment(self) -> None:
        items = [DeviceStat("GoPro HERO12 Black", 10), DeviceStat("DJI", 5)]
        lines = render_bar_chart(items)
        # 짧은 라벨도 동일한 너비로 정렬되어야 함
        assert len(lines) == 2
        # 두 줄의 첫 번째 BAR_CHAR 위치가 같아야 함
        pos0 = lines[0].index(BAR_CHAR)
        pos1 = lines[1].index(BAR_CHAR)
        assert pos0 == pos1

    def test_custom_max_width(self) -> None:
        items = [DeviceStat("A", 100)]
        lines = render_bar_chart(items, max_width=10)
        assert lines[0].count(BAR_CHAR) == 10

    def test_minimum_bar_length(self) -> None:
        """매우 작은 비율이라도 최소 1칸은 표시."""
        items = [DeviceStat("Big", 1000), DeviceStat("Tiny", 1)]
        lines = render_bar_chart(items, max_width=30)
        tiny_bar = lines[1].count(BAR_CHAR)
        assert tiny_bar >= 1


# ---------------------------------------------------------------------------
# render_stats 테스트
# ---------------------------------------------------------------------------


class TestRenderStats:
    """render_stats 렌더링 테스트."""

    @pytest.fixture
    def full_data(self) -> StatsData:
        return StatsData(
            period=None,
            total_videos=100,
            total_duration=36000.0,
            devices=[DeviceStat("GoPro", 60), DeviceStat("Nikon", 40)],
            transcoding=TranscodingStats(90, 5, 3, 2, 100, 2.5),
            merge=MergeStats(20, 18, 2, 10, 5_000_000_000, 72000.0),
            archive=ArchiveStats(30, 5),
        )

    @pytest.fixture
    def empty_data(self) -> StatsData:
        return StatsData(
            period=None,
            total_videos=0,
            total_duration=0.0,
            devices=[],
            transcoding=TranscodingStats(0, 0, 0, 0, 0, None),
            merge=MergeStats(0, 0, 0, 0, 0, 0.0),
            archive=ArchiveStats(0, 0),
        )

    def test_full_data_contains_all_sections(self, full_data: StatsData) -> None:
        output = render_stats(full_data)
        assert "통계 대시보드" in output
        assert "전체 요약" in output
        assert "100개" in output
        assert "트랜스코딩" in output
        assert "90.0%" in output  # 성공률
        assert "2.5x" in output  # 인코딩 속도
        assert "병합" in output
        assert "기기별 분포" in output
        assert "GoPro" in output
        assert "아카이브" in output
        assert "이동: 30건" in output

    def test_empty_data_no_sections(self, empty_data: StatsData) -> None:
        output = render_stats(empty_data)
        assert "통계 대시보드" in output
        assert "전체 요약" in output
        assert "0개" in output
        # 빈 데이터에는 트랜스코딩/병합/기기별/아카이브 섹션이 없어야 함
        assert "트랜스코딩" not in output
        assert "기기별 분포" not in output
        assert "아카이브" not in output

    def test_period_label_shown(self) -> None:
        data = StatsData(
            period="2026-01",
            total_videos=0,
            total_duration=0.0,
            devices=[],
            transcoding=TranscodingStats(0, 0, 0, 0, 0, None),
            merge=MergeStats(0, 0, 0, 0, 0, 0.0),
            archive=ArchiveStats(0, 0),
        )
        output = render_stats(data)
        assert "기간: 2026-01" in output

    def test_no_period_label_when_none(self, full_data: StatsData) -> None:
        output = render_stats(full_data)
        assert "기간:" not in output

    def test_encoding_speed_hidden_when_none(self) -> None:
        data = StatsData(
            period=None,
            total_videos=5,
            total_duration=600.0,
            devices=[],
            transcoding=TranscodingStats(5, 0, 0, 0, 5, None),
            merge=MergeStats(0, 0, 0, 0, 0, 0.0),
            archive=ArchiveStats(0, 0),
        )
        output = render_stats(data)
        assert "인코딩 속도" not in output

    def test_output_size_shown_when_nonzero(self, full_data: StatsData) -> None:
        output = render_stats(full_data)
        assert "출력 크기" in output

    def test_output_size_hidden_when_zero(self, empty_data: StatsData) -> None:
        output = render_stats(empty_data)
        assert "출력 크기" not in output

    def test_merge_section_hidden_when_zero(self) -> None:
        data = StatsData(
            period=None,
            total_videos=5,
            total_duration=100.0,
            devices=[DeviceStat("A", 5)],
            transcoding=TranscodingStats(5, 0, 0, 0, 5, None),
            merge=MergeStats(0, 0, 0, 0, 0, 0.0),
            archive=ArchiveStats(0, 0),
        )
        output = render_stats(data)
        assert "📦 병합" not in output

    def test_archive_section_hidden_when_zero(self, empty_data: StatsData) -> None:
        output = render_stats(empty_data)
        assert "아카이브" not in output

    def test_success_rate_100_percent(self) -> None:
        data = StatsData(
            period=None,
            total_videos=10,
            total_duration=1000.0,
            devices=[],
            transcoding=TranscodingStats(10, 0, 0, 0, 10, None),
            merge=MergeStats(0, 0, 0, 0, 0, 0.0),
            archive=ArchiveStats(0, 0),
        )
        output = render_stats(data)
        assert "100.0%" in output

    def test_success_rate_zero_when_all_failed(self) -> None:
        data = StatsData(
            period=None,
            total_videos=5,
            total_duration=500.0,
            devices=[],
            transcoding=TranscodingStats(0, 5, 0, 0, 5, None),
            merge=MergeStats(0, 0, 0, 0, 0, 0.0),
            archive=ArchiveStats(0, 0),
        )
        output = render_stats(data)
        assert "0.0%" in output


# ---------------------------------------------------------------------------
# Repository.get_stats() 테스트
# ---------------------------------------------------------------------------


class TestVideoRepositoryGetStats:
    """VideoRepository.get_stats() 테스트."""

    def test_empty_db(self, db_conn: sqlite3.Connection) -> None:
        stats = VideoRepository(db_conn).get_stats()
        assert stats["total"] == 0
        assert stats["total_duration"] == 0.0
        assert stats["devices"] == []

    def test_counts_and_duration(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/a.mp4", duration=60.0, device="GoPro")
        _insert_video(db_conn, path="/b.mp4", duration=120.0, device="Nikon")
        stats = VideoRepository(db_conn).get_stats()
        assert stats["total"] == 2
        assert stats["total_duration"] == 180.0

    def test_device_distribution(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/a.mp4", device="GoPro")
        _insert_video(db_conn, path="/b.mp4", device="GoPro")
        _insert_video(db_conn, path="/c.mp4", device="Nikon")
        stats = VideoRepository(db_conn).get_stats()
        devices = stats["devices"]
        assert isinstance(devices, list)
        assert len(devices) == 2
        # 내림차순
        assert devices[0] == ("GoPro", 2)
        assert devices[1] == ("Nikon", 1)

    def test_null_device_label(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/a.mp4", device=None)
        stats = VideoRepository(db_conn).get_stats()
        devices = stats["devices"]
        assert len(devices) == 1
        assert devices[0] == ("미상", 1)

    def test_null_duration_treated_as_zero(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/a.mp4", duration=None)
        _insert_video(db_conn, path="/b.mp4", duration=60.0)
        stats = VideoRepository(db_conn).get_stats()
        assert stats["total_duration"] == 60.0

    def test_period_filter(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/jan.mp4", creation_time="2026-01-15T10:00:00", device="A")
        _insert_video(db_conn, path="/feb.mp4", creation_time="2026-02-20T10:00:00", device="B")
        stats = VideoRepository(db_conn).get_stats(period="2026-01")
        assert stats["total"] == 1
        devices = stats["devices"]
        assert len(devices) == 1
        assert devices[0][0] == "A"

    def test_period_filter_no_match(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/a.mp4", creation_time="2026-01-15T10:00:00")
        stats = VideoRepository(db_conn).get_stats(period="2025-12")
        assert stats["total"] == 0

    def test_period_year_only(self, db_conn: sqlite3.Connection) -> None:
        _insert_video(db_conn, path="/a.mp4", creation_time="2026-01-15T10:00:00")
        _insert_video(db_conn, path="/b.mp4", creation_time="2025-12-31T23:59:59")
        stats = VideoRepository(db_conn).get_stats(period="2026")
        assert stats["total"] == 1


class TestTranscodingJobRepositoryGetStats:
    """TranscodingJobRepository.get_stats() 테스트."""

    def test_empty_db(self, db_conn: sqlite3.Connection) -> None:
        stats = TranscodingJobRepository(db_conn).get_stats()
        assert stats["status_counts"] == {}
        assert stats["avg_encoding_speed"] is None

    def test_status_counts(self, db_conn: sqlite3.Connection) -> None:
        vid1 = _insert_video(db_conn, path="/a.mp4")
        vid2 = _insert_video(db_conn, path="/b.mp4")
        vid3 = _insert_video(db_conn, path="/c.mp4")
        _insert_transcoding_job(db_conn, vid1, status="completed")
        _insert_transcoding_job(db_conn, vid2, status="failed")
        _insert_transcoding_job(db_conn, vid3, status="pending")
        stats = TranscodingJobRepository(db_conn).get_stats()
        counts = stats["status_counts"]
        assert counts["completed"] == 1
        assert counts["failed"] == 1
        assert counts["pending"] == 1

    def test_avg_encoding_speed(self, db_conn: sqlite3.Connection) -> None:
        # 120초 영상을 60초에 처리 → 2x 속도
        vid = _insert_video(db_conn, path="/a.mp4", duration=120.0)
        now = datetime.now()
        start = now.isoformat()
        end = (now + timedelta(seconds=60)).isoformat()
        _insert_transcoding_job(
            db_conn, vid, status="completed", started_at=start, completed_at=end
        )
        stats = TranscodingJobRepository(db_conn).get_stats()
        speed = stats["avg_encoding_speed"]
        assert speed is not None
        assert abs(speed - 2.0) < 0.1

    def test_encoding_speed_none_when_no_times(self, db_conn: sqlite3.Connection) -> None:
        vid = _insert_video(db_conn, path="/a.mp4")
        _insert_transcoding_job(db_conn, vid, status="completed")
        stats = TranscodingJobRepository(db_conn).get_stats()
        assert stats["avg_encoding_speed"] is None

    def test_period_filter(self, db_conn: sqlite3.Connection) -> None:
        vid = _insert_video(db_conn, path="/a.mp4")
        # 수동으로 created_at 설정
        db_conn.execute(
            "INSERT INTO transcoding_jobs (video_id, status, created_at) VALUES (?, ?, ?)",
            (vid, "completed", "2026-01-15T10:00:00"),
        )
        db_conn.execute(
            "INSERT INTO transcoding_jobs (video_id, status, created_at) VALUES (?, ?, ?)",
            (vid, "failed", "2026-02-20T10:00:00"),
        )
        db_conn.commit()
        stats = TranscodingJobRepository(db_conn).get_stats(period="2026-01")
        counts = stats["status_counts"]
        assert counts.get("completed", 0) == 1
        assert "failed" not in counts


class TestMergeJobRepositoryGetStats:
    """MergeJobRepository.get_stats() 테스트."""

    def test_empty_db(self, db_conn: sqlite3.Connection) -> None:
        stats = MergeJobRepository(db_conn).get_stats()
        assert stats["total"] == 0
        assert stats["completed"] == 0
        assert stats["failed"] == 0
        assert stats["uploaded"] == 0
        assert stats["total_size_bytes"] == 0
        assert stats["total_duration"] == 0.0

    def test_counts(self, db_conn: sqlite3.Connection) -> None:
        _insert_merge_job(db_conn, output_path="/a.mp4", status="completed", youtube_id="yt123")
        _insert_merge_job(db_conn, output_path="/b.mp4", status="completed")
        _insert_merge_job(db_conn, output_path="/c.mp4", status="failed")
        stats = MergeJobRepository(db_conn).get_stats()
        assert stats["total"] == 3
        assert stats["completed"] == 2
        assert stats["failed"] == 1
        assert stats["uploaded"] == 1

    def test_size_and_duration(self, db_conn: sqlite3.Connection) -> None:
        _insert_merge_job(
            db_conn,
            output_path="/a.mp4",
            total_size_bytes=1_000_000,
            total_duration=600.0,
        )
        _insert_merge_job(
            db_conn,
            output_path="/b.mp4",
            total_size_bytes=2_000_000,
            total_duration=1200.0,
        )
        stats = MergeJobRepository(db_conn).get_stats()
        assert stats["total_size_bytes"] == 3_000_000
        assert stats["total_duration"] == 1800.0

    def test_null_size_treated_as_zero(self, db_conn: sqlite3.Connection) -> None:
        _insert_merge_job(db_conn, output_path="/a.mp4", total_size_bytes=None)
        stats = MergeJobRepository(db_conn).get_stats()
        assert stats["total_size_bytes"] == 0

    def test_period_filter(self, db_conn: sqlite3.Connection) -> None:
        _insert_merge_job(
            db_conn, output_path="/jan.mp4", status="completed", created_at="2026-01-15T10:00:00"
        )
        _insert_merge_job(
            db_conn, output_path="/feb.mp4", status="completed", created_at="2026-02-20T10:00:00"
        )
        stats = MergeJobRepository(db_conn).get_stats(period="2026-01")
        assert stats["total"] == 1


class TestArchiveHistoryRepositoryGetStats:
    """ArchiveHistoryRepository.get_stats() 테스트."""

    def test_empty_db(self, db_conn: sqlite3.Connection) -> None:
        stats = ArchiveHistoryRepository(db_conn).get_stats()
        assert stats["moved"] == 0
        assert stats["deleted"] == 0

    def test_counts(self, db_conn: sqlite3.Connection) -> None:
        vid = _insert_video(db_conn, path="/a.mp4")
        _insert_archive_history(db_conn, vid, operation="move")
        _insert_archive_history(
            db_conn, vid, operation="delete", original_path="/b.mp4", destination_path=None
        )
        _insert_archive_history(
            db_conn, vid, operation="delete", original_path="/c.mp4", destination_path=None
        )
        stats = ArchiveHistoryRepository(db_conn).get_stats()
        assert stats["moved"] == 1
        assert stats["deleted"] == 2

    def test_period_filter(self, db_conn: sqlite3.Connection) -> None:
        vid = _insert_video(db_conn, path="/a.mp4")
        _insert_archive_history(db_conn, vid, operation="move", archived_at="2026-01-15T10:00:00")
        _insert_archive_history(db_conn, vid, operation="move", archived_at="2026-02-20T10:00:00")
        stats = ArchiveHistoryRepository(db_conn).get_stats(period="2026-01")
        assert stats["moved"] == 1


# ---------------------------------------------------------------------------
# fetch_stats 통합 테스트
# ---------------------------------------------------------------------------


class TestFetchStats:
    """fetch_stats DB 집계 통합 테스트."""

    def test_empty_db(self, db_conn: sqlite3.Connection) -> None:
        data = fetch_stats(db_conn)
        assert data.total_videos == 0
        assert data.total_duration == 0.0
        assert data.devices == []
        assert data.transcoding.total == 0
        assert data.merge.total == 0
        assert data.archive.moved == 0

    def test_full_data_integration(self, db_conn: sqlite3.Connection) -> None:
        # 영상 삽입
        vid1 = _insert_video(db_conn, path="/a.mp4", duration=60.0, device="GoPro")
        vid2 = _insert_video(db_conn, path="/b.mp4", duration=120.0, device="Nikon")
        vid3 = _insert_video(db_conn, path="/c.mp4", duration=90.0, device="GoPro")

        # 트랜스코딩 작업
        _insert_transcoding_job(db_conn, vid1, status="completed")
        _insert_transcoding_job(db_conn, vid2, status="completed")
        _insert_transcoding_job(db_conn, vid3, status="failed")

        # 병합 작업
        _insert_merge_job(
            db_conn,
            output_path="/out/a.mp4",
            status="completed",
            youtube_id="yt123",
            total_size_bytes=500_000_000,
            total_duration=180.0,
        )

        # 아카이브
        _insert_archive_history(db_conn, vid1, operation="move")

        data = fetch_stats(db_conn)
        assert data.total_videos == 3
        assert data.total_duration == 270.0
        assert len(data.devices) == 2
        assert data.transcoding.completed == 2
        assert data.transcoding.failed == 1
        assert data.transcoding.total == 3
        assert data.merge.total == 1
        assert data.merge.uploaded == 1
        assert data.merge.total_size_bytes == 500_000_000
        assert data.archive.moved == 1

    def test_period_filter_propagates(self, db_conn: sqlite3.Connection) -> None:
        # 1월 데이터
        _insert_video(db_conn, path="/jan.mp4", creation_time="2026-01-15T10:00:00", device="A")
        # 2월 데이터
        _insert_video(db_conn, path="/feb.mp4", creation_time="2026-02-20T10:00:00", device="B")

        data = fetch_stats(db_conn, period="2026-01")
        assert data.period == "2026-01"
        assert data.total_videos == 1

    def test_merged_status_counted_as_completed(self, db_conn: sqlite3.Connection) -> None:
        vid = _insert_video(db_conn, path="/a.mp4")
        _insert_transcoding_job(db_conn, vid, status="merged")
        data = fetch_stats(db_conn)
        assert data.transcoding.completed == 1  # merged → completed로 합산


# ---------------------------------------------------------------------------
# cmd_stats 통합 테스트
# ---------------------------------------------------------------------------


class TestCmdStats:
    """cmd_stats 커맨드 통합 테스트."""

    def test_empty_db(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        cmd_stats(db_conn)
        captured = capsys.readouterr()
        assert "통계 대시보드" in captured.out
        assert "0개" in captured.out

    def test_with_data(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        vid = _insert_video(db_conn, path="/a.mp4", duration=60.0, device="GoPro")
        _insert_transcoding_job(db_conn, vid, status="completed")
        _insert_merge_job(
            db_conn,
            output_path="/out.mp4",
            total_size_bytes=100_000_000,
            total_duration=60.0,
        )
        cmd_stats(db_conn)
        captured = capsys.readouterr()
        assert "1개" in captured.out
        assert "GoPro" in captured.out

    def test_with_period(
        self,
        db_conn: sqlite3.Connection,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        _insert_video(db_conn, path="/jan.mp4", creation_time="2026-01-15T10:00:00")
        _insert_video(db_conn, path="/feb.mp4", creation_time="2026-02-20T10:00:00")
        cmd_stats(db_conn, period="2026-01")
        captured = capsys.readouterr()
        assert "기간: 2026-01" in captured.out
        assert "1개" in captured.out


# ---------------------------------------------------------------------------
# 엣지 케이스 테스트
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_large_dataset(self, db_conn: sqlite3.Connection) -> None:
        """대량 데이터에서도 정상 동작 확인."""
        for i in range(100):
            _insert_video(
                db_conn,
                path=f"/video_{i}.mp4",
                duration=float(i * 10),
                device=f"Device{i % 5}",
            )
        data = fetch_stats(db_conn)
        assert data.total_videos == 100
        assert len(data.devices) == 5

    def test_unicode_device_name(self, db_conn: sqlite3.Connection) -> None:
        """유니코드 기기명 처리."""
        _insert_video(db_conn, path="/a.mp4", device="니콘 Z8")
        data = fetch_stats(db_conn)
        assert data.devices[0].device == "니콘 Z8"

    def test_very_long_device_name(self, db_conn: sqlite3.Connection) -> None:
        """매우 긴 기기명에서도 차트 렌더링."""
        long_name = "A" * 100
        _insert_video(db_conn, path="/a.mp4", device=long_name)
        data = fetch_stats(db_conn)
        lines = render_bar_chart(data.devices)
        assert len(lines) == 1
        assert long_name in lines[0]

    def test_zero_duration_video(self, db_conn: sqlite3.Connection) -> None:
        """duration이 0인 영상."""
        _insert_video(db_conn, path="/a.mp4", duration=0.0)
        data = fetch_stats(db_conn)
        assert data.total_duration == 0.0

    def test_negative_duration_handled(self, db_conn: sqlite3.Connection) -> None:
        """음수 duration (비정상 데이터) 처리."""
        _insert_video(db_conn, path="/a.mp4", duration=-10.0)
        # 에러 없이 집계되어야 함
        data = fetch_stats(db_conn)
        assert data.total_duration == -10.0  # DB 값 그대로

    def test_period_filter_with_full_date(self, db_conn: sqlite3.Connection) -> None:
        """전체 날짜로 필터링."""
        _insert_video(db_conn, path="/a.mp4", creation_time="2026-01-15T10:30:00")
        _insert_video(db_conn, path="/b.mp4", creation_time="2026-01-15T14:00:00")
        _insert_video(db_conn, path="/c.mp4", creation_time="2026-01-16T09:00:00")
        data = fetch_stats(db_conn, period="2026-01-15")
        assert data.total_videos == 2

    def test_render_stats_with_no_encoding_speed(self) -> None:
        """인코딩 속도 없을 때 렌더링."""
        data = StatsData(
            period=None,
            total_videos=1,
            total_duration=60.0,
            devices=[DeviceStat("A", 1)],
            transcoding=TranscodingStats(1, 0, 0, 0, 1, None),
            merge=MergeStats(0, 0, 0, 0, 0, 0.0),
            archive=ArchiveStats(0, 0),
        )
        output = render_stats(data)
        assert "인코딩 속도" not in output

    def test_single_device_bar_chart(self) -> None:
        """기기 1개일 때 차트."""
        items = [DeviceStat("OnlyDevice", 42)]
        lines = render_bar_chart(items)
        assert len(lines) == 1
        assert "100%" in lines[0]

    def test_many_devices_bar_chart(self) -> None:
        """기기 10개일 때 차트 정렬."""
        items = [DeviceStat(f"Dev{i}", (10 - i) * 5) for i in range(10)]
        lines = render_bar_chart(items)
        assert len(lines) == 10

    def test_merge_stats_with_all_failed(self, db_conn: sqlite3.Connection) -> None:
        """모든 병합이 실패한 경우."""
        _insert_merge_job(db_conn, output_path="/a.mp4", status="failed")
        _insert_merge_job(db_conn, output_path="/b.mp4", status="failed")
        data = fetch_stats(db_conn)
        assert data.merge.total == 2
        assert data.merge.completed == 0
        assert data.merge.failed == 2

    def test_stats_with_only_archive_data(self, db_conn: sqlite3.Connection) -> None:
        """아카이브 데이터만 있는 경우."""
        vid = _insert_video(db_conn, path="/a.mp4")
        _insert_archive_history(db_conn, vid, operation="delete", destination_path=None)
        data = fetch_stats(db_conn)
        assert data.total_videos == 1
        assert data.archive.deleted == 1
        assert data.archive.moved == 0

    def test_encoding_speed_with_multiple_jobs(self, db_conn: sqlite3.Connection) -> None:
        """여러 트랜스코딩 작업의 평균 속도."""
        # 영상1: 120s / 60s = 2x
        vid1 = _insert_video(db_conn, path="/a.mp4", duration=120.0)
        now = datetime.now()
        _insert_transcoding_job(
            db_conn,
            vid1,
            status="completed",
            started_at=now.isoformat(),
            completed_at=(now + timedelta(seconds=60)).isoformat(),
        )
        # 영상2: 60s / 60s = 1x
        vid2 = _insert_video(db_conn, path="/b.mp4", duration=60.0)
        _insert_transcoding_job(
            db_conn,
            vid2,
            status="completed",
            started_at=now.isoformat(),
            completed_at=(now + timedelta(seconds=60)).isoformat(),
        )
        data = fetch_stats(db_conn)
        # 평균: (2.0 + 1.0) / 2 = 1.5
        assert data.transcoding.avg_encoding_speed is not None
        assert abs(data.transcoding.avg_encoding_speed - 1.5) < 0.1

    def test_failed_job_excluded_from_encoding_speed(self, db_conn: sqlite3.Connection) -> None:
        """실패한 작업은 인코딩 속도 평균에서 제외된다.

        mark_failed()가 completed_at을 설정하므로, status 필터 없이는
        실패 작업의 처리 시간이 평균 속도를 왜곡할 수 있다.
        """
        now = datetime.now()
        # 성공 작업: 120s / 60s = 2x
        vid1 = _insert_video(db_conn, path="/ok.mp4", duration=120.0)
        _insert_transcoding_job(
            db_conn,
            vid1,
            status="completed",
            started_at=now.isoformat(),
            completed_at=(now + timedelta(seconds=60)).isoformat(),
        )
        # 실패 작업: 120s / 600s = 0.2x (오래 걸린 후 실패)
        vid2 = _insert_video(db_conn, path="/fail.mp4", duration=120.0)
        _insert_transcoding_job(
            db_conn,
            vid2,
            status="failed",
            started_at=now.isoformat(),
            completed_at=(now + timedelta(seconds=600)).isoformat(),
        )
        stats = TranscodingJobRepository(db_conn).get_stats()
        speed = stats["avg_encoding_speed"]
        assert speed is not None
        # 실패 작업 제외 → 성공 작업만 평균: 2.0x
        assert abs(speed - 2.0) < 0.1
