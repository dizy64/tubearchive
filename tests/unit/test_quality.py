"""품질 지표 계산 유닛 테스트."""

from pathlib import Path
from unittest.mock import patch

from tubearchive.core.quality import (
    generate_quality_reports,
    parse_psnr_output,
    parse_ssim_output,
    parse_vmaf_output,
)
from tubearchive.models.video import VideoMetadata


class TestMetricParser:
    """지표 파서 테스트."""

    def test_parse_ssim_output_uses_last_valid_value(self) -> None:
        """SSIM 값은 마지막 유효 All 수치로 파싱한다."""
        stderr = (
            "[Parsed_ssim_0] All:0.1234 (..)\n"
            "[Parsed_ssim_0] All:nan (..)\n"
            "[Parsed_ssim_0] All:0.9876 (..)\n"
        )

        value = parse_ssim_output(stderr)

        assert value == 0.9876

    def test_parse_psnr_output_uses_last_valid_value(self) -> None:
        """PSNR 값은 마지막 유효 average 수치로 파싱한다."""
        stderr = "psnr: average:30.2: ...\npsnr: average:inf: ...\npsnr: average:31.8: ...\n"

        value = parse_psnr_output(stderr)

        assert value == 31.8

    def test_parse_vmaf_output_reads_json_payload(self) -> None:
        """libvmaf JSONL payload에서 VMAF 점수를 파싱한다."""
        stderr = (
            "frame 1/10 ...\n"
            '{"version":"1.0","frames":[{"metrics":{"vmaf_score":94.4}}]}\n'
            'final: {"pooled_metrics":{"vmaf":{"mean":95.2}}}\n'
        )

        value = parse_vmaf_output(stderr)

        assert value == 95.2

    def test_parse_vmaf_output_fallback_to_text(self) -> None:
        """텍스트 출력에서도 VMAF score를 파싱한다."""
        stderr = "VMAF score: 82.5 (0.00)\n"

        value = parse_vmaf_output(stderr)

        assert value == 82.5


class TestGenerateQualityReports:
    """리포트 생성 테스트."""

    @staticmethod
    def _metadata() -> VideoMetadata:
        return VideoMetadata(
            width=1920,
            height=1080,
            duration_seconds=1.0,
            fps=30.0,
            codec="h264",
            pixel_format="yuv420p",
            is_portrait=False,
            is_vfr=False,
            device_model=None,
            color_space=None,
            color_transfer=None,
            color_primaries=None,
            has_audio=True,
        )

    def test_generate_quality_reports_marks_missing_files(self, tmp_path: Path) -> None:
        """존재하지 않는 파일은 미측정 처리."""
        source = tmp_path / "missing.mov"
        output = tmp_path / "output.mov"
        output.write_bytes(b"dummy")

        reports = generate_quality_reports([(source, output)])

        assert len(reports) == 1
        report = reports[0]
        assert report.unavailable == ("ssim", "psnr", "vmaf")
        assert report.errors == ("source_path 또는 output_path가 존재하지 않습니다.",)
        assert report.ssim is None
        assert report.psnr is None
        assert report.vmaf is None

    def test_generate_quality_reports_marks_unavailable_when_score_missing(
        self,
        tmp_path: Path,
    ) -> None:
        """지표 계산 실패 시 미측정으로 기록한다."""
        source = tmp_path / "in.mov"
        output = tmp_path / "out.mov"
        source.write_bytes(b"dummy")
        output.write_bytes(b"dummy")

        with (
            patch("tubearchive.core.quality.detect_metadata", return_value=self._metadata()),
            patch("tubearchive.core.quality.is_filter_supported", return_value=True),
            patch(
                "tubearchive.core.quality._run_metric_analysis",
                side_effect=[0.987, None, None],
            ),
        ):
            reports = generate_quality_reports([(source, output)])

        assert len(reports) == 1
        report = reports[0]
        assert report.ssim == 0.987
        assert report.psnr is None
        assert report.vmaf is None
        assert report.unavailable == ("psnr", "vmaf")

    def test_generate_quality_reports_keeps_available_metrics(self, tmp_path: Path) -> None:
        """일부 지표만 미지원이어도 사용 가능한 지표는 유지한다."""
        source = tmp_path / "in.mov"
        output = tmp_path / "out.mov"
        source.write_bytes(b"dummy")
        output.write_bytes(b"dummy")

        with (
            patch("tubearchive.core.quality.detect_metadata", return_value=self._metadata()),
            patch("tubearchive.core.quality.is_filter_supported", side_effect=[True, True, False]),
            patch(
                "tubearchive.core.quality._run_metric_analysis",
                side_effect=[0.99, 34.5],
            ),
        ):
            reports = generate_quality_reports([(source, output)])

        assert len(reports) == 1
        report = reports[0]
        assert report.ssim == 0.99
        assert report.psnr == 34.5
        assert report.vmaf is None
        assert report.unavailable == ("vmaf",)
