"""core/archiver.py 단위 테스트."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tubearchive.core.archiver import ArchivePolicy, Archiver, ArchiveStats


@pytest.fixture
def mock_repo():
    """Mock ArchiveHistoryRepository."""
    return MagicMock()


@pytest.fixture
def temp_files(tmp_path: Path):
    """테스트용 임시 파일 생성."""
    files = []
    for i in range(3):
        file_path = tmp_path / f"video_{i}.mp4"
        file_path.write_text(f"fake video content {i}")
        files.append(file_path)
    return files


# --- 초기화 테스트 ---


def test_archiver_init_keep_policy(mock_repo):
    """KEEP 정책으로 Archiver 초기화."""
    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.KEEP,
    )
    assert archiver.policy == ArchivePolicy.KEEP
    assert archiver.destination is None


def test_archiver_init_move_policy_requires_destination(mock_repo):
    """MOVE 정책은 destination 필수."""
    with pytest.raises(ValueError, match="destination 경로를 지정"):
        Archiver(
            repo=mock_repo,
            policy=ArchivePolicy.MOVE,
            destination=None,
        )


def test_archiver_init_move_policy_with_destination(mock_repo, tmp_path: Path):
    """MOVE 정책 + destination 정상 초기화."""
    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.MOVE,
        destination=tmp_path / "archive",
    )
    assert archiver.policy == ArchivePolicy.MOVE
    assert archiver.destination == tmp_path / "archive"


# --- archive_files 정책별 동작 테스트 ---


def test_archive_files_keep_policy(mock_repo, temp_files: list[Path]):
    """KEEP 정책: 파일 유지, 아무것도 안 함."""
    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.KEEP,
    )

    video_paths = [(i + 1, path) for i, path in enumerate(temp_files)]
    stats = archiver.archive_files(video_paths)

    assert isinstance(stats, ArchiveStats)
    assert stats.kept == 3
    assert stats.moved == 0
    assert stats.deleted == 0
    assert stats.failed == 0

    # 모든 파일이 여전히 존재
    for path in temp_files:
        assert path.exists()

    # Repository 호출 없음
    mock_repo.insert_history.assert_not_called()


def test_archive_files_move_policy(mock_repo, temp_files: list[Path], tmp_path: Path):
    """MOVE 정책: 파일 이동."""
    destination = tmp_path / "archive"
    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.MOVE,
        destination=destination,
    )

    video_paths = [(i + 1, path) for i, path in enumerate(temp_files)]
    stats = archiver.archive_files(video_paths)

    assert stats.moved == 3
    assert stats.deleted == 0
    assert stats.failed == 0

    # 원본 파일 삭제됨
    for path in temp_files:
        assert not path.exists()

    # destination에 파일 존재
    for path in temp_files:
        moved_path = destination / path.name
        assert moved_path.exists()

    # Repository에 이력 기록됨
    assert mock_repo.insert_history.call_count == 3


def test_archive_files_delete_policy(mock_repo, temp_files: list[Path]):
    """DELETE 정책: 파일 삭제 (확인 프롬프트는 CLI 계층에서 처리)."""
    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.DELETE,
    )

    video_paths = [(i + 1, path) for i, path in enumerate(temp_files)]
    stats = archiver.archive_files(video_paths)

    assert stats.deleted == 3
    assert stats.failed == 0

    # 모든 파일 삭제됨
    for path in temp_files:
        assert not path.exists()

    # Repository에 이력 기록됨
    assert mock_repo.insert_history.call_count == 3


# --- _move_file 테스트 ---


def test_move_file_creates_destination_dir(mock_repo, tmp_path: Path):
    """이동 시 destination 디렉토리 자동 생성."""
    source = tmp_path / "source" / "video.mp4"
    source.parent.mkdir()
    source.write_text("content")

    destination_dir = tmp_path / "archive" / "subdir"
    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.MOVE,
        destination=destination_dir,
    )

    result = archiver._move_file(source)

    assert destination_dir.exists()
    assert result.exists()
    assert result == destination_dir / "video.mp4"


def test_move_file_handles_name_collision(mock_repo, tmp_path: Path):
    """동일 파일명 충돌 시 번호 추가."""
    source1 = tmp_path / "source1" / "video.mp4"
    source1.parent.mkdir()
    source1.write_text("content1")

    source2 = tmp_path / "source2" / "video.mp4"
    source2.parent.mkdir()
    source2.write_text("content2")

    destination_dir = tmp_path / "archive"
    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.MOVE,
        destination=destination_dir,
    )

    # 첫 번째 이동
    result1 = archiver._move_file(source1)
    assert result1 == destination_dir / "video.mp4"

    # 두 번째 이동 (동일 파일명 → 충돌 처리)
    result2 = archiver._move_file(source2)
    assert result2 == destination_dir / "video_1.mp4"


# --- Repository 이력 기록 테스트 ---


def test_record_history_called_on_move(mock_repo, tmp_path: Path):
    """MOVE 작업 시 Repository를 통해 이력 기록."""
    source = tmp_path / "video.mp4"
    source.write_text("content")

    destination_dir = tmp_path / "archive"
    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.MOVE,
        destination=destination_dir,
    )

    archiver.archive_files([(1, source)])

    mock_repo.insert_history.assert_called_once()
    call_args = mock_repo.insert_history.call_args
    assert call_args[0][0] == 1  # video_id
    assert call_args[0][1] == "move"  # operation


def test_record_history_called_on_delete(mock_repo, tmp_path: Path):
    """DELETE 작업 시 Repository를 통해 이력 기록."""
    source = tmp_path / "video.mp4"
    source.write_text("content")

    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.DELETE,
    )

    archiver.archive_files([(1, source)])

    mock_repo.insert_history.assert_called_once()
    call_args = mock_repo.insert_history.call_args
    assert call_args[0][0] == 1  # video_id
    assert call_args[0][1] == "delete"  # operation
    assert call_args[0][3] is None  # destination_path


# --- 에지 케이스 ---


def test_archive_files_handles_missing_file(mock_repo, tmp_path: Path):
    """존재하지 않는 파일 삭제 시도: 경고만 출력, 삭제 카운트 미반영."""
    non_existent = tmp_path / "non_existent.mp4"

    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.DELETE,
    )

    stats = archiver.archive_files([(1, non_existent)])

    # 존재하지 않는 파일은 삭제 카운트에서 제외
    assert stats.deleted == 0
    assert stats.failed == 0

    # DB 이력도 기록하지 않음
    mock_repo.insert_history.assert_not_called()


def test_archive_files_empty_list(mock_repo):
    """빈 리스트: 아무 작업 없이 빈 통계 반환."""
    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.DELETE,
    )

    stats = archiver.archive_files([])

    assert stats.deleted == 0
    assert stats.failed == 0
    mock_repo.insert_history.assert_not_called()


def test_archive_files_move_failure_increments_failed(mock_repo, tmp_path: Path):
    """MOVE 실패 시 failed 카운트 증가."""
    # 존재하지 않는 파일 이동 시도
    non_existent = tmp_path / "non_existent.mp4"

    destination_dir = tmp_path / "archive"
    archiver = Archiver(
        repo=mock_repo,
        policy=ArchivePolicy.MOVE,
        destination=destination_dir,
    )

    stats = archiver.archive_files([(1, non_existent)])

    assert stats.moved == 0
    assert stats.failed == 1
    mock_repo.insert_history.assert_not_called()
