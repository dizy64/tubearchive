"""연속 파일 시퀀스 감지 및 그룹핑."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from tubearchive.models.video import FadeConfig, VideoFile

_GOPRO_CHAPTER_PATTERN = re.compile(r"^GH(\d{2})(\d{4})\.\w+$", re.IGNORECASE)
_GOPRO_OLD_FIRST_PATTERN = re.compile(r"^GOPR(\d{4})\.\w+$", re.IGNORECASE)
_GOPRO_OLD_CONT_PATTERN = re.compile(r"^GP(\d{2})(\d{4})\.\w+$", re.IGNORECASE)
_DJI_PATTERN = re.compile(r"^DJI_(\d{14})_(\d{4})_\w\.\w+$", re.IGNORECASE)

_DJI_SPLIT_BOUNDARIES = (4 * 1024**3, 16 * 1024**3)
_DJI_SPLIT_TOLERANCE = 0.05
_DJI_MAX_GAP_SECONDS = 3 * 60 * 60


@dataclass(frozen=True)
class SequenceKey:
    """파일명 기반 시퀀스 키."""

    group_id: str
    order: int


@dataclass(frozen=True)
class FileSequenceGroup:
    """연속 시퀀스 그룹."""

    files: tuple[VideoFile, ...]
    group_id: str


@dataclass(frozen=True)
class _DjiEntry:
    """DJI 파일 처리용 내부 모델."""

    video_file: VideoFile
    sequence: int
    timestamp: datetime | None


@dataclass
class _GroupRange:
    """재정렬 시 그룹 범위 정보."""

    group: FileSequenceGroup
    start: int
    end: int
    pending: list[VideoFile]


def detect_sequence_key(filename: str) -> SequenceKey | None:
    """파일명에서 시퀀스 키 추출."""
    name = Path(filename).name

    match = _GOPRO_CHAPTER_PATTERN.match(name)
    if match:
        chapter, session = match.groups()
        return SequenceKey(group_id=f"gopro_{session}", order=int(chapter))

    match = _GOPRO_OLD_FIRST_PATTERN.match(name)
    if match:
        session = match.group(1)
        return SequenceKey(group_id=f"gopro_{session}", order=0)

    match = _GOPRO_OLD_CONT_PATTERN.match(name)
    if match:
        chapter, session = match.groups()
        return SequenceKey(group_id=f"gopro_{session}", order=int(chapter))

    match = _DJI_PATTERN.match(name)
    if match:
        _timestamp, sequence = match.groups()
        return SequenceKey(group_id="dji", order=int(sequence))

    return None


def _parse_dji_timestamp(filename: str) -> datetime | None:
    """DJI 파일명에서 타임스탬프 추출."""
    match = _DJI_PATTERN.match(Path(filename).name)
    if not match:
        return None
    timestamp_str = match.group(1)
    try:
        return datetime.strptime(timestamp_str, "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _is_split_boundary(size_bytes: int) -> bool:
    """DJI 분할 경계 근처인지 확인."""
    for boundary in _DJI_SPLIT_BOUNDARIES:
        if abs(size_bytes - boundary) <= boundary * _DJI_SPLIT_TOLERANCE:
            return True
    return False


def _is_dji_time_contiguous(prev: _DjiEntry, curr: _DjiEntry) -> bool:
    """DJI 타임스탬프 연속성 확인."""
    if prev.timestamp is None or curr.timestamp is None:
        return True
    gap = (curr.timestamp - prev.timestamp).total_seconds()
    if gap < 0:
        return False
    return gap <= _DJI_MAX_GAP_SECONDS


def group_sequences(files: list[VideoFile]) -> list[FileSequenceGroup]:
    """파일 목록에서 같은 세션의 분할 파일을 그룹으로 묶는다."""
    if not files:
        return []

    index_map = {vf.path: idx for idx, vf in enumerate(files)}

    gopro_groups: dict[str, list[tuple[int, int, VideoFile]]] = {}
    dji_entries: list[_DjiEntry] = []
    standalone_files: list[VideoFile] = []

    for vf in files:
        key = detect_sequence_key(vf.path.name)
        if key and key.group_id.startswith("gopro_"):
            gopro_groups.setdefault(key.group_id, []).append((key.order, index_map[vf.path], vf))
        elif key and key.group_id == "dji":
            dji_entries.append(
                _DjiEntry(
                    video_file=vf,
                    sequence=key.order,
                    timestamp=_parse_dji_timestamp(vf.path.name),
                )
            )
        else:
            standalone_files.append(vf)

    groups: list[FileSequenceGroup] = []

    # GoPro 그룹
    for group_id, entries in gopro_groups.items():
        entries.sort(key=lambda item: (item[0], item[1]))
        files_sorted = tuple(vf for _, _, vf in entries)
        groups.append(FileSequenceGroup(files=files_sorted, group_id=group_id))

    # DJI 그룹 (연속 순번 + 분할 경계 + 타임스탬프 연속성)
    if dji_entries:
        dji_entries.sort(
            key=lambda entry: (
                entry.timestamp or entry.video_file.creation_time,
                entry.sequence,
                index_map[entry.video_file.path],
            )
        )

        current: list[_DjiEntry] = []
        group_index = 1
        for entry in dji_entries:
            if not current:
                current = [entry]
                continue

            prev = current[-1]
            should_group = (
                entry.sequence == prev.sequence + 1
                and _is_split_boundary(prev.video_file.size_bytes)
                and _is_dji_time_contiguous(prev, entry)
            )
            if should_group:
                current.append(entry)
            else:
                groups.append(_finalize_dji_group(current, group_index))
                group_index += 1
                current = [entry]

        if current:
            groups.append(_finalize_dji_group(current, group_index))

    # Standalone 그룹
    for idx, vf in enumerate(standalone_files, 1):
        groups.append(FileSequenceGroup(files=(vf,), group_id=f"s_{idx}"))

    # 첫 등장 순으로 정렬
    groups.sort(key=lambda g: min(index_map[f.path] for f in g.files))
    return groups


def _finalize_dji_group(entries: list[_DjiEntry], group_index: int) -> FileSequenceGroup:
    """DJI 그룹 확정."""
    first_timestamp = entries[0].timestamp
    if first_timestamp:
        group_id = f"dji_{first_timestamp.strftime('%Y%m%d%H%M%S')}_{group_index}"
    else:
        group_id = f"dji_{group_index}"

    files_sorted = tuple(entry.video_file for entry in entries)
    return FileSequenceGroup(files=files_sorted, group_id=group_id)


def reorder_with_groups(files: list[VideoFile], groups: list[FileSequenceGroup]) -> list[VideoFile]:
    """그룹 파일을 연속 배치하고, 끼어든 파일은 그룹 뒤로 이동."""
    if not files or not groups:
        return files

    index_map = {vf.path: idx for idx, vf in enumerate(files)}
    multi_groups = [group for group in groups if len(group.files) > 1]
    if not multi_groups:
        return files

    group_files_map = {group.group_id: {vf.path for vf in group.files} for group in multi_groups}
    group_info: dict[str, _GroupRange] = {}
    index_to_group: dict[int, str] = {}

    for group in multi_groups:
        indices = [index_map[vf.path] for vf in group.files if vf.path in index_map]
        if not indices:
            continue
        start = min(indices)
        end = max(indices)
        group_info[group.group_id] = _GroupRange(
            group=group,
            start=start,
            end=end,
            pending=[],
        )
        for idx in range(start, end + 1):
            index_to_group.setdefault(idx, group.group_id)

    result: list[VideoFile] = []
    emitted_groups: set[str] = set()

    for idx, vf in enumerate(files):
        group_id = index_to_group.get(idx)
        if not group_id:
            result.append(vf)
            continue

        info = group_info[group_id]
        group = info.group
        start = info.start
        end = info.end
        pending = info.pending
        group_files = group_files_map[group_id]

        if vf.path in group_files:
            if group_id not in emitted_groups and idx == start:
                result.extend(group.files)
                emitted_groups.add(group_id)
        else:
            pending.append(vf)

        if idx == end and pending:
            result.extend(pending)

    return result


def compute_fade_map(
    groups: list[FileSequenceGroup],
    default_fade: float = 0.5,
) -> dict[Path, FadeConfig]:
    """그룹 기반 fade 설정 맵 계산."""
    fade_map: dict[Path, FadeConfig] = {}
    normalized = max(default_fade, 0.0)

    for group in groups:
        if not group.files:
            continue
        if len(group.files) == 1:
            vf = group.files[0]
            fade_map[vf.path] = FadeConfig(fade_in=normalized, fade_out=normalized)
            continue

        for idx, vf in enumerate(group.files):
            if idx == 0:
                fade_map[vf.path] = FadeConfig(fade_in=normalized, fade_out=0.0)
            elif idx == len(group.files) - 1:
                fade_map[vf.path] = FadeConfig(fade_in=0.0, fade_out=normalized)
            else:
                fade_map[vf.path] = FadeConfig(fade_in=0.0, fade_out=0.0)

    return fade_map
