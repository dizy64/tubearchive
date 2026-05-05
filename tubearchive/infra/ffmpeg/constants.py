"""FFmpeg 필터 관련 순수 상수.

외부 의존성이 없으므로 config.py / effects.py 양쪽에서 자유롭게 import 가능하다.
"""

from __future__ import annotations

# 화이트밸런스 Kelvin 프리셋
WB_PRESETS: dict[str, int] = {
    "tungsten": 3200,
    "fluorescent": 4000,
    "daylight": 5500,
    "cloudy": 6500,
    "shade": 7500,
}

# 기기별 빌트인 기본 WB 프리셋 (GoPro/DJI는 야외 촬영 위주이므로 cloudy 기본)
WB_DEVICE_DEFAULTS: dict[str, str] = {
    "nikon": "daylight",
    "canon": "daylight",
    "sony": "daylight",
    "iphone": "daylight",
    "gopro": "cloudy",
    "dji": "cloudy",
}
