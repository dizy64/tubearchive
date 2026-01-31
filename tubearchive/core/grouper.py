"""연속 파일 시퀀스 감지 및 그룹핑.

GoPro·DJI 등의 카메라가 파일 크기 제한(4GB FAT32 등)으로 인해
하나의 촬영을 여러 파일로 분할 저장하는 경우, 이를 자동으로 감지하여
원래의 연속 촬영 단위로 그룹핑한다.

지원 패턴:
    - **GoPro**: ``GH{chapter}{id}`` / ``GOPR{id}`` + ``GP{chapter}{id}``
    - **DJI**: ``DJI_{timestamp}_{seq}_D`` (타임스탬프·파일 크기 기반)

그룹 내 클립 경계에서는 페이드 없이 이어붙이고,
그룹 간 경계에서는 Dip-to-Black 페이드를 적용한다.
"""

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
    """파일명에서 추출한 시퀀스 식별 키.

    같은 ``group_id`` 를 가진 파일들은 하나의 촬영 세션에서 분할된 것이며,
    ``order`` 로 원래 촬영 순서를 복원한다.

    Attributes:
        group_id: 촬영 세션 식별자 (예: ``"gopro_0042"``, ``"dji_20240101120000"``)
        order: 그룹 내 순서 (0-based, 챕터 또는 시퀀스 번호)
    """

    group_id: str
    order: int


@dataclass(frozen=True)
class FileSequenceGroup:
    """연속 시퀀스 그룹 (같은 촬영 세션의 분할 파일 묶음).

    페이드 계산 시 그룹 내 파일 경계에서는 페이드를 생략하고,
    그룹 간 경계에서만 Dip-to-Black을 적용한다.

    Attributes:
        files: 촬영 순서대로 정렬된 분할 파일 튜플
        group_id: 그룹 식별자 (``SequenceKey.group_id`` 와 동일)
    """

    files: tuple[VideoFile, ...]
    group_id: str


@dataclass(frozen=True)
class _GoProEntry:
    """GoPro 파일 분석용 내부 모델.

    Attributes:
        chapter_order: GoPro 챕터 번호 (``GH01`` → 1, ``GOPR`` 첫 파일 → 0)
        original_index: 입력 리스트에서의 원래 인덱스 (순서 복원용)
        video_file: 원본 VideoFile 참조
    """

    chapter_order: int
    original_index: int
    video_file: VideoFile


@dataclass(frozen=True)
class _DjiEntry:
    """DJI 파일 분석용 내부 모델.

    Attributes:
        video_file: 원본 VideoFile 참조
        sequence: DJI 파일명의 시퀀스 번호 (``_0001_`` → 1)
        timestamp: 파일명에서 파싱한 촬영 시각 (파싱 실패 시 None)
    """

    video_file: VideoFile
    sequence: int
    timestamp: datetime | None


@dataclass
class _GroupRange:
    """재정렬 시 그룹의 원본 위치 범위 및 보류 파일.

    ``reorder_with_groups()`` 에서 그룹 파일이 원본 리스트에서
    차지하는 범위(start~end)를 추적하고, 그 사이에 끼어든
    비-그룹 파일을 ``pending`` 에 모아 그룹 뒤에 배치한다.

    Attributes:
        group: 대상 시퀀스 그룹
        start: 그룹 첫 파일의 원본 인덱스
        end: 그룹 마지막 파일의 원본 인덱스
        pending: 그룹 범위 내에 끼어든 비-그룹 파일 목록
    """

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

    gopro_groups: dict[str, list[_GoProEntry]] = {}
    dji_entries: list[_DjiEntry] = []
    standalone_files: list[VideoFile] = []

    for vf in files:
        key = detect_sequence_key(vf.path.name)
        if key and key.group_id.startswith("gopro_"):
            gopro_entry = _GoProEntry(
                chapter_order=key.order,
                original_index=index_map[vf.path],
                video_file=vf,
            )
            gopro_groups.setdefault(key.group_id, []).append(gopro_entry)
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
    for group_id, gopro_entries in gopro_groups.items():
        gopro_entries.sort(key=lambda e: (e.chapter_order, e.original_index))
        files_sorted = tuple(e.video_file for e in gopro_entries)
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
            # DJI 연속 그룹핑 조건 3가지:
            # 1) 시퀀스 번호가 연속 (ex. 0001 → 0002)
            # 2) 이전 파일 크기가 FAT32/exFAT 분할 경계(4GB/16GB) 근처
            # 3) 타임스탬프 간격이 _DJI_MAX_GAP_SECONDS(3시간) 이내
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
