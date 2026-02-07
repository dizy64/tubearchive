"""core/archiver.py 단위 테스트."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tubearchive.core.archiver import ArchivePolicy, Archiver


@pytest.fixture
def mock_conn():
    """Mock SQLite 연결 객체."""
    conn = MagicMock()
    conn.execute.return_value = MagicMock(lastrowid=1)
    return conn


@pytest.fixture
def temp_files(tmp_path: Path):
    """테스트용 임시 파일 생성."""
    files = []
    for i in range(3):
        file_path = tmp_path / f"video_{i}.mp4"
        file_path.write_text(f"fake video content {i}")
        files.append(file_path)
    return files


def test_archiver_init_keep_policy(mock_conn):
    """KEEP 정책으로 Archiver 초기화."""
    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.KEEP,
    )
    assert archiver.policy == ArchivePolicy.KEEP
    assert archiver.destination is None
    assert archiver.force is False


def test_archiver_init_move_policy_requires_destination(mock_conn):
    """MOVE 정책은 destination 필수."""
    with pytest.raises(ValueError, match="destination 경로를 지정"):
        Archiver(
            conn=mock_conn,
            policy=ArchivePolicy.MOVE,
            destination=None,
        )


def test_archiver_init_move_policy_with_destination(mock_conn, tmp_path: Path):
    """MOVE 정책 + destination 정상 초기화."""
    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.MOVE,
        destination=tmp_path / "archive",
    )
    assert archiver.policy == ArchivePolicy.MOVE
    assert archiver.destination == tmp_path / "archive"


def test_archive_files_keep_policy(mock_conn, temp_files: list[Path]):
    """KEEP 정책: 파일 유지, 아무것도 안 함."""
    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.KEEP,
    )

    video_paths = [(i + 1, path) for i, path in enumerate(temp_files)]
    stats = archiver.archive_files(video_paths)

    assert stats["kept"] == 3
    assert stats["moved"] == 0
    assert stats["deleted"] == 0
    assert stats["failed"] == 0

    # 모든 파일이 여전히 존재
    for path in temp_files:
        assert path.exists()


def test_archive_files_move_policy(mock_conn, temp_files: list[Path], tmp_path: Path):
    """MOVE 정책: 파일 이동."""
    destination = tmp_path / "archive"
    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.MOVE,
        destination=destination,
    )

    video_paths = [(i + 1, path) for i, path in enumerate(temp_files)]
    stats = archiver.archive_files(video_paths)

    assert stats["moved"] == 3
    assert stats["deleted"] == 0
    assert stats["failed"] == 0

    # 원본 파일 삭제됨
    for path in temp_files:
        assert not path.exists()

    # destination에 파일 존재
    for path in temp_files:
        moved_path = destination / path.name
        assert moved_path.exists()


def test_archive_files_delete_policy_without_force(mock_conn, temp_files: list[Path], monkeypatch):
    """DELETE 정책: force 없이 확인 프롬프트 거부."""
    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.DELETE,
        force=False,
    )

    # 사용자 입력 'n' (거부)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    video_paths = [(i + 1, path) for i, path in enumerate(temp_files)]
    stats = archiver.archive_files(video_paths)

    assert stats["kept"] == 3
    assert stats["deleted"] == 0

    # 모든 파일 유지
    for path in temp_files:
        assert path.exists()


def test_archive_files_delete_policy_with_force(mock_conn, temp_files: list[Path]):
    """DELETE 정책: force 플래그로 확인 우회."""
    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.DELETE,
        force=True,
    )

    video_paths = [(i + 1, path) for i, path in enumerate(temp_files)]
    stats = archiver.archive_files(video_paths)

    assert stats["deleted"] == 3
    assert stats["failed"] == 0

    # 모든 파일 삭제됨
    for path in temp_files:
        assert not path.exists()


def test_archive_files_delete_policy_with_confirmation(
    mock_conn, temp_files: list[Path], monkeypatch
):
    """DELETE 정책: 확인 프롬프트 승인."""
    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.DELETE,
        force=False,
    )

    # 사용자 입력 'y' (승인)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    video_paths = [(i + 1, path) for i, path in enumerate(temp_files)]
    stats = archiver.archive_files(video_paths)

    assert stats["deleted"] == 3
    assert stats["failed"] == 0

    # 모든 파일 삭제됨
    for path in temp_files:
        assert not path.exists()


def test_move_file_creates_destination_dir(mock_conn, tmp_path: Path):
    """이동 시 destination 디렉토리 자동 생성."""
    source = tmp_path / "source" / "video.mp4"
    source.parent.mkdir()
    source.write_text("content")

    destination_dir = tmp_path / "archive" / "subdir"
    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.MOVE,
        destination=destination_dir,
    )

    result = archiver._move_file(source)

    assert destination_dir.exists()
    assert result.exists()
    assert result == destination_dir / "video.mp4"


def test_move_file_handles_name_collision(mock_conn, tmp_path: Path):
    """동일 파일명 충돌 시 번호 추가."""
    source1 = tmp_path / "source" / "video.mp4"
    source1.parent.mkdir()
    source1.write_text("content1")

    source2 = tmp_path / "source" / "video.mp4"
    source2.write_text("content2")

    destination_dir = tmp_path / "archive"
    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.MOVE,
        destination=destination_dir,
    )

    # 첫 번째 이동
    result1 = archiver._move_file(source1)
    assert result1 == destination_dir / "video.mp4"

    # 두 번째 이동 (동일 이름)
    source2.write_text("content2")  # 다시 생성
    result2 = archiver._move_file(source2)
    assert result2 == destination_dir / "video_1.mp4"


def test_record_history_called_on_move(mock_conn, tmp_path: Path):
    """MOVE 작업 시 DB 이력 기록 호출."""
    source = tmp_path / "video.mp4"
    source.write_text("content")

    destination_dir = tmp_path / "archive"
    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.MOVE,
        destination=destination_dir,
    )

    archiver.archive_files([(1, source)])

    # execute 호출 확인
    mock_conn.execute.assert_called()
    call_args = mock_conn.execute.call_args[0]
    assert "INSERT INTO archive_history" in call_args[0]


def test_record_history_called_on_delete(mock_conn, tmp_path: Path):
    """DELETE 작업 시 DB 이력 기록 호출."""
    source = tmp_path / "video.mp4"
    source.write_text("content")

    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.DELETE,
        force=True,
    )

    archiver.archive_files([(1, source)])

    # execute 호출 확인
    mock_conn.execute.assert_called()
    call_args = mock_conn.execute.call_args[0]
    assert "INSERT INTO archive_history" in call_args[0]


def test_archive_files_handles_missing_file(mock_conn, tmp_path: Path):
    """존재하지 않는 파일 삭제 시도: 경고만 출력, 실패 안 함."""
    non_existent = tmp_path / "non_existent.mp4"

    archiver = Archiver(
        conn=mock_conn,
        policy=ArchivePolicy.DELETE,
        force=True,
    )

    stats = archiver.archive_files([(1, non_existent)])

    # 실패가 아니라 성공으로 처리 (존재하지 않으면 경고만)
    assert stats["deleted"] == 1
    assert stats["failed"] == 0
