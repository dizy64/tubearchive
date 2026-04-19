"""영상 메타데이터 감지기.

ffprobe를 서브프로세스로 실행하여 영상 파일의 기술 메타데이터를 추출한다.

추출 항목:
    - **해상도**: width x height (예: 3840x2160)
    - **코덱**: codec_name (hevc, h264 등)
    - **프레임레이트**: r_frame_rate → float 변환 (분수 ``30000/1001`` 지원)
    - **픽셀 포맷**: pix_fmt (yuv420p, yuv420p10le 등)
    - **색 공간**: color_transfer, color_primaries, color_space
    - **길이**: duration (초)
    - **비트레이트**: bit_rate (bps)

반환:
    :class:`~tubearchive.domain.models.video.VideoMetadata` 데이터클래스
"""

import json
import logging
import re
import subprocess
from collections.abc import Mapping
from fractions import Fraction
from pathlib import Path
from typing import Any

from tubearchive.domain.models.video import VideoMetadata

logger = logging.getLogger(__name__)

_ISO6709_RE = re.compile(
    r"(?P<lat>[+-]\d+(?:\.\d+)?)(?P<lon>[+-]\d+(?:\.\d+)?)(?:[+-]\d+(?:\.\d+)?)?/?"
)
_LATITUDE_RE = re.compile(r"\blat(?:itude)?\s*[:=]\s*(?P<lat>[+-]?\d+(?:\.\d+)?)", re.IGNORECASE)
_LONGITUDE_RE = re.compile(r"\blon(?:gitude)?\s*[:=]\s*(?P<lon>[+-]?\d+(?:\.\d+)?)", re.IGNORECASE)
_NSEW_COORD_RE = re.compile(
    r"\b(?P<lat_dir>[NS])\s*(?P<lat>\d+(?:\.\d+)?)\s*,?\s*(?P<lon_dir>[EW])\s*(?P<lon>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _coerce_tag_text(value: Any) -> str | None:
    """메타데이터 태그 값을 문자열로 정규화."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _is_valid_lat_lon(lat: float, lon: float) -> bool:
    """위도/경도 범위를 검증한다."""
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def _format_coordinate_pair(lat: float, lon: float) -> str:
    """좌표를 워터마크 표시용 문자열로 변환."""
    return f"{lat:.6f}, {lon:.6f}"


def _parse_iso6709(value: str) -> tuple[float, float] | None:
    """ISO6709 형식 문자열에서 위도/경도 추출."""
    match = _ISO6709_RE.search(value)
    if not match:
        return None

    try:
        lat = float(match.group("lat"))
        lon = float(match.group("lon"))
    except (TypeError, ValueError):
        return None

    if not _is_valid_lat_lon(lat, lon):
        logger.warning(
            "Invalid ISO6709 coordinate ignored: %s (parsed lat=%s, lon=%s)",
            value,
            lat,
            lon,
        )
        return None
    return lat, lon


def _parse_nsew(value: str) -> tuple[float, float] | None:
    """N/S/E/W 표기 좌표(`N 35.12, E 129.31`)를 파싱."""
    match = _NSEW_COORD_RE.search(value)
    if not match:
        return None

    try:
        lat = float(match.group("lat"))
        lon = float(match.group("lon"))
    except (TypeError, ValueError):
        return None

    lat = -abs(lat) if match.group("lat_dir").upper() == "S" else abs(lat)
    lon = -abs(lon) if match.group("lon_dir").upper() == "W" else abs(lon)

    if not _is_valid_lat_lon(lat, lon):
        return None
    return lat, lon


def _extract_location_from_tags(tags: dict[str, Any]) -> tuple[float, float] | None:
    """메타데이터 태그에서 ISO6709 또는 lat/lon 키-값을 추출."""
    # 1) ISO6709 우선 탐색
    for value in tags.values():
        value_str = _coerce_tag_text(value)
        if not value_str:
            continue

        if parsed := _parse_iso6709(value_str):
            return parsed

        if parsed := _parse_nsew(value_str):
            return parsed

    # 키가 lat/lon 형태인 필드에서 개별 추출
    lat = None
    lon = None
    for key, value in tags.items():
        value_str = _coerce_tag_text(value)
        if not value_str:
            continue

        key_lower = str(key).lower()
        if lat is None and key_lower.startswith("lat"):
            match = _LATITUDE_RE.search(value_str)
            if match:
                try:
                    lat = float(match.group("lat"))
                except ValueError:
                    lat = None
        elif lon is None and key_lower.startswith("lon"):
            match = _LONGITUDE_RE.search(value_str)
            if match:
                try:
                    lon = float(match.group("lon"))
                except ValueError:
                    lon = None

    # 3) 키와 무관하게 값에서 'lat:'/'lon:' 패턴 폴백
    if lat is None or lon is None:
        for value in tags.values():
            value_str = _coerce_tag_text(value)
            if not value_str:
                continue

            if lat is None:
                lat_match = _LATITUDE_RE.search(value_str)
                if lat_match:
                    try:
                        lat = float(lat_match.group("lat"))
                    except ValueError:
                        lat = None
            if lon is None:
                lon_match = _LONGITUDE_RE.search(value_str)
                if lon_match:
                    try:
                        lon = float(lon_match.group("lon"))
                    except ValueError:
                        lon = None

            if lat is not None and lon is not None:
                break

    if lat is None or lon is None:
        return None
    if not _is_valid_lat_lon(lat, lon):
        return None
    return lat, lon


def _extract_location_from_sidecar(video_path: Path) -> tuple[float, float] | None:
    """동일한 이름의 .srt sidecar에서 위도/경도 추출."""
    sidecar_path = video_path.with_suffix(".srt")
    if not sidecar_path.is_file():
        return None

    try:
        text = sidecar_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    return _parse_coordinates_from_text(text)


def _extract_freeform_location(tags: dict[str, Any]) -> str | None:
    """lat/lon 외 위치 텍스트 태그에서 문자열 위치를 추출."""
    for key, value in tags.items():
        key_lower = str(key).lower()
        if not any(token in key_lower for token in ("location", "gps", "address", "venue", "geo")):
            continue
        value_str = _coerce_tag_text(value)
        if value_str:
            return value_str
    return None


def _find_metadata_location(
    video_path: Path, probe_data: dict[str, Any]
) -> tuple[float, float] | str | None:
    """ffprobe 출력에서 위치 문자열/좌표 쌍을 안전하게 추출."""
    tags = probe_data.get("format", {}).get("tags", {})
    stream_tags: dict[str, Any] = {}
    for stream in probe_data.get("streams", []):
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            stream_tags = stream.get("tags", {})
            break

    if isinstance(tags, dict):
        coordinates = _extract_location_from_tags(tags)
        if coordinates:
            return coordinates

    if isinstance(stream_tags, dict):
        coordinates = _extract_location_from_tags(stream_tags)
        if coordinates:
            return coordinates

    if isinstance(tags, dict):
        location_text = _extract_freeform_location(tags)
        if location_text:
            return location_text

    if isinstance(stream_tags, dict):
        location_text = _extract_freeform_location(stream_tags)
        if location_text:
            return location_text

    sidecar_location = _extract_location_from_sidecar(video_path)
    if sidecar_location:
        return sidecar_location

    return None


def _coerce_str(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _parse_geo_float(value: str | None) -> float | None:
    if not value:
        return None

    try:
        return float(value)
    except ValueError:
        return None


def _parse_hemisphere_coordinate(text: str) -> tuple[float, float] | None:
    match = re.search(
        r"([NS])\s*([+-]?\d+(?:\.\d+)?).*?([EW])\s*([+-]?\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None

    ns, lat_txt, ew, lon_txt = match.groups()
    lat = _parse_geo_float(lat_txt)
    lon = _parse_geo_float(lon_txt)
    if lat is None or lon is None:
        return None

    lat = -abs(lat) if ns.upper() == "S" else abs(lat)
    lon = -abs(lon) if ew.upper() == "W" else abs(lon)

    return lat, lon


def _parse_iso6709_coordinates(text: str) -> tuple[float, float] | None:
    cleaned = text.strip().strip("/")
    match = re.match(
        r"^([+-]?\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)([NS]?)([EW]?)$",
        cleaned,
    )
    if not match:
        return None

    lat_txt, lon_txt, ns, ew = match.groups()
    lat = _parse_geo_float(lat_txt)
    lon = _parse_geo_float(lon_txt)
    if lat is None or lon is None:
        return None

    if ns and ns.upper() == "S":
        lat = -abs(lat)
    if ew and ew.upper() == "W":
        lon = -abs(lon)

    return lat, lon


def _parse_coordinates_from_text(text: str) -> tuple[float, float] | None:
    # 1) ISO6709 / NSEW 형식 문자열이 전체 텍스트에 존재하면 우선 사용
    if parsed := _parse_iso6709(text):
        return parsed

    if parsed := _parse_nsew(text):
        return parsed

    # 2) 줄 단위 검사로 타임스탬프(예: 00:00:00,000) 오검출 방지
    for line in text.splitlines():
        line = line.strip()
        if not line or line == "-->":
            continue
        if line.isdigit() or "-->" in line:
            continue

        if parsed := _parse_iso6709(line):
            return parsed

        if parsed := _parse_nsew(line):
            return parsed

        pair_match = re.search(
            r"([+-]?\d+(?:\.\d+)?)\s*[ ,;/|]\s*([+-]?\d+(?:\.\d+)?)",
            line,
        )
        if pair_match:
            lat = _parse_geo_float(pair_match.group(1))
            lon = _parse_geo_float(pair_match.group(2))
            if lat is not None and lon is not None:
                return lat, lon

    return None


def _extract_location_from_mapping(tags: Mapping[str, Any]) -> str | None:
    lat: float | None = None
    lon: float | None = None

    for key, raw_value in tags.items():
        value = _coerce_str(raw_value)
        if value is None:
            continue

        key_l = key.lower()

        if "lat" in key_l and lat is None:
            lat = _parse_geo_float(value)
            continue

        if any(t in key_l for t in ["lon", "lng"]) and lon is None:
            lon = _parse_geo_float(value)
            continue

        if "location" in key_l or "iso6709" in key_l:
            coords = _parse_coordinates_from_text(value)
            if coords:
                return f"{coords[0]:.6f}, {coords[1]:.6f}"

    if lat is None or lon is None:
        return None

    return f"{lat:.6f}, {lon:.6f}"


def _extract_location(probe_data: dict[str, Any]) -> str | None:
    format_tags = probe_data.get("format", {}).get("tags", {})
    if isinstance(format_tags, Mapping):
        location = _extract_location_from_mapping(format_tags)
        if location:
            return location

    for stream in probe_data.get("streams", []):
        tags = stream.get("tags", {})
        if isinstance(tags, Mapping):
            location = _extract_location_from_mapping(tags)
            if location:
                return location

    return None


def detect_metadata(video_path: Path) -> VideoMetadata:
    """ffprobe로 영상 메타데이터를 감지한다.

    비디오 스트림에서 해상도·코덱·픽셀 포맷·색 공간·프레임 레이트를,
    ``format`` 태그에서 기기명·재생 시간을 추출하여
    :class:`~tubearchive.domain.models.video.VideoMetadata` 로 반환한다.

    Args:
        video_path: 분석할 영상 파일 경로.

    Returns:
        추출된 메타데이터 (해상도, 코덱, 색 공간, 기기명 등).

    Raises:
        RuntimeError: ffprobe 실행 실패 또는 비디오 스트림 없음.
    """
    probe_data = _run_ffprobe(video_path)

    # 비디오 스트림 찾기
    video_stream = None
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    if not video_stream:
        raise RuntimeError(f"No video stream found in {video_path}")

    # 기본 정보 추출
    width = video_stream["width"]
    height = video_stream["height"]
    codec = video_stream["codec_name"]
    pixel_format = video_stream.get("pix_fmt", "unknown")

    # FPS 계산
    r_frame_rate = video_stream.get("r_frame_rate", "0/1")
    avg_frame_rate = video_stream.get("avg_frame_rate", "0/1")
    fps = _parse_frame_rate(r_frame_rate)

    # VFR 감지 (r_frame_rate != avg_frame_rate)
    is_vfr = _parse_frame_rate(r_frame_rate) != _parse_frame_rate(avg_frame_rate)

    # Duration
    duration_seconds = float(
        video_stream.get("duration") or probe_data.get("format", {}).get("duration", "0")
    )

    # 회전 메타데이터 확인
    rotation = int(video_stream.get("tags", {}).get("rotate", "0"))
    is_rotated_vertical = rotation in (90, 270)

    # 세로 영상 감지
    is_portrait = is_rotated_vertical or (width < height)

    # 기기 모델 감지
    device_model = _extract_device_model(probe_data, video_path)

    # 위치 감지: ISO6709 / Lat/Lon 개별 필드
    location = _extract_location(probe_data)

    # 컬러 정보
    color_space = video_stream.get("color_space")
    color_transfer = video_stream.get("color_transfer")
    color_primaries = video_stream.get("color_primaries")
    # tags와 stream_tags는 _find_metadata_location 내부에서 재사용

    location_raw = _find_metadata_location(video_path, probe_data)
    location = None
    location_latitude = None
    location_longitude = None
    if isinstance(location_raw, tuple):
        latitude, longitude = location_raw
        if _is_valid_lat_lon(latitude, longitude):
            location = _format_coordinate_pair(latitude, longitude)
            location_latitude = latitude
            location_longitude = longitude
    elif location_raw:
        location = location_raw

    # 오디오 스트림 존재 여부
    has_audio = any(s.get("codec_type") == "audio" for s in probe_data.get("streams", []))

    return VideoMetadata(
        width=width,
        height=height,
        duration_seconds=duration_seconds,
        fps=fps,
        codec=codec,
        pixel_format=pixel_format,
        is_portrait=is_portrait,
        is_vfr=is_vfr,
        device_model=device_model,
        location_latitude=location_latitude,
        location_longitude=location_longitude,
        color_space=color_space,
        color_transfer=color_transfer,
        color_primaries=color_primaries,
        location=location,
        has_audio=has_audio,
    )


_GOPRO_FIRMWARE_RE = re.compile(r"^HD(\d+)\.", re.IGNORECASE)
_APPLE_MODEL_LENS_RE = re.compile(r"\s+\d+mm$", re.IGNORECASE)

# exiftool Make 필드 정규화 테이블
_MAKE_OVERRIDES: dict[str, str] = {
    "NIKON CORPORATION": "Nikon",
    "NIKON": "Nikon",
    "CANON INC.": "Canon",
    "CANON": "Canon",
    "SONY": "Sony",
    "FUJIFILM": "Fujifilm",
    "PANASONIC": "Panasonic",
    "OM DIGITAL SOLUTIONS": "OM System",
    "OLYMPUS CORPORATION": "Olympus",
    "OLYMPUS IMAGING CORP.": "Olympus",
    "APPLE": "Apple",
}

# exiftool 미설치 경고는 1회만 출력
_exiftool_missing_warned = False


def _normalize_apple_model(raw: str) -> str:
    """iPhone/iPad 모델명을 정규화한다.

    - ``"Apple "`` 접두사 제거 (Blackmagic Camera 앱 등)
    - `` NNmm`` 렌즈 접미사 제거 (Blackmagic Camera 촬영 파일)

    Examples::

        "Apple iPhone 17 Pro Max 100mm" → "iPhone 17 Pro Max"
        "iPhone 17 Pro"                 → "iPhone 17 Pro"
    """
    model = raw.strip()
    if model.startswith("Apple "):
        model = model[6:].strip()
    return _APPLE_MODEL_LENS_RE.sub("", model).strip()


def _normalize_make(raw_make: str) -> str:
    """제조사 문자열을 표시용 이름으로 정규화한다."""
    upper = raw_make.strip().upper()
    for key, normalized in _MAKE_OVERRIDES.items():
        if upper == key:
            return normalized
    # 알 수 없는 제조사: 첫 단어를 Title Case로
    first = raw_make.strip().split()[0] if raw_make.strip() else ""
    return first.title() if first else raw_make.strip()


def _build_device_name(make: str | None, model: str | None) -> str | None:
    """exiftool Make + Model을 결합하여 표시용 기기명을 반환한다.

    - Make 정규화 후 Model의 중복 접두사 제거
    - GoPro는 Make 없이 Model만 있을 수 있어 "GoPro " 접두사를 보완
    """
    if not model:
        return None
    model = model.strip()
    if not model:
        return None

    # GoPro: Make 없이 Model이 "HERO"로 시작
    if not make and model.upper().startswith("HERO"):
        return f"GoPro {model}"

    if not make:
        return model

    norm_make = _normalize_make(make)

    # Model에 Make 접두사가 포함된 경우 제거 ("NIKON Z 8" → "Z 8" under make "Nikon")
    model_upper = model.upper()
    for prefix in (make.strip().upper(), norm_make.upper()):
        if prefix and model_upper.startswith(prefix):
            model = model[len(prefix) :].strip()
            break

    return f"{norm_make} {model}".strip() if model else norm_make


def _run_exiftool(video_path: Path) -> dict[str, Any]:
    """exiftool로 Make/Model 태그를 추출한다.

    exiftool이 설치되지 않은 경우 경고를 1회 출력하고 빈 dict를 반환한다.

    Args:
        video_path: 분석할 영상 파일 경로.

    Returns:
        ``{"Make": ..., "Model": ...}`` 형태의 dict. 태그가 없으면 빈 dict.
    """
    global _exiftool_missing_warned

    cmd = ["exiftool", "-j", "-Make", "-Model", str(video_path)]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        data: list[dict[str, Any]] = json.loads(result.stdout)
        return data[0] if data else {}
    except FileNotFoundError:
        if not _exiftool_missing_warned:
            logger.warning(
                "exiftool을 찾을 수 없습니다. Nikon/Canon/Sony 등의 카메라 모델 감지가 "
                "제한됩니다. 설치: brew install exiftool  (https://exiftool.org)"
            )
            _exiftool_missing_warned = True
        return {}
    except (subprocess.CalledProcessError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return {}


def _extract_device_model(probe_data: dict[str, Any], video_path: Path) -> str | None:
    """다양한 카메라 제조사 태그에서 기기 모델을 추출한다.

    우선순위:
    1. ``com.apple.quicktime.model`` — iPhone / iPad (정규화 적용)
    2. ``format.tags.encoder`` 가 "DJI"로 시작 — DJI 카메라
    3. ``format.tags.firmware`` "HD{n}.*" 패턴 — GoPro HERO {n}
    4. 비디오 스트림 ``handler_name`` 에 "gopro" 포함 — GoPro (모델 불명)
    5. exiftool Make + Model — Nikon / Canon / Sony / Fujifilm 등
    """
    format_tags = probe_data.get("format", {}).get("tags", {})

    # 1) Apple QuickTime (iPhone, iPad) — "Apple " 접두사·렌즈 접미사 정규화
    apple_model = format_tags.get("com.apple.quicktime.model")
    if isinstance(apple_model, str) and apple_model.strip():
        return _normalize_apple_model(apple_model)

    # 2) DJI: format.tags.encoder starts with "DJI"
    encoder = format_tags.get("encoder", "")
    if isinstance(encoder, str) and encoder.upper().startswith("DJI"):
        return encoder.strip()

    # 3) GoPro: format.tags.firmware "HD{n}.x.x" → "GoPro HERO {n}"
    firmware = format_tags.get("firmware", "")
    if isinstance(firmware, str):
        m = _GOPRO_FIRMWARE_RE.match(firmware)
        if m:
            return f"GoPro HERO {m.group(1)}"

    # 4) GoPro: video stream handler_name contains "gopro"
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        handler = stream.get("tags", {}).get("handler_name", "")
        if isinstance(handler, str) and "gopro" in handler.lower():
            return "GoPro"

    # 5) exiftool: Nikon / Canon / Sony 등 MakerNote 기반 카메라
    exif = _run_exiftool(video_path)
    return _build_device_name(exif.get("Make"), exif.get("Model"))


def _run_ffprobe(video_path: Path) -> dict[str, Any]:
    """ffprobe를 실행하여 스트림·포맷 정보를 JSON으로 반환한다.

    ``-show_streams -show_format`` 옵션으로 모든 스트림과
    컨테이너 메타데이터를 한 번에 추출한다.

    Args:
        video_path: 분석할 영상 파일 경로.

    Returns:
        ffprobe JSON 출력 (``streams``, ``format`` 키 포함).

    Raises:
        RuntimeError: ffprobe 실행 실패 또는 JSON 파싱 오류.
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(video_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        data: dict[str, Any] = json.loads(result.stdout)
        return data
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"ffprobe failed: {e.stderr}") from e
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Failed to parse ffprobe output: {e}") from e


def _parse_frame_rate(frame_rate_str: str) -> float:
    """FFmpeg ``r_frame_rate`` 분수 문자열을 float FPS로 변환한다.

    ``"30000/1001"`` → ``29.97``, ``"60/1"`` → ``60.0`` 등
    분수 형식을 :class:`fractions.Fraction` 으로 파싱한다.

    Args:
        frame_rate_str: 분수 또는 정수 형식 프레임 레이트 문자열.

    Returns:
        초당 프레임 수. 파싱 실패 시 ``0.0``.
    """
    try:
        fraction = Fraction(frame_rate_str)
        return float(fraction)
    except (ValueError, ZeroDivisionError):
        return 0.0
