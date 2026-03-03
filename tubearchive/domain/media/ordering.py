"""클립 순서 편집 모듈.

스캔된 영상 파일 목록에 대해 필터링·정렬·수동 재정렬 기능을 제공한다.

- **필터링**: ``--exclude`` / ``--include-only`` 글로브 패턴 기반
- **정렬**: ``--sort {time,name,size,device}`` 기준 변경
- **재정렬**: ``--reorder`` 인터랙티브 모드로 수동 순서 편집
"""

from __future__ import annotations

import fnmatch
import logging
import sys
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from tubearchive.domain.models.video import VideoFile

logger = logging.getLogger(__name__)


class SortKey(Enum):
    """클립 정렬 기준.

    Attributes:
        TIME: 파일 생성 시간 (기본값)
        NAME: 파일명 알파벳순
        SIZE: 파일 크기
        DEVICE: 촬영 기기명 (ffprobe 메타데이터 필요)
    """

    TIME = "time"
    NAME = "name"
    SIZE = "size"
    DEVICE = "device"


def filter_videos(
    videos: list[VideoFile],
    *,
    exclude_patterns: list[str] | None = None,
    include_only_patterns: list[str] | None = None,
) -> list[VideoFile]:
    """글로브 패턴으로 영상 파일 목록을 필터링한다.

    ``--exclude`` 와 ``--include-only`` 가 동시에 지정되면
    include-only를 먼저 적용한 뒤 exclude를 적용한다.

    Args:
        videos: 원본 영상 파일 리스트
        exclude_patterns: 제외할 파일명 글로브 패턴 (예: ``["GH*", "*.mts"]``)
        include_only_patterns: 포함할 파일명 글로브 패턴 (미매칭은 제외)

    Returns:
        필터링된 영상 파일 리스트 (원본 순서 유지)
    """
    if not videos:
        return []

    result = list(videos)

    if include_only_patterns:
        result = [v for v in result if _matches_any_pattern(v.path.name, include_only_patterns)]

    if exclude_patterns:
        result = [v for v in result if not _matches_any_pattern(v.path.name, exclude_patterns)]

    excluded_count = len(videos) - len(result)
    if excluded_count > 0:
        logger.info("필터 적용: %d/%d개 파일 제외됨", excluded_count, len(videos))

    return result


def sort_videos(
    videos: list[VideoFile],
    sort_key: SortKey = SortKey.TIME,
    *,
    reverse: bool = False,
    device_detector: Callable[[Path], str | None] | None = None,
) -> list[VideoFile]:
    """정렬 기준에 따라 영상 파일 목록을 정렬한다.

    Args:
        videos: 정렬 대상 영상 파일 리스트
        sort_key: 정렬 기준 (time/name/size/device)
        reverse: 역순 정렬 여부
        device_detector: device 정렬 시 기기명 추출 함수
            ``(Path) -> str | None``. 미지정 시 ``detect_metadata`` 사용.

    Returns:
        정렬된 영상 파일 리스트 (새 리스트)
    """
    if not videos:
        return []

    if sort_key == SortKey.TIME:
        return sorted(videos, key=lambda v: v.creation_time, reverse=reverse)
    elif sort_key == SortKey.NAME:
        return sorted(videos, key=lambda v: v.path.name.lower(), reverse=reverse)
    elif sort_key == SortKey.SIZE:
        return sorted(videos, key=lambda v: v.size_bytes, reverse=reverse)
    elif sort_key == SortKey.DEVICE:
        return _sort_by_device(videos, reverse=reverse, detector=device_detector)
    else:
        logger.warning("알 수 없는 정렬 기준: %s, 기본값(time) 사용", sort_key)
        return sorted(videos, key=lambda v: v.creation_time, reverse=reverse)


def print_video_list(
    videos: list[VideoFile],
    *,
    header: str = "클립 목록",
) -> None:
    """영상 파일 목록을 번호와 함께 콘솔에 출력한다.

    Args:
        videos: 출력할 영상 파일 리스트
        header: 상단 헤더 텍스트
    """
    print(f"\n📋 {header} ({len(videos)}개)")
    print("-" * 70)
    print(f"{'번호':<5} {'파일명':<40} {'크기':<12} {'생성 시간'}")
    print("-" * 70)

    for i, v in enumerate(videos, 1):
        name = v.path.name
        if len(name) > 38:
            name = name[:35] + "..."
        size_mb = v.size_bytes / (1024 * 1024)
        time_str = v.creation_time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"{i:<5} {name:<40} {size_mb:>8.1f} MB  {time_str}")

    print("-" * 70)


def interactive_reorder(
    videos: list[VideoFile],
    *,
    input_fn: Callable[[str], str] | None = None,
) -> list[VideoFile]:
    """인터랙티브 모드로 클립 순서를 수동 편집한다.

    현재 순서를 보여주고 사용자 입력으로 순서를 변경한다.

    명령어:
        - 번호 나열 (예: ``3,1,2,4``): 전체 순서 재지정
        - ``swap 1 3``: 1번과 3번 위치 교환
        - ``move 3 1``: 3번을 1번 위치로 이동
        - ``remove 2``: 2번 제거
        - ``done`` 또는 빈 입력: 편집 완료

    Args:
        videos: 편집 대상 영상 파일 리스트
        input_fn: 사용자 입력 함수 (테스트용 주입 가능)

    Returns:
        재정렬된 영상 파일 리스트
    """
    if not videos:
        return []

    if input_fn is None:
        input_fn = _default_input

    current = list(videos)

    print_video_list(current, header="현재 클립 순서")
    print("\n💡 명령어:")
    print("  숫자 나열  : 전체 순서 재지정 (예: 3,1,2,4)")
    print("  swap A B   : A번과 B번 위치 교환")
    print("  move A B   : A번을 B번 위치로 이동")
    print("  remove N   : N번 제거")
    print("  done/엔터  : 편집 완료\n")

    while True:
        try:
            user_input = input_fn("순서 편집> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n편집 취소, 원본 순서 유지")
            return list(videos)

        if not user_input or user_input.lower() == "done":
            break

        result = _process_reorder_command(current, user_input)
        if result is not None:
            current = result
            print_video_list(current, header="변경된 순서")

    return current


def _matches_any_pattern(filename: str, patterns: list[str]) -> bool:
    """파일명이 글로브 패턴 중 하나와 매칭되는지 확인."""
    return any(
        fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(filename.lower(), pattern.lower())
        for pattern in patterns
    )


def _sort_by_device(
    videos: list[VideoFile],
    *,
    reverse: bool = False,
    detector: Callable[[Path], str | None] | None = None,
) -> list[VideoFile]:
    """기기명으로 정렬 (ffprobe 메타데이터 사용)."""
    if detector is None:
        from tubearchive.domain.media.detector import detect_metadata

        def _default_detector(path: Path) -> str | None:
            try:
                meta = detect_metadata(path)
                return meta.device_model
            except Exception:
                return None

        detector = _default_detector

    device_cache: dict[Path, str] = {}
    for v in videos:
        device = detector(v.path)
        device_cache[v.path] = device or ""

    return sorted(
        videos,
        key=lambda v: (device_cache.get(v.path, ""), v.creation_time),
        reverse=reverse,
    )


def _default_input(prompt: str) -> str:
    """기본 사용자 입력 함수."""
    sys.stdout.write(prompt)
    sys.stdout.flush()

    try:
        import subprocess

        result = subprocess.run(
            ["bash", "-c", 'read -r line </dev/tty && printf "%s" "$line"'],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    return input().strip()


def _process_reorder_command(
    current: list[VideoFile],
    command: str,
) -> list[VideoFile] | None:
    """재정렬 명령어를 파싱하고 실행한다.

    Args:
        current: 현재 영상 파일 리스트
        command: 사용자 입력 명령어

    Returns:
        변경된 리스트 (성공 시) 또는 None (실패 시)
    """
    parts = command.strip().split()

    if not parts:
        return None

    if parts[0].lower() == "swap" and len(parts) == 3:
        return _cmd_swap(current, parts[1], parts[2])

    if parts[0].lower() == "move" and len(parts) == 3:
        return _cmd_move(current, parts[1], parts[2])

    if parts[0].lower() == "remove" and len(parts) == 2:
        return _cmd_remove(current, parts[1])

    return _cmd_reorder_by_numbers(current, command)


def _cmd_swap(
    current: list[VideoFile],
    a_str: str,
    b_str: str,
) -> list[VideoFile] | None:
    """두 위치의 클립을 교환."""
    try:
        a = int(a_str) - 1
        b = int(b_str) - 1
    except ValueError:
        print("  ⚠️ 숫자를 입력해주세요.")
        return None

    if not (0 <= a < len(current) and 0 <= b < len(current)):
        print(f"  ⚠️ 범위 초과: 1~{len(current)} 사이 값을 입력하세요.")
        return None

    result = list(current)
    result[a], result[b] = result[b], result[a]
    print(f"  ✅ {a + 1}번 ↔ {b + 1}번 교환")
    return result


def _cmd_move(
    current: list[VideoFile],
    from_str: str,
    to_str: str,
) -> list[VideoFile] | None:
    """클립을 지정 위치로 이동."""
    try:
        from_idx = int(from_str) - 1
        to_idx = int(to_str) - 1
    except ValueError:
        print("  ⚠️ 숫자를 입력해주세요.")
        return None

    if not (0 <= from_idx < len(current)):
        print(f"  ⚠️ 이동 대상 범위 초과: 1~{len(current)}")
        return None
    if not (0 <= to_idx < len(current)):
        print(f"  ⚠️ 목적지 범위 초과: 1~{len(current)}")
        return None

    result = list(current)
    item = result.pop(from_idx)
    result.insert(to_idx, item)
    print(f"  ✅ {from_idx + 1}번 → {to_idx + 1}번 위치로 이동")
    return result


def _cmd_remove(
    current: list[VideoFile],
    idx_str: str,
) -> list[VideoFile] | None:
    """클립을 목록에서 제거."""
    try:
        idx = int(idx_str) - 1
    except ValueError:
        print("  ⚠️ 숫자를 입력해주세요.")
        return None

    if not (0 <= idx < len(current)):
        print(f"  ⚠️ 범위 초과: 1~{len(current)}")
        return None

    result = list(current)
    removed = result.pop(idx)
    print(f"  ✅ 제거됨: {removed.path.name}")

    if not result:
        print("  ⚠️ 모든 파일이 제거되었습니다. 최소 1개 파일이 필요합니다.")
        return None

    return result


def _cmd_reorder_by_numbers(
    current: list[VideoFile],
    command: str,
) -> list[VideoFile] | None:
    """쉼표 또는 공백으로 구분된 번호로 전체 순서를 재지정."""
    raw = command.replace(",", " ").split()

    try:
        indices = [int(x) - 1 for x in raw]
    except ValueError:
        print("  ⚠️ 숫자만 입력해주세요. (예: 3,1,2,4)")
        return None

    for idx in indices:
        if not (0 <= idx < len(current)):
            print(f"  ⚠️ 범위 초과: {idx + 1} (유효 범위: 1~{len(current)})")
            return None

    if len(set(indices)) != len(indices):
        print("  ⚠️ 중복된 번호가 있습니다.")
        return None

    if len(indices) == len(current):
        result = [current[i] for i in indices]
        print("  ✅ 전체 순서 재지정 완료")
        return result
    else:
        print(f"  ⚠️ 모든 파일 번호를 지정하세요. ({len(indices)}개 입력 / {len(current)}개 필요)")
        return None
