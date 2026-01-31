"""Summary generator 테스트."""

from datetime import datetime
from pathlib import Path

from tubearchive.core.grouper import FileSequenceGroup
from tubearchive.models.video import VideoFile
from tubearchive.utils.summary_generator import (
    OutputInfo,
    extract_topic_from_path,
    format_timestamp,
    generate_chapters,
    generate_single_file_description,
    generate_summary_markdown,
)


class TestFormatTimestamp:
    """format_timestamp 테스트."""

    def test_formats_seconds_only(self) -> None:
        """초만 있는 경우."""
        assert format_timestamp(45.0) == "0:45"

    def test_formats_minutes_and_seconds(self) -> None:
        """분:초 형식."""
        assert format_timestamp(125.0) == "2:05"

    def test_formats_hours(self) -> None:
        """시:분:초 형식."""
        assert format_timestamp(3665.0) == "1:01:05"

    def test_handles_zero(self) -> None:
        """0초 처리."""
        assert format_timestamp(0.0) == "0:00"

    def test_rounds_decimal_seconds(self) -> None:
        """소수점 초 반올림."""
        assert format_timestamp(65.7) == "1:06"


class TestExtractTopicFromPath:
    """extract_topic_from_path 테스트."""

    def test_extracts_date_and_topic(self) -> None:
        """YYYY-MM-DD 주제 형식 추출."""
        path = Path("/Users/Videos/2024-01-15 도쿄 여행/clip1.mp4")
        date, topic = extract_topic_from_path(path)
        assert date == "2024-01-15"
        assert topic == "도쿄 여행"

    def test_handles_underscore_format(self) -> None:
        """YYYY_MM_DD 형식 처리."""
        path = Path("/Users/Videos/2024_01_15 서울 나들이/clip.mp4")
        date, topic = extract_topic_from_path(path)
        assert date == "2024-01-15"
        assert topic == "서울 나들이"

    def test_handles_no_date(self) -> None:
        """날짜 없는 경우."""
        path = Path("/Users/Videos/일상 브이로그/clip.mp4")
        date, topic = extract_topic_from_path(path)
        assert date is None
        assert topic == "일상 브이로그"

    def test_handles_plain_directory(self) -> None:
        """단순 디렉토리명."""
        path = Path("/Users/Videos/clips/video.mp4")
        date, topic = extract_topic_from_path(path)
        assert date is None
        assert topic == "clips"

    def test_handles_root_path(self) -> None:
        """현재 디렉토리 처리."""
        path = Path("video.mp4")
        _date, topic = extract_topic_from_path(path)
        assert topic is not None  # Should return something


class TestGenerateChapters:
    """generate_chapters 테스트."""

    def test_generates_chapter_list(self) -> None:
        """챕터 목록 생성."""
        clips = [
            ("오프닝.mp4", 30.0),
            ("메인_컨텐츠.mp4", 120.0),
            ("엔딩.mp4", 45.0),
        ]
        chapters = generate_chapters(clips)

        assert len(chapters) == 3
        assert chapters[0] == ("0:00", "오프닝")
        assert chapters[1] == ("0:30", "메인_컨텐츠")
        assert chapters[2] == ("2:30", "엔딩")

    def test_handles_single_clip(self) -> None:
        """단일 클립."""
        clips = [("영상.mp4", 60.0)]
        chapters = generate_chapters(clips)

        assert len(chapters) == 1
        assert chapters[0] == ("0:00", "영상")

    def test_removes_file_extension(self) -> None:
        """파일 확장자 제거."""
        clips = [("test.MOV", 30.0), ("clip.mp4", 30.0)]
        chapters = generate_chapters(clips)

        assert chapters[0][1] == "test"
        assert chapters[1][1] == "clip"

    def test_merges_grouped_clips(self, tmp_path: Path) -> None:
        """그룹된 파일은 하나의 챕터로 병합."""
        base = datetime(2025, 1, 1, 10, 0, 0)
        p1 = tmp_path / "GH010128.MP4"
        p2 = tmp_path / "GH020128.MP4"
        p1.write_bytes(b"0")
        p2.write_bytes(b"0")
        vf1 = VideoFile(path=p1, creation_time=base, size_bytes=1)
        vf2 = VideoFile(path=p2, creation_time=base, size_bytes=1)
        groups = [FileSequenceGroup(files=(vf1, vf2), group_id="gopro_0128")]

        clips = [("GH010128.MP4", 60.0), ("GH020128.MP4", 60.0)]
        chapters = generate_chapters(clips, groups=groups)

        assert chapters == [("0:00", "GH010128")]


class TestOutputInfo:
    """OutputInfo 모델 테스트."""

    def test_creates_output_info(self) -> None:
        """OutputInfo 생성."""
        info = OutputInfo(
            output_path=Path("/output/merged.mp4"),
            title="도쿄 여행",
            date="2024-01-15",
            total_duration=195.5,
            total_size=1024 * 1024 * 100,
            clips=[
                ("clip1.mp4", 60.0),
                ("clip2.mp4", 135.5),
            ],
        )

        assert info.title == "도쿄 여행"
        assert info.total_duration == 195.5
        assert len(info.clips) == 2

    def test_formatted_duration(self) -> None:
        """포맷된 총 시간."""
        info = OutputInfo(
            output_path=Path("/output/merged.mp4"),
            title="테스트",
            date=None,
            total_duration=3665.0,
            total_size=0,
            clips=[],
        )
        assert info.formatted_duration == "1:01:05"

    def test_formatted_size(self) -> None:
        """포맷된 파일 크기."""
        info = OutputInfo(
            output_path=Path("/output/merged.mp4"),
            title="테스트",
            date=None,
            total_duration=0,
            total_size=1536 * 1024 * 1024,  # 1.5GB
            clips=[],
        )
        assert "1.5" in info.formatted_size
        assert "GB" in info.formatted_size


class TestGenerateSummaryMarkdown:
    """generate_summary_markdown 테스트."""

    def test_generates_markdown(self) -> None:
        """마크다운 생성."""
        info = OutputInfo(
            output_path=Path("/output/도쿄_여행.mp4"),
            title="도쿄 여행",
            date="2024-01-15",
            total_duration=195.0,
            total_size=500 * 1024 * 1024,
            clips=[
                ("오프닝.mp4", 30.0),
                ("시부야.mp4", 90.0),
                ("엔딩.mp4", 75.0),
            ],
        )

        markdown = generate_summary_markdown(info)

        # 제목 확인
        assert "# 도쿄 여행" in markdown
        # 날짜 확인
        assert "2024-01-15" in markdown
        # 챕터 타임스탬프 확인
        assert "0:00" in markdown
        assert "0:30" in markdown
        assert "2:00" in markdown
        # 클립명 확인
        assert "오프닝" in markdown
        assert "시부야" in markdown
        # 총 시간 확인
        assert "3:15" in markdown or "3분" in markdown

    def test_generates_youtube_chapters(self) -> None:
        """YouTube 챕터 형식 생성."""
        info = OutputInfo(
            output_path=Path("/output/test.mp4"),
            title="테스트",
            date=None,
            total_duration=120.0,
            total_size=100 * 1024 * 1024,
            clips=[
                ("part1.mp4", 60.0),
                ("part2.mp4", 60.0),
            ],
        )

        markdown = generate_summary_markdown(info)

        # YouTube 챕터 섹션 확인
        assert "## YouTube 챕터" in markdown or "## 챕터" in markdown
        assert "0:00 part1" in markdown
        assert "1:00 part2" in markdown

    def test_includes_clip_list(self) -> None:
        """클립 목록 포함."""
        info = OutputInfo(
            output_path=Path("/output/test.mp4"),
            title="테스트",
            date=None,
            total_duration=90.0,
            total_size=50 * 1024 * 1024,
            clips=[
                ("클립1.mp4", 30.0),
                ("클립2.mp4", 60.0),
            ],
        )

        markdown = generate_summary_markdown(info)

        assert "클립1" in markdown
        assert "클립2" in markdown
        assert "0:30" in markdown  # clip1 duration
        assert "1:00" in markdown  # clip2 duration


class TestGenerateSingleFileDescription:
    """generate_single_file_description 테스트."""

    def test_generates_description_with_device_and_time(self) -> None:
        """기기 정보와 촬영 시간이 있는 경우."""
        clip_info = {
            "name": "video.mp4",
            "duration": 120.5,
            "start": 0.0,
            "end": 120.5,
            "device": "iPhone 17 Pro Max",
            "shot_time": "14:30:00",
        }
        result = generate_single_file_description(clip_info)

        assert "iPhone 17 Pro Max" in result
        assert "14:30:00" in result

    def test_generates_description_with_device_only(self) -> None:
        """기기 정보만 있는 경우."""
        clip_info = {
            "name": "video.mp4",
            "duration": 60.0,
            "start": 0.0,
            "end": 60.0,
            "device": "Nikon Z8",
            "shot_time": None,
        }
        result = generate_single_file_description(clip_info)

        assert "Nikon Z8" in result
        assert "Shot at" not in result

    def test_generates_description_with_time_only(self) -> None:
        """촬영 시간만 있는 경우."""
        clip_info = {
            "name": "video.mp4",
            "duration": 60.0,
            "start": 0.0,
            "end": 60.0,
            "device": None,
            "shot_time": "09:15:30",
        }
        result = generate_single_file_description(clip_info)

        assert "09:15:30" in result
        assert "Filmed with" not in result

    def test_returns_empty_for_no_metadata(self) -> None:
        """메타데이터가 없는 경우 빈 문자열 반환."""
        clip_info = {
            "name": "video.mp4",
            "duration": 60.0,
            "start": 0.0,
            "end": 60.0,
            "device": None,
            "shot_time": None,
        }
        result = generate_single_file_description(clip_info)

        assert result == ""

    def test_skips_unknown_device(self) -> None:
        """Unknown 기기는 표시하지 않음."""
        clip_info = {
            "name": "video.mp4",
            "duration": 60.0,
            "start": 0.0,
            "end": 60.0,
            "device": "Unknown",
            "shot_time": "10:00:00",
        }
        result = generate_single_file_description(clip_info)

        assert "Unknown" not in result
        assert "Filmed with" not in result
        assert "10:00:00" in result

    def test_skips_empty_shot_time(self) -> None:
        """빈 문자열 촬영 시간은 표시하지 않음."""
        clip_info = {
            "name": "video.mp4",
            "duration": 60.0,
            "start": 0.0,
            "end": 60.0,
            "device": "GoPro Hero 13",
            "shot_time": "",
        }
        result = generate_single_file_description(clip_info)

        assert "GoPro Hero 13" in result
        assert "Shot at" not in result
