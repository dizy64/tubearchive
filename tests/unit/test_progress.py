"""진행률 표시 테스트."""

from tubearchive.utils.progress import (
    MultiProgressBar,
    ProgressBar,
    ProgressInfo,
    format_size,
    format_time,
)


class TestFormatTime:
    """시간 포맷 테스트."""

    def test_format_seconds(self) -> None:
        """초 단위."""
        assert format_time(45) == "0:45"

    def test_format_minutes(self) -> None:
        """분 단위."""
        assert format_time(125) == "2:05"

    def test_format_hours(self) -> None:
        """시간 단위."""
        assert format_time(3725) == "1:02:05"

    def test_format_zero(self) -> None:
        """0초."""
        assert format_time(0) == "0:00"


class TestFormatSize:
    """크기 포맷 테스트."""

    def test_format_bytes(self) -> None:
        """바이트 단위."""
        assert format_size(500) == "500 B"

    def test_format_kilobytes(self) -> None:
        """킬로바이트 단위."""
        assert format_size(1536) == "1.5 KB"

    def test_format_megabytes(self) -> None:
        """메가바이트 단위."""
        assert format_size(1572864) == "1.5 MB"

    def test_format_gigabytes(self) -> None:
        """기가바이트 단위."""
        assert format_size(1610612736) == "1.5 GB"


class TestProgressBar:
    """ProgressBar 테스트."""

    def test_creates_progress_bar(self) -> None:
        """프로그레스 바 생성."""
        pb = ProgressBar(total=100, desc="Processing")

        assert pb.total == 100
        assert pb.current == 0
        assert pb.desc == "Processing"

    def test_update_progress(self) -> None:
        """진행률 업데이트."""
        pb = ProgressBar(total=100)
        pb.update(50)

        assert pb.current == 50

    def test_update_increments(self) -> None:
        """증분 업데이트."""
        pb = ProgressBar(total=100)
        pb.update(30)
        pb.update(20)

        assert pb.current == 50

    def test_render_progress_bar(self) -> None:
        """프로그레스 바 렌더링."""
        pb = ProgressBar(total=100, width=20)
        pb.update(50)

        rendered = pb.render()

        assert "50%" in rendered
        assert "█" in rendered or "=" in rendered

    def test_render_with_description(self) -> None:
        """설명 포함 렌더링."""
        pb = ProgressBar(total=100, desc="Test")
        pb.update(50)

        rendered = pb.render()

        assert "Test" in rendered

    def test_complete_shows_100_percent(self) -> None:
        """완료 시 100%."""
        pb = ProgressBar(total=100)
        pb.update(100)

        rendered = pb.render()

        assert "100%" in rendered

    def test_set_absolute_position(self) -> None:
        """절대 위치 설정."""
        pb = ProgressBar(total=100)
        pb.set(75)

        assert pb.current == 75

    def test_finish_sets_complete(self) -> None:
        """완료 처리."""
        pb = ProgressBar(total=100)
        pb.update(50)
        pb.finish()

        assert pb.current == pb.total


class TestProgressInfo:
    """ProgressInfo 테스트."""

    def test_create_progress_info(self) -> None:
        """ProgressInfo 생성."""
        info = ProgressInfo(
            percent=50,
            current_time=120.5,
            total_duration=241.0,
            fps=30.0,
        )

        assert info.percent == 50
        assert info.current_time == 120.5
        assert info.total_duration == 241.0
        assert info.fps == 30.0

    def test_eta_calculation(self) -> None:
        """ETA 계산."""
        info = ProgressInfo(
            percent=50,
            current_time=120.0,
            total_duration=240.0,
            fps=30.0,
        )

        # 50% 완료, 120초 소요 → 남은 120초
        eta = info.calculate_eta()
        assert eta is not None
        assert 115 <= eta <= 125  # 오차 허용

    def test_eta_zero_percent(self) -> None:
        """0% 일 때 ETA는 None."""
        info = ProgressInfo(
            percent=0,
            current_time=0.0,
            total_duration=240.0,
            fps=0.0,
        )

        assert info.calculate_eta() is None

    def test_eta_complete(self) -> None:
        """100% 일 때 ETA는 0."""
        info = ProgressInfo(
            percent=100,
            current_time=240.0,
            total_duration=240.0,
            fps=30.0,
        )

        eta = info.calculate_eta()
        assert eta == 0


class TestMultiProgressBarWithInfo:
    """MultiProgressBar 상세 정보 테스트."""

    def test_render_with_progress_info(self) -> None:
        """ProgressInfo로 렌더링."""
        mpb = MultiProgressBar(total_files=3)
        mpb.start_file("test_video.mov")

        info = ProgressInfo(
            percent=50,
            current_time=60.0,
            total_duration=120.0,
            fps=29.97,
        )
        mpb.update_with_info(info)

        rendered = mpb.render()

        # 필수 요소 확인
        assert "[1/3]" in rendered
        assert "50%" in rendered
        assert "1:00" in rendered  # current_time
        assert "2:00" in rendered  # total_duration
        assert "fps" in rendered.lower() or "29" in rendered

    def test_render_with_eta(self) -> None:
        """ETA 표시."""
        mpb = MultiProgressBar(total_files=1)
        mpb.start_file("video.mp4")

        info = ProgressInfo(
            percent=50,
            current_time=60.0,
            total_duration=120.0,
            fps=30.0,
        )
        mpb.update_with_info(info)

        rendered = mpb.render()

        # ETA 또는 남은 시간 표시 확인
        assert "ETA" in rendered or "남음" in rendered

    def test_backward_compatible_update(self) -> None:
        """기존 update_file_progress 호환성."""
        mpb = MultiProgressBar(total_files=2)
        mpb.start_file("old_style.mov")
        mpb.update_file_progress(75)

        rendered = mpb.render()

        assert "75%" in rendered
        assert "[1/2]" in rendered
