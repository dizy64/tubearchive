"""트랜스코딩 출력 품질 지표 계산.

병합 파이프라인에서 입력 파일 대비 트랜스코딩 결과의 SSIM/PSNR/VMAF를
계산해 리포트한다.

현재 환경에서 필터가 지원되지 않을 수 있으므로 각 지표는 개별적으로
지원 여부를 확인한 뒤 실패하더라도 전체 파이프라인이 중단되지 않게
Graceful하게 처리한다.
"""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from tubearchive.core.detector import detect_metadata
from tubearchive.ffmpeg.executor import FFmpegExecutor
from tubearchive.models.video import VideoMetadata

logger = logging.getLogger(__name__)

SUPPORTED_METRICS = ("ssim", "psnr", "vmaf")
FFMPEG_ANALYSIS_FILTERS = {
    "ssim": "ssim",
    "psnr": "psnr",
    "vmaf": "libvmaf",
}


@dataclass(frozen=True)
class QualityReport:
    """단일 클립의 품질 리포트.

    Attributes:
        source_path: 원본 영상 경로
        output_path: 트랜스코딩 결과 영상 경로
        ssim: SSIM 점수 (0~1) 또는 미측정(None)
        psnr: PSNR 점수 (dB) 또는 미측정(None)
        vmaf: VMAF 점수 (0~100) 또는 미측정(None)
        unavailable: 지원되지 않거나 실행 실패로 미측정된 지표 집합
    """

    source_path: Path
    output_path: Path
    ssim: float | None = None
    psnr: float | None = None
    vmaf: float | None = None
    unavailable: tuple[str, ...] = field(default_factory=tuple)
    errors: tuple[str, ...] = field(default_factory=tuple)


def _parse_float(value: str) -> float | None:
    """문자열 숫자를 안전하게 float로 변환한다."""
    try:
        parsed = float(value)
    except ValueError:
        return None

    if not math.isfinite(parsed):
        return None
    return parsed


def parse_ssim_output(stderr: str) -> float | None:
    """SSIM stderr에서 마지막 유효 ``All`` 점수를 추출한다."""
    pattern = re.compile(r"All:([0-9.+eE-]+|inf|nan)", re.IGNORECASE)
    value: float | None = None
    for match in pattern.finditer(stderr):
        parsed = _parse_float(match.group(1))
        if parsed is not None:
            value = parsed
    return value


def parse_psnr_output(stderr: str) -> float | None:
    """PSNR stderr에서 마지막 유효 평균값을 추출한다."""
    pattern = re.compile(r"average:([0-9.+eE-]+|inf|nan)", re.IGNORECASE)
    value: float | None = None
    for match in pattern.finditer(stderr):
        parsed = _parse_float(match.group(1))
        if parsed is not None:
            value = parsed
    return value


def parse_vmaf_output(stderr: str) -> float | None:
    """VMAF 출력에서 마지막 유효 점수를 추출한다."""
    value: float | None = None
    # JSON 로그(JSONL) 파서
    for line in stderr.splitlines():
        if "{" not in line:
            continue
        try:
            payload_text = line[line.index("{") :].strip()
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        score = _extract_vmaf_from_payload(payload)
        if score is not None:
            value = score

    # 일반 텍스트 출력 fallback
    pattern = re.compile(r"VMAF.*score[:=]\s*([0-9.+eE-]+|inf|nan)", re.IGNORECASE)
    for match in pattern.finditer(stderr):
        parsed = _parse_float(match.group(1))
        if parsed is not None:
            value = parsed

    return value


def _extract_vmaf_from_payload(
    payload: object,
    *,
    allow_any_numeric: bool = False,
) -> float | None:
    """libvmaf JSON payload에서 VMAF 점수를 추출한다."""
    if isinstance(payload, (int, float, str)):
        parsed = _parse_float(str(payload))
        if parsed is not None:
            return parsed

    if isinstance(payload, dict):
        # metrics/vmaf 키 안의 값을 우선 확인
        for key in ("metrics", "metrics.vmaf", "pooled_metrics"):
            value = payload.get(key)
            if value is None:
                continue
            score = _extract_vmaf_from_payload(
                value,
                allow_any_numeric=allow_any_numeric,
            )
            if score is not None:
                return score

        for key, value in payload.items():
            key_lower = str(key).lower()
            if "vmaf" in key_lower:
                score = _extract_vmaf_from_payload(
                    value,
                    allow_any_numeric=True,
                )
                if score is not None:
                    return score
            if key_lower in {"frames", "metrics", "pooled_metrics"}:
                score = _extract_vmaf_from_payload(
                    value,
                    allow_any_numeric=allow_any_numeric,
                )
                if score is not None:
                    return score

        if allow_any_numeric:
            for value in payload.values():
                if isinstance(value, (dict, list)):
                    score = _extract_vmaf_from_payload(
                        value,
                        allow_any_numeric=True,
                    )
                    if score is not None:
                        return score
                score = _parse_float(str(value))
                if score is not None:
                    return score
        return None

    if isinstance(payload, list):
        for item in payload:
            score = _extract_vmaf_from_payload(
                item,
                allow_any_numeric=allow_any_numeric,
            )
            if score is not None:
                return score
    return None


@lru_cache(maxsize=1)
def _get_available_filters() -> set[str]:
    """ffmpeg에서 사용 가능한 필터 집합을 반환한다."""
    result = subprocess.run(
        ["ffmpeg", "-filters"],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = result.stdout.splitlines()
    filters: set[str] = set()
    pattern = re.compile(r"^\s*[A-Z.]{3}\s+(\S+)")
    for line in lines:
        match = pattern.match(line)
        if match:
            filters.add(match.group(1))
    return filters


def is_filter_supported(filter_name: str) -> bool:
    """필터 지원 여부를 확인한다."""
    filters = _get_available_filters()
    return filter_name in filters


def _format_fps_value(fps: float) -> str:
    return f"{fps:g}"


def _build_metric_filter_chain(
    metric: str,
    width: int,
    height: int,
    fps: float | None,
) -> str:
    """필터 체인을 생성한다."""
    fps_filter = f",fps={_format_fps_value(fps)}" if fps else ""
    common_filter = f"{fps_filter},scale={width}:{height},format=yuv420p"
    if metric == "ssim":
        return f"[0:v]{common_filter}[a];[1:v]{common_filter}[b];[a][b]ssim=stats_file=-"
    if metric == "psnr":
        return f"[0:v]{common_filter}[a];[1:v]{common_filter}[b];[a][b]psnr=stats_file=-"
    if metric == "vmaf":
        return (
            f"[0:v]{common_filter}[a];[1:v]{common_filter}[b];[a][b]libvmaf=log_fmt=json:log_path=-"
        )
    msg = f"Unsupported quality metric: {metric}"
    raise ValueError(msg)


def _run_metric_analysis(
    reference: Path,
    output: Path,
    metric: str,
    meta: VideoMetadata,
) -> float | None:
    """단일 지표 분석을 수행한다."""
    width = meta.width
    height = meta.height
    fps = meta.fps if meta.fps > 0 else None
    cmd = [
        "ffmpeg",
        "-i",
        str(reference),
        "-i",
        str(output),
        "-filter_complex",
        _build_metric_filter_chain(metric, width, height, fps),
        "-f",
        "null",
        "-",
    ]
    logger.debug("Running quality metric %s for %s", metric, output.name)
    stderr = FFmpegExecutor().run_analysis(cmd)
    if metric == "ssim":
        return parse_ssim_output(stderr)
    if metric == "psnr":
        return parse_psnr_output(stderr)
    if metric == "vmaf":
        return parse_vmaf_output(stderr)
    return None


def generate_quality_reports(
    pairs: list[tuple[Path, Path]],
) -> list[QualityReport]:
    """원본-출력 쌍별 품질 지표를 계산한다."""
    if not pairs:
        return []

    supported_cache = {
        metric: is_filter_supported(ffmpeg_filter)
        for metric, ffmpeg_filter in FFMPEG_ANALYSIS_FILTERS.items()
    }

    reports: list[QualityReport] = []
    for source_path, output_path in pairs:
        unavailable: list[str] = []
        errors: list[str] = []
        if not source_path.exists() or not output_path.exists():
            msg = "source_path 또는 output_path가 존재하지 않습니다."
            reports.append(
                QualityReport(
                    source_path=source_path,
                    output_path=output_path,
                    unavailable=tuple(SUPPORTED_METRICS),
                    errors=(msg,),
                )
            )
            continue

        try:
            meta = detect_metadata(output_path)
        except Exception as exc:
            logger.warning("Metadata check failed for %s: %s", output_path, exc)
            msg = f"메타데이터 확인 실패: {output_path.name}"
            reports.append(
                QualityReport(
                    source_path=source_path,
                    output_path=output_path,
                    unavailable=tuple(SUPPORTED_METRICS),
                    errors=(msg,),
                )
            )
            continue

        scores: dict[str, float | None] = {}
        for metric in SUPPORTED_METRICS:
            if not supported_cache.get(metric, False):
                unavailable.append(metric)
                continue
            try:
                score = _run_metric_analysis(source_path, output_path, metric, meta)
                scores[metric] = score
                if score is None:
                    unavailable.append(metric)
                    errors.append(f"{metric}: 점수 추출 실패")
            except Exception as exc:
                logger.warning("Quality metric '%s' failed (%s): %s", metric, output_path, exc)
                unavailable.append(metric)
                errors.append(f"{metric}: {exc}")

        report = QualityReport(
            source_path=source_path,
            output_path=output_path,
            ssim=scores.get("ssim"),
            psnr=scores.get("psnr"),
            vmaf=scores.get("vmaf"),
            unavailable=tuple(unavailable),
            errors=tuple(errors),
        )
        reports.append(report)
    return reports
