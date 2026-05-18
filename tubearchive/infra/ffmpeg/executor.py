"""FFmpeg 서브프로세스 실행기.

FFmpeg / ffprobe 명령을 구성하고 서브프로세스로 실행한다.
표준 에러(stderr)를 실시간 파싱하여 진행률 콜백을 호출하고,
오류 발생 시 :class:`FFmpegError` 를 raise 한다.

주요 역할:
    - 트랜스코딩 명령 빌드 (``build_transcode_command``)
    - 병합 명령 빌드 (``build_concat_command``)
    - 라우드니스 분석 명령 빌드 (``build_loudness_analysis_command``)
    - 프로세스 실행 및 진행률 파싱 (``run``, ``run_analysis``)
"""

import logging
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from tubearchive.infra.ffmpeg.profiles import EncodingProfile
from tubearchive.shared.progress import ProgressInfo

logger = logging.getLogger(__name__)


class FFmpegError(Exception):
    """FFmpeg 프로세스가 0이 아닌 종료 코드로 실패했을 때 발생하는 예외.

    Attributes:
        stderr: FFmpeg stderr 전체 출력 (디버깅용)
    """

    def __init__(self, message: str, stderr: str | None = None) -> None:
        """초기화."""
        super().__init__(message)
        self.stderr = stderr

    def __str__(self) -> str:
        """에러 메시지와 stderr 마지막 줄들을 함께 표시."""
        base = super().__str__()
        if not self.stderr:
            return base
        # stderr 마지막 20줄만 표시 (전체는 너무 길 수 있음)
        lines = self.stderr.strip().splitlines()
        tail = lines[-20:] if len(lines) > 20 else lines
        stderr_snippet = "\n".join(tail)
        return f"{base}\n--- FFmpeg stderr (last {len(tail)} lines) ---\n{stderr_snippet}"


def parse_progress_line(line: str) -> dict[str, float] | None:
    """FFmpeg stderr 진행률 라인에서 처리 상태를 추출한다.

    ``time=HH:MM:SS.CC`` 패턴이 없으면 None을 반환한다.
    나머지 필드(frame, fps, bitrate)는 있는 경우에만 포함된다.

    Args:
        line: FFmpeg stderr 출력 한 줄.
            예: ``"frame=1234 fps=29.97 time=00:01:30.50 bitrate=5000.0kbits/s"``

    Returns:
        파싱 결과 딕셔너리. ``time=`` 이 없으면 None.

        - ``time_seconds`` (float): 처리된 시점 (초). **항상 포함**.
        - ``frame`` (float): 처리된 프레임 수. 선택.
        - ``fps`` (float): 현재 처리 속도 (frames/sec). 선택.
        - ``bitrate`` (float): 현재 비트레이트 (kbits/s). 선택.
    """
    # time= 파싱
    time_match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", line)
    if not time_match:
        return None

    hours = int(time_match.group(1))
    minutes = int(time_match.group(2))
    seconds = int(time_match.group(3))
    centiseconds = int(time_match.group(4))

    time_seconds = hours * 3600 + minutes * 60 + seconds + centiseconds / 100

    result: dict[str, float] = {"time_seconds": time_seconds}

    # frame= 파싱
    frame_match = re.search(r"frame=\s*(\d+)", line)
    if frame_match:
        result["frame"] = float(frame_match.group(1))

    # fps= 파싱
    fps_match = re.search(r"fps=\s*([\d.]+)", line)
    if fps_match:
        result["fps"] = float(fps_match.group(1))

    # bitrate= 파싱
    bitrate_match = re.search(r"bitrate=\s*([\d.]+)kbits/s", line)
    if bitrate_match:
        result["bitrate"] = float(bitrate_match.group(1))

    return result


class FFmpegExecutor:
    """FFmpeg 서브프로세스 빌더 및 실행기.

    명령어 빌드(``build_*``)와 실행(``run``, ``run_analysis``)을 분리하여
    테스트 가능성을 높이고, stderr 파싱으로 실시간 진행률 콜백을 지원한다.
    """

    def __init__(self, ffmpeg_path: str = "ffmpeg") -> None:
        """초기화."""
        self.ffmpeg_path = ffmpeg_path

    @property
    def ffprobe_path(self) -> str:
        """ffmpeg 실행 파일명 기준으로 대응되는 ffprobe 경로를 반환한다."""
        ffmpeg = Path(self.ffmpeg_path)
        if "ffmpeg" not in ffmpeg.name:
            return "ffprobe"
        return str(ffmpeg.with_name(ffmpeg.name.replace("ffmpeg", "ffprobe", 1)))

    def build_transcode_command(
        self,
        input_path: Path,
        output_path: Path,
        profile: EncodingProfile,
        video_filter: str | None = None,
        audio_filter: str | None = None,
        filter_complex: str | None = None,
        overwrite: bool = True,
        seek_start: float | None = None,
        has_audio: bool = True,
        external_audio_path: Path | None = None,
        external_audio_offset: float = 0.0,
        external_audio_mode: str = "replace",
        camera_audio_volume: float = 0.1,
        external_audio_tempo: float = 1.0,
        external_audio_start: float | None = None,
        external_audio_duration: float | None = None,
    ) -> list[str]:
        """
        트랜스코딩 FFmpeg 명령어 빌드.

        Args:
            input_path: 입력 파일 경로
            output_path: 출력 파일 경로
            profile: 인코딩 프로파일
            video_filter: 비디오 필터 (-vf)
            audio_filter: 오디오 필터 (-af)
            filter_complex: 복합 필터 (-filter_complex)
            overwrite: 덮어쓰기 여부
            seek_start: 시작 위치 (초)
            has_audio: 입력 파일에 오디오 스트림 존재 여부.
                False이면 anullsrc로 무음 오디오를 생성하여
                concat 병합 호환성을 유지한다.
            external_audio_path: 영상 내장 오디오 대신 사용할 외부 오디오 파일.
            external_audio_offset: 외부 오디오 입력에 적용할 시간 offset(초).
            external_audio_mode: 외부 오디오 적용 방식 ("replace" 또는 "mix").
            camera_audio_volume: mix 모드에서 카메라 내장 오디오 볼륨.
            external_audio_tempo: drift 보정용 외부 오디오 atempo 비율.
            external_audio_start: 긴 외부 녹음에서 사용할 시작 시점(초).
            external_audio_duration: 긴 외부 녹음에서 사용할 길이(초).

        Returns:
            FFmpeg 명령어 리스트
        """
        cmd = [self.ffmpeg_path]

        # 덮어쓰기
        if overwrite:
            cmd.append("-y")

        # PTS 재생성 (A/V 싱크 문제 방지)
        cmd.extend(["-fflags", "+genpts"])

        # 시작 위치 (Resume용)
        if seek_start is not None and seek_start > 0:
            cmd.extend(["-ss", str(seek_start)])

        # 입력 파일
        cmd.extend(["-i", str(input_path)])

        if external_audio_path is not None:
            if external_audio_start is not None:
                cmd.extend(["-ss", f"{external_audio_start:g}"])
            if external_audio_duration is not None:
                cmd.extend(["-t", f"{external_audio_duration:g}"])
            if external_audio_offset:
                cmd.extend(["-itsoffset", f"{external_audio_offset:g}"])
            cmd.extend(["-i", str(external_audio_path)])

        # 오디오 스트림이 없으면 lavfi 무음 입력 추가 (concat 호환성)
        # 입력 인덱스: 0=원본 비디오, 1=anullsrc 무음
        if external_audio_path is None and not has_audio and (filter_complex or video_filter):
            cmd.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                ]
            )

        if external_audio_mode not in {"replace", "mix"}:
            raise ValueError(f"Unsupported external audio mode: {external_audio_mode}")
        if not (0.5 <= external_audio_tempo <= 2.0):
            raise ValueError(
                f"external_audio_tempo must be in range [0.5, 2.0]: {external_audio_tempo}"
            )

        # 오디오 매핑 소스: 입력에 오디오 있으면 0:a:0, 없으면 1:a:0 (anullsrc)
        if external_audio_path is not None:
            audio_map = "1:a:0"
        else:
            audio_map = "0:a:0" if has_audio else "1:a:0"

        # 필터 및 스트림 매핑
        if external_audio_path is not None and external_audio_mode == "mix" and has_audio:
            base_video_filter = filter_complex or video_filter or "null"
            mixed_audio_chain = (
                "[camera_a][external_a]amix=inputs=2:duration=first:"
                "dropout_transition=0:weights=1 1"
            )
            if audio_filter:
                mixed_audio_chain = f"{mixed_audio_chain},{audio_filter}"
            external_audio_chain = (
                f"atempo={external_audio_tempo:g}" if external_audio_tempo != 1.0 else "anull"
            )
            mix_audio_filter = (
                f"[0:a:0]volume={camera_audio_volume:g}[camera_a];"
                f"[1:a:0]{external_audio_chain}[external_a];"
                f"{mixed_audio_chain}[a_out]"
            )
            if filter_complex:
                cmd.extend(["-filter_complex", f"{base_video_filter};{mix_audio_filter}"])
                cmd.extend(["-map", "[v_out]", "-map", "[a_out]"])
            else:
                if "[v_out]" in base_video_filter:
                    video_graph = base_video_filter
                else:
                    video_graph = f"[0:v]{base_video_filter}[v_out]"
                cmd.extend(["-filter_complex", f"{video_graph};{mix_audio_filter}"])
                cmd.extend(["-map", "[v_out]", "-map", "[a_out]"])
        elif filter_complex:
            cmd.extend(["-filter_complex", filter_complex])
            cmd.extend(["-map", "[v_out]", "-map", audio_map])
        elif video_filter:
            # 명시적 매핑: 첫 번째 비디오/오디오 스트림만 선택
            # (iPhone 등 mebx data 스트림이 포함된 파일에서 디코더 오류 방지)
            cmd.extend(["-map", "0:v:0", "-map", audio_map])
            cmd.extend(["-vf", video_filter])

        # 오디오 필터는 실제 오디오가 있을 때만 적용 (무음에는 불필요)
        effective_audio_filter = audio_filter
        if external_audio_path is not None and external_audio_mode != "mix":
            external_filters: list[str] = []
            if external_audio_tempo != 1.0:
                external_filters.append(f"atempo={external_audio_tempo:g}")
            if audio_filter:
                external_filters.append(audio_filter)
            external_filters.append("apad")
            effective_audio_filter = ",".join(external_filters)

        if (
            effective_audio_filter
            and (has_audio or external_audio_path is not None)
            and external_audio_mode != "mix"
        ):
            cmd.extend(["-af", effective_audio_filter])

        # 인코딩 프로파일 적용
        cmd.extend(profile.to_ffmpeg_args())

        # 음수 타임스탬프 방지 (concat 병합 시 PTS 불연속 해결)
        cmd.extend(["-avoid_negative_ts", "make_zero"])

        # anullsrc는 무한 길이이므로 비디오 종료 시 같이 종료
        if (external_audio_path is not None or not has_audio) and (filter_complex or video_filter):
            cmd.append("-shortest")

        # 출력 파일
        cmd.append(str(output_path))

        return cmd

    def build_concat_command(
        self,
        concat_file: Path,
        output_path: Path,
        overwrite: bool = True,
    ) -> list[str]:
        """
        concat 병합 FFmpeg 명령어 빌드.

        Args:
            concat_file: concat 텍스트 파일 경로
            output_path: 출력 파일 경로
            overwrite: 덮어쓰기 여부

        Returns:
            FFmpeg 명령어 리스트
        """
        cmd = [self.ffmpeg_path]

        if overwrite:
            cmd.append("-y")

        cmd.extend(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_file),
                "-c",
                "copy",
                str(output_path),
            ]
        )

        return cmd

    def run(
        self,
        cmd: list[str],
        total_duration: float,
        progress_callback: Callable[[int], None] | None = None,
        progress_info_callback: Callable[[ProgressInfo], None] | None = None,
    ) -> None:
        """
        FFmpeg 명령 실행.

        Args:
            cmd: FFmpeg 명령어 리스트
            total_duration: 영상 전체 길이 (초)
            progress_callback: 진행률 콜백 (0-100), 하위 호환용
            progress_info_callback: 상세 진행률 콜백 (ProgressInfo)

        Raises:
            FFmpegError: FFmpeg 실행 실패
        """
        logger.info(f"Running FFmpeg: {' '.join(cmd)}")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        stderr_lines: list[str] = []

        # stderr에서 진행률 읽기
        if process.stderr:
            for line in process.stderr:
                stderr_lines.append(line)
                progress = parse_progress_line(line)

                if progress and total_duration > 0:
                    percent = self.calculate_progress_percent(
                        progress["time_seconds"],
                        total_duration,
                    )

                    # 새로운 상세 콜백 우선
                    if progress_info_callback:
                        info = ProgressInfo(
                            percent=percent,
                            current_time=progress["time_seconds"],
                            total_duration=total_duration,
                            fps=progress.get("fps", 0.0),
                        )
                        progress_info_callback(info)
                    elif progress_callback:
                        progress_callback(percent)

        # 프로세스 완료 대기
        return_code = process.wait()

        if return_code != 0:
            stderr_output = "".join(stderr_lines)
            logger.error(f"FFmpeg failed with code {return_code}: {stderr_output}")
            raise FFmpegError(
                f"FFmpeg failed with exit code {return_code}",
                stderr=stderr_output,
            )

        logger.info("FFmpeg completed successfully")

    def _build_analysis_command(
        self,
        input_path: Path,
        filter_flag: str,
        filter_str: str,
        suppress_flag: str,
    ) -> list[str]:
        """1st pass 분석용 공통 FFmpeg 명령어 빌드.

        Args:
            input_path: 입력 파일 경로
            filter_flag: 필터 플래그 ("-af" 또는 "-vf")
            filter_str: 필터 문자열
            suppress_flag: 미사용 스트림 억제 플래그 ("-vn" 또는 "-an")

        Returns:
            FFmpeg 명령어 리스트
        """
        return [
            self.ffmpeg_path,
            "-i",
            str(input_path),
            filter_flag,
            filter_str,
            suppress_flag,
            "-f",
            "null",
            os.devnull,
        ]

    def build_loudness_analysis_command(
        self,
        input_path: Path,
        audio_filter: str,
    ) -> list[str]:
        """loudnorm 1st pass 분석용 FFmpeg 명령어 빌드."""
        return self._build_analysis_command(input_path, "-af", audio_filter, "-vn")

    def build_silence_detection_command(
        self,
        input_path: Path,
        audio_filter: str,
    ) -> list[str]:
        """무음 구간 감지용 FFmpeg 명령어 빌드."""
        return self._build_analysis_command(input_path, "-af", audio_filter, "-vn")

    def build_vidstab_detect_command(
        self,
        input_path: Path,
        video_filter: str,
    ) -> list[str]:
        """vidstab detect (1st pass) 분석용 FFmpeg 명령어 빌드."""
        return self._build_analysis_command(input_path, "-vf", video_filter, "-an")

    def run_analysis(self, cmd: list[str]) -> str:
        """
        분석용 FFmpeg 명령 실행 (stderr 반환).

        진행률 콜백 없이 실행하고 stderr 전체를 반환한다.

        Args:
            cmd: FFmpeg 명령어 리스트

        Returns:
            FFmpeg stderr 전체 출력

        Raises:
            FFmpegError: FFmpeg 실행 실패
        """
        logger.info(f"Running FFmpeg analysis: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error(f"FFmpeg analysis failed with code {result.returncode}: {result.stderr}")
            raise FFmpegError(
                f"FFmpeg analysis failed with exit code {result.returncode}",
                stderr=result.stderr,
            )

        logger.info("FFmpeg analysis completed successfully")
        return result.stderr

    @staticmethod
    def calculate_progress_percent(
        current_time: float,
        total_duration: float,
    ) -> int:
        """
        진행률 계산.

        Args:
            current_time: 현재 처리된 시간 (초)
            total_duration: 전체 길이 (초)

        Returns:
            진행률 (0-100)
        """
        if total_duration <= 0:
            return 0

        percent = int((current_time / total_duration) * 100)
        return min(percent, 100)
