"""출력 영상 요약 및 YouTube 정보 생성기."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


def format_timestamp(seconds: float) -> str:
    """
    초를 YouTube 타임스탬프 형식으로 변환.

    Args:
        seconds: 초 단위 시간

    Returns:
        H:MM:SS 또는 M:SS 형식 문자열
    """
    total_seconds = int(round(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60

    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_size(bytes_: int) -> str:
    """
    바이트를 읽기 쉬운 형식으로 변환.

    Args:
        bytes_: 바이트 단위 크기

    Returns:
        KB, MB, GB 등 형식 문자열
    """
    size = float(bytes_)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"


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


def generate_chapters(clips: list[tuple[str, float]]) -> list[tuple[str, str]]:
    """
    YouTube 챕터 목록 생성.

    Args:
        clips: (파일명, 길이 초) 튜플 리스트

    Returns:
        (타임스탬프, 제목) 튜플 리스트
    """
    chapters: list[tuple[str, str]] = []
    current_time = 0.0

    for filename, duration in clips:
        # 확장자 제거
        title = Path(filename).stem
        timestamp = format_timestamp(current_time)
        chapters.append((timestamp, title))
        current_time += duration

    return chapters


@dataclass
class OutputInfo:
    """출력 영상 메타데이터."""

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


def save_summary(info: OutputInfo, output_dir: Path | None = None) -> Path:
    """
    요약 마크다운 파일 저장.

    Args:
        info: OutputInfo 인스턴스
        output_dir: 저장 디렉토리 (None이면 출력 파일과 같은 디렉토리)

    Returns:
        저장된 파일 경로
    """
    if output_dir is None:
        output_dir = info.output_path.parent

    # 파일명 생성 (출력파일명_summary.md)
    summary_filename = f"{info.output_path.stem}_summary.md"
    summary_path = output_dir / summary_filename

    markdown = generate_summary_markdown(info)
    summary_path.write_text(markdown, encoding="utf-8")

    return summary_path
