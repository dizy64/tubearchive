"""클립 메타데이터 모델."""

from typing import NamedTuple


class ClipInfo(NamedTuple):
    """영상 클립 메타데이터 (Summary·타임라인용).

    Attributes:
        name: 파일명 (예: ``GH010042.MP4``)
        duration: 재생시간 (초)
        device: 촬영 기기명 (예: ``Nikon Z6III``, ``GoPro HERO12``)
        shot_time: 촬영 시각 문자열 (``HH:MM:SS``, None이면 알 수 없음)
    """

    name: str
    duration: float
    device: str | None
    shot_time: str | None
