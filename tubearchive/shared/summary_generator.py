"""출력 영상 요약 및 YouTube 정보 생성기.

병합된 영상의 메타데이터를 수집하여 다양한 포맷의 요약 정보를 생성한다.

제공 기능:
    - **챕터 생성**: 클립별 시작 시점 → YouTube 챕터 타임스탬프 (``HH:MM:SS``)
    - **YouTube 설명문**: 촬영 일시·클립 목록·챕터를 포함한 업로드용 텍스트
    - **Markdown 요약**: 파일 크기·길이·기기별 구성을 포함한 로컬 기록용 문서
    - **단일 파일 설명**: 병합 없이 단독 업로드할 때의 간단 설명문

연속 시퀀스(:class:`~tubearchive.domain.media.grouper.FileSequenceGroup`)를
인식하여, 그룹 내 클립을 하나의 챕터로 묶는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tubearchive.shared.progress import format_size

if TYPE_CHECKING:
    from tubearchive.domain.media.grouper import FileSequenceGroup
    from tubearchive.domain.models.clip import ClipInfo


def format_timestamp(seconds: float) -> str:
    """
    초를 YouTube 타임스탬프 형식으로 변환.

    format_time()과 달리 반올림을 적용한다 (YouTube 챕터용).

    Args:
        seconds: 초 단위 시간

    Returns:
        H:MM:SS 또는 M:SS 형식 문자열
    """
    total_seconds = round(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def extract_topic_from_path(path: Path) -> tuple[str | None, str]:
    """
    경로에서 날짜와 주제 추출.

    디렉토리명이 "YYYY-MM-DD 주제" 또는 "YYYY_MM_DD 주제" 형식인 경우 파싱.

    Args:
        path: 파일 또는 디렉토리 경로

    Returns:
        (날짜 문자열 또는 None, 주제 문자열) 튜플
    """
    # 디렉토리명 추출 (파일인지 확장자로 판단)
    is_file = bool(path.suffix)
    if is_file:
        dir_name = path.parent.name
        if not dir_name or dir_name == ".":
            dir_name = Path.cwd().name
    else:
        dir_name = path.name

    if not dir_name:
        dir_name = Path.cwd().name

    # YYYY-MM-DD 또는 YYYY_MM_DD 패턴 매칭
    date_pattern = r"^(\d{4})[-_](\d{2})[-_](\d{2})\s+(.+)$"
    match = re.match(date_pattern, dir_name)

    if match:
        year, month, day, topic = match.groups()
        date_str = f"{year}-{month}-{day}"
        return date_str, topic.strip()

    # 날짜 패턴이 없으면 디렉토리명 전체를 주제로 사용
    return None, dir_name


def generate_chapters(
    clips: list[tuple[str, float]],
    groups: list[FileSequenceGroup] | None = None,
) -> list[tuple[str, str]]:
    """
    YouTube 챕터 목록 생성.

    Args:
        clips: (파일명, 길이 초) 튜플 리스트
        groups: 그룹 정보 (있으면 연속 파일을 하나의 챕터로 합침)

    Returns:
        (타임스탬프, 제목) 튜플 리스트
    """
    chapters: list[tuple[str, str]] = []
    current_time = 0.0
    aggregated = _aggregate_clips_for_chapters(clips, groups)

    for filename, duration in aggregated:
        # 확장자 제거
        title = Path(filename).stem
        timestamp = format_timestamp(current_time)
        chapters.append((timestamp, title))
        current_time += duration

    return chapters


def _aggregate_clips_for_chapters(
    clips: list[tuple[str, float]],
    groups: list[FileSequenceGroup] | None,
) -> list[tuple[str, float]]:
    """그룹 정보를 바탕으로 챕터용 클립을 병합한다."""
    if not groups:
        return clips

    grouped_files: dict[str, str] = {}
    for group in groups:
        if len(group.files) <= 1:
            continue
        for vf in group.files:
            grouped_files[vf.path.name] = group.group_id

    aggregated: list[tuple[str, float]] = []
    current_group: str | None = None
    current_title: str | None = None
    current_duration = 0.0

    for filename, duration in clips:
        group_id = grouped_files.get(filename)
        if group_id is None:
            if current_group:
                aggregated.append((current_title or filename, current_duration))
                current_group = None
                current_title = None
                current_duration = 0.0
            aggregated.append((filename, duration))
            continue

        if group_id != current_group:
            if current_group:
                aggregated.append((current_title or filename, current_duration))
            current_group = group_id
            current_title = filename
            current_duration = duration
        else:
            current_duration += duration

    if current_group:
        aggregated.append((current_title or "", current_duration))

    return aggregated


@dataclass
class OutputInfo:
    """병합 출력 영상의 메타데이터 (제목, 길이, 크기, 클립 정보 등)."""

    output_path: Path
    title: str
    date: str | None
    total_duration: float
    total_size: int
    clips: list[tuple[str, float]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def formatted_duration(self) -> str:
        """포맷된 총 재생 시간."""
        return format_timestamp(self.total_duration)

    @property
    def formatted_size(self) -> str:
        """포맷된 파일 크기."""
        return format_size(self.total_size)

    @property
    def chapters(self) -> list[tuple[str, str]]:
        """YouTube 챕터 목록."""
        return generate_chapters(self.clips)

    @classmethod
    def from_video_files(
        cls,
        video_files: list[tuple[Path, float]],
        output_path: Path,
    ) -> OutputInfo:
        """
        VideoFile 목록에서 OutputInfo 생성.

        Args:
            video_files: (파일 경로, 길이 초) 튜플 리스트
            output_path: 출력 파일 경로

        Returns:
            OutputInfo 인스턴스
        """
        if not video_files:
            raise ValueError("video_files cannot be empty")

        # 첫 번째 파일 경로에서 주제 추출
        first_path = video_files[0][0]
        date, title = extract_topic_from_path(first_path)

        # 총 길이 계산
        total_duration = sum(duration for _, duration in video_files)

        # 출력 파일 크기 (아직 생성 안됐으면 0)
        total_size = output_path.stat().st_size if output_path.exists() else 0

        # 클립 정보
        clips = [(path.name, duration) for path, duration in video_files]

        return cls(
            output_path=output_path,
            title=title,
            date=date,
            total_duration=total_duration,
            total_size=total_size,
            clips=clips,
        )


def generate_summary_markdown(info: OutputInfo) -> str:
    """
    YouTube/타임라인용 마크다운 요약 생성.

    Args:
        info: OutputInfo 인스턴스

    Returns:
        마크다운 형식 문자열
    """
    lines: list[str] = []

    # 제목
    lines.append(f"# {info.title}")
    lines.append("")

    # 메타데이터
    if info.date:
        lines.append(f"**촬영일**: {info.date}")
    lines.append(f"**총 길이**: {info.formatted_duration}")
    lines.append(f"**파일 크기**: {info.formatted_size}")
    lines.append(f"**파일명**: {info.output_path.name}")
    lines.append("")

    # YouTube 챕터
    lines.append("## YouTube 챕터")
    lines.append("")
    lines.append("```")
    for timestamp, title in info.chapters:
        lines.append(f"{timestamp} {title}")
    lines.append("```")
    lines.append("")

    # 클립 상세 목록
    lines.append("## 클립 목록")
    lines.append("")
    lines.append("| # | 클립명 | 길이 | 시작 시간 |")
    lines.append("|---|--------|------|-----------|")

    current_time = 0.0
    for i, (filename, duration) in enumerate(info.clips, 1):
        clip_name = Path(filename).stem
        duration_str = format_timestamp(duration)
        start_str = format_timestamp(current_time)
        lines.append(f"| {i} | {clip_name} | {duration_str} | {start_str} |")
        current_time += duration

    lines.append("")

    # YouTube 설명 템플릿
    lines.append("## YouTube 설명 템플릿")
    lines.append("")
    lines.append("```")
    if info.date:
        lines.append(f"{info.date}에 촬영한 {info.title} 영상입니다.")
    else:
        lines.append(f"{info.title} 영상입니다.")
    lines.append("")
    lines.append("📍 장소: ")
    lines.append("📷 장비: ")
    lines.append("")
    lines.append("⏱️ 타임라인")
    for timestamp, title in info.chapters:
        lines.append(f"{timestamp} {title}")
    lines.append("")
    lines.append("#vlog #여행 #일상")
    lines.append("```")
    lines.append("")

    # 생성 정보
    lines.append("---")
    lines.append(f"*Generated by TubeArchive at {info.created_at.strftime('%Y-%m-%d %H:%M:%S')}*")

    return "\n".join(lines)


def generate_clip_summary(
    video_clips: list[ClipInfo],
    groups: list[FileSequenceGroup] | None = None,
) -> str:
    """클립 정보 요약 생성 (기종, 촬영시간, 타임스탬프).

    Args:
        video_clips: 클립 메타데이터 리스트
        groups: 시퀀스 그룹 목록 (챕터 집계용)

    Returns:
        마크다운 형식 문자열
    """
    lines: list[str] = []

    # 클립 정보 테이블
    lines.append("## 클립 정보")
    lines.append("")
    lines.append("| # | 파일명 | 기종 | 촬영시간 | 길이 |")
    lines.append("|---|--------|------|----------|------|")

    for i, clip in enumerate(video_clips, 1):
        device_str = clip.device or "-"
        shot_time_str = clip.shot_time or "-"
        duration_str = format_timestamp(clip.duration)
        lines.append(f"| {i} | {clip.name} | {device_str} | {shot_time_str} | {duration_str} |")

    lines.append("")

    # YouTube 챕터 (복사용)
    lines.append("## YouTube 챕터")
    lines.append("")
    lines.append("```")
    current_time = 0.0
    aggregated = _aggregate_clips_for_chapters(
        [(clip.name, clip.duration) for clip in video_clips],
        groups,
    )
    for filename, duration in aggregated:
        timestamp = format_timestamp(current_time)
        clip_name = Path(filename).stem
        lines.append(f"{timestamp} {clip_name}")
        current_time += duration
    lines.append("```")

    return "\n".join(lines)


def generate_youtube_description(
    video_clips: list[ClipInfo],
    groups: list[FileSequenceGroup] | None = None,
) -> str:
    """YouTube 설명용 타임스탬프 생성.

    YouTube 설명에 바로 복사할 수 있는 형식으로, 챕터와 촬영 기기 정보를 포함한다.

    Args:
        video_clips: 클립 메타데이터 리스트
        groups: 시퀀스 그룹 목록 (챕터 집계용)

    Returns:
        타임스탬프 문자열
    """
    lines: list[str] = []

    # 타임스탬프
    current_time = 0.0
    aggregated = _aggregate_clips_for_chapters(
        [(clip.name, clip.duration) for clip in video_clips],
        groups,
    )
    for filename, duration in aggregated:
        timestamp = format_timestamp(current_time)
        clip_name = Path(filename).stem
        lines.append(f"{timestamp} {clip_name}")
        current_time += duration

    # 촬영 기기 정보 (중복 제거, None 제외)
    devices = list(dict.fromkeys(clip.device for clip in video_clips if clip.device))
    if devices:
        lines.append("")
        devices_str = ", ".join(devices)
        lines.append(f"이 영상은 {devices_str}로 촬영됨")

    return "\n".join(lines)


def generate_single_file_description(
    device: str | None = None,
    shot_time: str | None = None,
) -> str:
    """단일 파일용 YouTube 설명을 생성한다.

    촬영 기기와 촬영 시각 정보를 포함한 간결한 설명문을 반환한다.
    ``"Unknown"`` 기기명이나 빈 촬영 시각은 무시된다.

    Args:
        device: 촬영 기기명 (예: ``"Nikon Z6III"``). ``"Unknown"`` 이면 무시.
        shot_time: 촬영 시각 문자열 (``"HH:MM:SS"``). 빈 문자열이면 무시.

    Returns:
        YouTube 설명 문자열. 메타데이터가 없으면 빈 문자열.
    """
    lines: list[str] = []

    if device and device != "Unknown":
        lines.append(f"Filmed with {device}")

    if shot_time:
        lines.append(f"Shot at {shot_time}")

    return "\n".join(lines)


def remap_chapters_for_splits(
    clips: list[tuple[str, float]],
    split_durations: list[float],
    groups: list[FileSequenceGroup] | None = None,
) -> list[list[tuple[str, str]]]:
    """분할 파일별 챕터 목록을 생성한다.

    원본 타임라인의 클립 정보와 각 분할 파일의 실제 길이를 바탕으로,
    분할 파일마다 해당 구간에 속하는 챕터를 재매핑한다.

    클립이 분할 경계를 걸치는 경우, 해당 클립은 양쪽 파트 모두에
    챕터로 포함된다 (후속 파트에서는 0:00부터 시작).

    Args:
        clips: (파일명, 길이 초) 튜플 리스트 (원본 타임라인 순서)
        split_durations: 각 분할 파일의 실제 길이(초) 리스트
        groups: 그룹 정보 (있으면 연속 파일을 하나의 챕터로 합침)

    Returns:
        분할 파일별 [(타임스탬프, 제목)] 리스트의 리스트
    """
    aggregated = _aggregate_clips_for_chapters(clips, groups)

    # 클립 타임라인 구축
    clip_timeline: list[tuple[str, float, float]] = []  # (name, start, end)
    current = 0.0
    for filename, duration in aggregated:
        clip_timeline.append((filename, current, current + duration))
        current += duration

    # 분할 경계 구축
    part_boundaries: list[tuple[float, float]] = []  # (start, end)
    current = 0.0
    for dur in split_durations:
        part_boundaries.append((current, current + dur))
        current += dur

    # 각 파트별 챕터 생성
    result: list[list[tuple[str, str]]] = []
    for part_start, part_end in part_boundaries:
        chapters: list[tuple[str, str]] = []
        for filename, clip_start, clip_end in clip_timeline:
            # 클립이 파트와 겹치는지 확인
            if clip_start < part_end and clip_end > part_start:
                # 파트 내 상대 타임스탬프
                relative_start = max(0.0, clip_start - part_start)
                title = Path(filename).stem
                timestamp = format_timestamp(relative_start)
                chapters.append((timestamp, title))
        result.append(chapters)

    return result


def generate_split_youtube_description(
    video_clips: list[ClipInfo],
    split_durations: list[float],
    part_index: int,
    groups: list[FileSequenceGroup] | None = None,
) -> str:
    """분할 파일 하나에 대한 YouTube 설명을 생성한다.

    Args:
        video_clips: 원본 클립 메타데이터 리스트
        split_durations: 각 분할 파일의 실제 길이(초) 리스트
        part_index: 0-based 파트 인덱스
        groups: 그룹 정보

    Returns:
        해당 파트의 YouTube 설명 문자열 (타임스탬프 + 촬영기기 정보)
    """
    clips_tuples = [(clip.name, clip.duration) for clip in video_clips]
    all_chapters = remap_chapters_for_splits(clips_tuples, split_durations, groups)

    if part_index < 0 or part_index >= len(all_chapters) or not all_chapters[part_index]:
        return ""

    chapters = all_chapters[part_index]
    lines: list[str] = []
    for timestamp, title in chapters:
        lines.append(f"{timestamp} {title}")

    # 촬영 기기 정보 (중복 제거, None 제외)
    devices = list(dict.fromkeys(clip.device for clip in video_clips if clip.device))
    if devices:
        lines.append("")
        devices_str = ", ".join(devices)
        lines.append(f"이 영상은 {devices_str}로 촬영됨")

    return "\n".join(lines)
