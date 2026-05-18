"""트랜스코딩 스킵(stream-copy concat) 판정 테스트.

``_can_skip_transcoding`` 은 모든 입력이 이미 PROFILE_SDR과 정합하고
어떤 필터도 활성화되지 않은 경우에만 ``True`` 를 반환해야 한다.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from tubearchive.app.cli.pipeline import (
    TranscodeOptions,
    _can_skip_transcoding,
    _run_skip_transcoding,
)
from tubearchive.domain.models.video import FadeConfig, VideoFile, VideoMetadata


def _make_video_file(tmp_path: Path, name: str = "clip.mp4") -> VideoFile:
    """검증 통과용 최소 VideoFile."""
    path = tmp_path / name
    path.write_bytes(b"x")
    return VideoFile(
        path=path,
        creation_time=datetime(2026, 1, 1, 12, 0, 0),
        size_bytes=1,
    )


def _profile_sdr_metadata(**overrides: object) -> VideoMetadata:
    """PROFILE_SDR과 정합하는 기본 메타데이터 (테스트용)."""
    defaults: dict[str, object] = {
        "width": 3840,
        "height": 2160,
        "duration_seconds": 30.0,
        "fps": 30000 / 1001,
        "codec": "hevc",
        "pixel_format": "p010le",
        "is_portrait": False,
        "is_vfr": False,
        "device_model": "Nikon Z6III",
        "color_space": "bt709",
        "color_transfer": "bt709",
        "color_primaries": "bt709",
        "has_audio": True,
        "sar": "1:1",
        "audio_codec": "aac",
        "audio_sample_rate": 48000,
        "audio_channels": 2,
        "audio_stream_count": 1,
    }
    defaults.update(overrides)
    return VideoMetadata(**defaults)  # type: ignore[arg-type]


def _make_validated_args(tmp_path: Path) -> MagicMock:
    """``_can_skip_transcoding`` 은 ``validated_args``를 읽지 않으므로 빈 mock으로 충분.

    필터/이펙트 옵션은 ``TranscodeOptions``로 전달되며, ``validated_args`` 시그니처는
    인터페이스 호환만을 위해 받아둔다. ``args.foo`` 자동-생성 MagicMock에 의존하는
    분기가 추가되면 그때 명시 필드를 추가하자.
    """
    return MagicMock()


class TestCanSkipTranscoding:
    """``_can_skip_transcoding`` 의 긍정·부정 분기 검증."""

    def test_eligible_when_all_match(self, tmp_path: Path) -> None:
        """입력 메타가 모두 PROFILE_SDR과 정합 + 필터 비활성 → 스킵 가능."""
        files = [_make_video_file(tmp_path, "a.mp4"), _make_video_file(tmp_path, "b.mp4")]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is True
        assert "match PROFILE_SDR" in reason

    def test_blocked_by_denoise(self, tmp_path: Path) -> None:
        """오디오 denoise가 켜져 있으면 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions(denoise=True)
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "denoise" in reason

    def test_blocked_by_lut(self, tmp_path: Path) -> None:
        """LUT 경로가 지정되어 있으면 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions(lut_path=Path("/tmp/x.cube"))
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "LUT" in reason

    def test_blocked_by_stabilize(self, tmp_path: Path) -> None:
        """vidstab가 켜져 있으면 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions(stabilize=True)
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "stabilize" in reason

    def test_blocked_by_fade(self, tmp_path: Path) -> None:
        """fade_map에 non-zero 페이드가 있으면 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        fade_map = {files[0].path: FadeConfig(fade_in=0.5, fade_out=0.5)}
        opts = TranscodeOptions(fade_map=fade_map)
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "fade" in reason

    def test_zero_fade_map_does_not_block(self, tmp_path: Path) -> None:
        """그룹 내부 zero 페이드만 있으면 스킵 가능 (시퀀스 그룹핑 결과)."""
        files = [_make_video_file(tmp_path)]
        fade_map = {files[0].path: FadeConfig(fade_in=0.0, fade_out=0.0)}
        opts = TranscodeOptions(fade_map=fade_map)
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is True, reason

    def test_blocked_by_template_intro(self, tmp_path: Path) -> None:
        """인트로 템플릿이 있으면 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        intro = _make_video_file(tmp_path, "intro.mp4")
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, intro, None)

        assert can_skip is False
        assert "template" in reason

    def test_blocked_by_template_outro(self, tmp_path: Path) -> None:
        """아웃트로 템플릿이 있으면 스킵 불가 (인트로와 대칭)."""
        files = [_make_video_file(tmp_path)]
        outro = _make_video_file(tmp_path, "outro.mp4")
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, outro)

        assert can_skip is False
        assert "template" in reason

    def test_blocked_by_multi_track_audio(self, tmp_path: Path) -> None:
        """다중 오디오 트랙 입력은 stream-copy concat이 안전하지 않아 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(audio_stream_count=2),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "multi-track audio" in reason

    def test_blocked_by_h264_codec(self, tmp_path: Path) -> None:
        """입력 코덱이 hevc가 아니면 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(codec="h264"),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "h264" in reason

    def test_yuv420p10le_is_accepted_as_skip_eligible(self, tmp_path: Path) -> None:
        """hevc_videotoolbox는 p010le 요청 시 ffprobe가 yuv420p10le로 보고한다.

        두 이름은 모두 10-bit 4:2:0이므로 스킵 판정에서 양쪽을 수용해야 한다.
        """
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(pixel_format="yuv420p10le"),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is True, f"yuv420p10le should be accepted: {reason}"

    def test_blocked_by_wrong_pixel_format(self, tmp_path: Path) -> None:
        """yuv420p(8-bit)처럼 다른 픽셀 포맷이면 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(pixel_format="yuv420p"),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "yuv420p" in reason

    def test_blocked_by_resolution(self, tmp_path: Path) -> None:
        """4K가 아니면 스킵 불가 (PROFILE_SDR 기본 출력 해상도와 다름)."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(width=1920, height=1080),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "1920x1080" in reason

    def test_blocked_by_hdr_transfer(self, tmp_path: Path) -> None:
        """HDR(HLG/PQ)이면 SDR 변환이 필요하므로 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(color_transfer="arib-std-b67"),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "color transfer" in reason

    def test_blocked_by_portrait(self, tmp_path: Path) -> None:
        """세로 영상은 레이아웃 필터가 필요해 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(width=2160, height=3840, is_portrait=True),
        ):
            can_skip, _reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False

    def test_blocked_by_vfr(self, tmp_path: Path) -> None:
        """VFR 영상은 concat 호환성 위험 → 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(is_vfr=True),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "variable frame rate" in reason

    def test_blocked_by_audio_codec_mismatch(self, tmp_path: Path) -> None:
        """오디오 코덱이 aac가 아니면 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(audio_codec="pcm_s16le"),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "audio codec" in reason

    def test_blocked_by_sample_rate_mismatch(self, tmp_path: Path) -> None:
        """샘플레이트가 48000이 아니면 스킵 불가 (loudnorm 96kHz 회귀 방지)."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(audio_sample_rate=96000),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "sample rate" in reason

    def test_blocked_by_heterogeneous_codec(self, tmp_path: Path) -> None:
        """입력 파일들이 서로 다른 코덱을 가지면 스킵 불가."""
        files = [
            _make_video_file(tmp_path, "a.mp4"),
            _make_video_file(tmp_path, "b.mp4"),
        ]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)
        metas = iter([_profile_sdr_metadata(), _profile_sdr_metadata(codec="h264")])

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            side_effect=lambda *_a, **_k: next(metas),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "heterogeneous codec" in reason

    def test_blocked_by_non_square_sar(self, tmp_path: Path) -> None:
        """비-정사각 픽셀은 concat 안전성 보장 어려움 → 스킵 불가."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(sar="40:33"),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "non-square pixels" in reason

    def test_sar_none_is_treated_as_square(self, tmp_path: Path) -> None:
        """ffprobe가 SAR을 보고하지 않는 경우(``None``)는 정사각으로 간주."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            return_value=_profile_sdr_metadata(sar=None),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is True, reason

    def test_metadata_probe_failure_yields_false(self, tmp_path: Path) -> None:
        """ffprobe 자체가 실패하면 일반 경로로 폴백 (False 반환)."""
        files = [_make_video_file(tmp_path)]
        opts = TranscodeOptions()
        args = _make_validated_args(tmp_path)

        with patch(
            "tubearchive.app.cli.pipeline.detect_metadata",
            side_effect=RuntimeError("ffprobe gone"),
        ):
            can_skip, reason, _metas = _can_skip_transcoding(files, opts, args, None, None)

        assert can_skip is False
        assert "probe failed" in reason


class TestRunSkipTranscoding:
    """``_run_skip_transcoding`` 이 원본 경로를 그대로 가진 결과를 만들어내는지 검증."""

    def test_returns_results_pointing_to_originals(self, tmp_path: Path) -> None:
        """결과의 output_path가 원본 파일 경로와 동일해야 한다."""
        files = [
            _make_video_file(tmp_path, "a.mp4"),
            _make_video_file(tmp_path, "b.mp4"),
        ]
        metadata_cache = {
            files[0].path: _profile_sdr_metadata(),
            files[1].path: _profile_sdr_metadata(),
        }

        with patch("tubearchive.app.cli.pipeline.Transcoder") as mock_transcoder_cls:
            mock_transcoder = MagicMock()
            mock_transcoder.__enter__.return_value = mock_transcoder
            mock_transcoder.register_video.side_effect = [11, 12]
            mock_transcoder.resume_mgr.get_or_create_job.side_effect = [101, 102]
            mock_transcoder_cls.return_value = mock_transcoder

            results = _run_skip_transcoding(files, tmp_path, metadata_cache)

        assert [r.output_path for r in results] == [files[0].path, files[1].path]
        assert [r.video_id for r in results] == [11, 12]
        # 두 파일 모두 mark_completed가 원본 경로로 호출되어야 한다
        mark_calls = mock_transcoder.job_repo.mark_completed.call_args_list
        assert mark_calls[0].args == (101, files[0].path)
        assert mark_calls[1].args == (102, files[1].path)

    def test_falls_back_to_probe_when_cache_misses(self, tmp_path: Path) -> None:
        """캐시에 없는 경로는 ``detect_metadata``로 재-probe해야 한다 (안전 그물)."""
        files = [_make_video_file(tmp_path, "a.mp4")]

        with (
            patch("tubearchive.app.cli.pipeline.Transcoder") as mock_transcoder_cls,
            patch(
                "tubearchive.app.cli.pipeline.detect_metadata",
                return_value=_profile_sdr_metadata(),
            ) as mock_probe,
        ):
            mock_transcoder = MagicMock()
            mock_transcoder.__enter__.return_value = mock_transcoder
            mock_transcoder.register_video.return_value = 1
            mock_transcoder.resume_mgr.get_or_create_job.return_value = 10
            mock_transcoder_cls.return_value = mock_transcoder

            results = _run_skip_transcoding(files, tmp_path, metadata_cache={})

        assert len(results) == 1
        mock_probe.assert_called_once_with(files[0].path)


class TestCleanupTempPreservesSourceClips:
    """``_cleanup_temp`` 가 스킵 모드의 원본 클립을 삭제하지 않는지 검증.

    스킵 경로에서 ``TranscodeResult.output_path``는 원본 입력 파일이라
    무차별 unlink하면 사용자의 소스가 사라진다. (CodeRabbit P0 리뷰 회귀 테스트)
    """

    def test_original_files_outside_temp_dir_not_deleted(self, tmp_path: Path) -> None:
        from tubearchive.app.cli.pipeline import (
            TranscodeResult,
            _cleanup_temp,
        )
        from tubearchive.domain.models.clip import ClipInfo

        # 원본 입력은 source_dir(임시 디렉토리 외부)에 위치
        source_dir = tmp_path / "originals"
        source_dir.mkdir()
        source_file = source_dir / "clip.mp4"
        source_file.write_bytes(b"original-data")

        temp_dir = tmp_path / "tmp_pipeline"
        temp_dir.mkdir()

        # 스킵 모드: output_path == 원본 경로
        result = TranscodeResult(
            output_path=source_file,
            video_id=1,
            clip_info=ClipInfo(name="clip.mp4", duration=10.0, device=None, shot_time=None),
            silence_segments=None,
        )

        final_path = temp_dir / "out.mp4"
        final_path.write_bytes(b"merged-data")

        _cleanup_temp(temp_dir, [result], final_path)

        # 원본은 그대로, temp_dir은 정리됨
        assert source_file.exists(), "원본 클립이 삭제되었음 — _cleanup_temp 가드 누락"
        assert source_file.read_bytes() == b"original-data"
        assert not temp_dir.exists()
