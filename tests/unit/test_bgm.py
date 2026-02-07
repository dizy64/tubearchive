"""BGM 믹싱 기능 단위 테스트."""

from tubearchive.config import _parse_bgm
from tubearchive.ffmpeg.effects import create_bgm_filter


class TestBGMConfig:
    """BGMConfig 설정 파싱 테스트."""

    def test_parse_bgm_all_fields(self) -> None:
        """모든 필드가 올바르게 파싱되는지 확인."""
        data = {
            "bgm_path": "~/Music/bgm.mp3",
            "bgm_volume": 0.3,
            "bgm_loop": True,
        }
        config = _parse_bgm(data)
        assert config.bgm_path == "~/Music/bgm.mp3"
        assert config.bgm_volume == 0.3
        assert config.bgm_loop is True

    def test_parse_bgm_empty(self) -> None:
        """빈 데이터에서 기본값 반환."""
        config = _parse_bgm({})
        assert config.bgm_path is None
        assert config.bgm_volume is None
        assert config.bgm_loop is None

    def test_parse_bgm_volume_out_of_range(self) -> None:
        """볼륨 범위 초과 시 무시."""
        data = {"bgm_volume": 1.5}
        config = _parse_bgm(data)
        assert config.bgm_volume is None

    def test_parse_bgm_volume_negative(self) -> None:
        """음수 볼륨 무시."""
        data = {"bgm_volume": -0.1}
        config = _parse_bgm(data)
        assert config.bgm_volume is None

    def test_parse_bgm_invalid_types(self) -> None:
        """잘못된 타입 무시."""
        data = {
            "bgm_path": 123,  # 숫자
            "bgm_volume": "high",  # 문자열
            "bgm_loop": "yes",  # 문자열
        }
        config = _parse_bgm(data)
        assert config.bgm_path is None
        assert config.bgm_volume is None
        assert config.bgm_loop is None


class TestBGMFilter:
    """BGM 필터 생성 테스트."""

    def test_bgm_filter_shorter_than_video(self) -> None:
        """BGM이 영상보다 짧을 때 루프 재생."""
        filter_str = create_bgm_filter(
            bgm_duration=30.0,
            video_duration=90.0,
            bgm_volume=0.2,
            bgm_loop=True,
        )
        assert "aloop" in filter_str
        assert "atrim=end=90.0" in filter_str
        assert "volume=0.2" in filter_str
        assert "amix" in filter_str

    def test_bgm_filter_shorter_no_loop(self) -> None:
        """BGM이 영상보다 짧지만 루프 비활성화."""
        filter_str = create_bgm_filter(
            bgm_duration=30.0,
            video_duration=90.0,
            bgm_volume=0.2,
            bgm_loop=False,
        )
        assert "aloop" not in filter_str
        assert "volume=0.2" in filter_str
        assert "amix" in filter_str

    def test_bgm_filter_longer_than_video(self) -> None:
        """BGM이 영상보다 길 때 페이드 아웃."""
        filter_str = create_bgm_filter(
            bgm_duration=120.0,
            video_duration=90.0,
            bgm_volume=0.3,
            bgm_loop=False,
        )
        assert "atrim=end=90.0" in filter_str
        assert "afade=t=out:st=87.0:d=3.0" in filter_str
        assert "volume=0.3" in filter_str
        assert "amix" in filter_str

    def test_bgm_filter_same_length(self) -> None:
        """BGM과 영상 길이가 같을 때."""
        filter_str = create_bgm_filter(
            bgm_duration=60.0,
            video_duration=60.0,
            bgm_volume=0.25,
            bgm_loop=False,
        )
        assert "atrim" not in filter_str
        assert "afade" not in filter_str
        assert "volume=0.25" in filter_str
        assert "amix" in filter_str

    def test_bgm_filter_volume_range(self) -> None:
        """다양한 볼륨 값 테스트."""
        # 최소 볼륨
        filter_min = create_bgm_filter(
            bgm_duration=60.0,
            video_duration=60.0,
            bgm_volume=0.0,
            bgm_loop=False,
        )
        assert "volume=0.0" in filter_min

        # 최대 볼륨
        filter_max = create_bgm_filter(
            bgm_duration=60.0,
            video_duration=60.0,
            bgm_volume=1.0,
            bgm_loop=False,
        )
        assert "volume=1.0" in filter_max
