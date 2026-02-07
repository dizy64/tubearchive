"""BGM 믹싱 기능 단위 테스트."""

import pytest

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

    def test_parse_bgm_volume_boundary_zero(self) -> None:
        """볼륨 0.0은 유효한 값."""
        data = {"bgm_volume": 0.0}
        config = _parse_bgm(data)
        assert config.bgm_volume == 0.0

    def test_parse_bgm_volume_boundary_one(self) -> None:
        """볼륨 1.0은 유효한 값."""
        data = {"bgm_volume": 1.0}
        config = _parse_bgm(data)
        assert config.bgm_volume == 1.0

    def test_parse_bgm_partial_fields(self) -> None:
        """일부 필드만 지정된 경우."""
        data = {"bgm_path": "/music/bgm.mp3"}
        config = _parse_bgm(data)
        assert config.bgm_path == "/music/bgm.mp3"
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
        assert "aloop=loop=-1" in filter_str
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

    def test_bgm_filter_zero_duration_raises(self) -> None:
        """bgm_duration=0일 때 ValueError 발생."""
        with pytest.raises(ValueError, match="BGM duration must be > 0"):
            create_bgm_filter(
                bgm_duration=0.0,
                video_duration=60.0,
                bgm_volume=0.2,
                bgm_loop=False,
            )

    def test_bgm_filter_negative_duration_raises(self) -> None:
        """음수 bgm_duration일 때 ValueError 발생."""
        with pytest.raises(ValueError, match="BGM duration must be > 0"):
            create_bgm_filter(
                bgm_duration=-10.0,
                video_duration=60.0,
                bgm_volume=0.2,
                bgm_loop=False,
            )

    def test_bgm_filter_zero_video_duration_raises(self) -> None:
        """video_duration=0일 때 ValueError 발생."""
        with pytest.raises(ValueError, match="Video duration must be > 0"):
            create_bgm_filter(
                bgm_duration=30.0,
                video_duration=0.0,
                bgm_volume=0.2,
                bgm_loop=False,
            )

    def test_bgm_filter_no_audio_track(self) -> None:
        """오디오 트랙 없는 영상: BGM만 출력."""
        filter_str = create_bgm_filter(
            bgm_duration=60.0,
            video_duration=60.0,
            bgm_volume=0.3,
            has_audio=False,
        )
        assert "[1:a]" in filter_str
        assert "[a_out]" in filter_str
        assert "amix" not in filter_str
        assert "[0:a]" not in filter_str

    def test_bgm_filter_no_audio_with_loop(self) -> None:
        """오디오 없는 영상에서 루프 + BGM."""
        filter_str = create_bgm_filter(
            bgm_duration=30.0,
            video_duration=90.0,
            bgm_volume=0.2,
            bgm_loop=True,
            has_audio=False,
        )
        assert "aloop=loop=-1" in filter_str
        assert "atrim=end=90.0" in filter_str
        assert "amix" not in filter_str
        assert "[a_out]" in filter_str

    def test_bgm_filter_amix_weights(self) -> None:
        """amix weights 파라미터 확인."""
        filter_str = create_bgm_filter(
            bgm_duration=60.0,
            video_duration=60.0,
            bgm_volume=0.3,
            bgm_loop=False,
        )
        assert "weights=1 0.3" in filter_str

    def test_bgm_filter_short_video_fade(self) -> None:
        """짧은 영상(2초)에서 fade_out 적용."""
        filter_str = create_bgm_filter(
            bgm_duration=10.0,
            video_duration=2.0,
            bgm_volume=0.2,
            bgm_loop=False,
            fade_out_duration=3.0,
        )
        # 2초 영상이므로 fade는 전체 구간에 걸쳐야 함
        assert "atrim=end=2.0" in filter_str
        assert "afade=t=out:st=0.0:d=2.0" in filter_str

    def test_bgm_filter_exact_multiple_loop(self) -> None:
        """BGM이 영상의 정확한 배수일 때 무한 루프 사용."""
        filter_str = create_bgm_filter(
            bgm_duration=30.0,
            video_duration=90.0,
            bgm_volume=0.2,
            bgm_loop=True,
        )
        # loop=-1 (무한 루프)을 사용하므로 배수 계산 버그 없음
        assert "aloop=loop=-1" in filter_str
        assert "atrim=end=90.0" in filter_str
