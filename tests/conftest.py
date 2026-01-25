"""pytest 설정 및 공통 fixture."""

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

# 테스트 DB 영속성 제어 환경 변수
# TUBEARCHIVE_TEST_PERSISTENT=1: 실제 DB 사용 (영속성 유지)
# 미설정 또는 0: 임시 DB 사용 (테스트 후 삭제)
ENV_TEST_PERSISTENT = "TUBEARCHIVE_TEST_PERSISTENT"


@pytest.fixture(scope="session", autouse=True)
def isolate_test_database() -> Generator[Path | None]:
    """
    테스트용 DB 격리.

    TUBEARCHIVE_TEST_PERSISTENT=1 설정 시:
      - 실제 DB 경로 사용 (TUBEARCHIVE_DB_PATH 또는 기본 경로)
      - 테스트 데이터가 영속적으로 유지됨

    미설정 시 (기본):
      - 임시 디렉토리에 테스트 DB 생성
      - 테스트 완료 후 자동 삭제
    """
    use_persistent = os.environ.get(ENV_TEST_PERSISTENT, "0") == "1"

    if use_persistent:
        # 영속 모드: 실제 DB 사용, 환경 변수 변경 없음
        yield None
    else:
        # 격리 모드: 임시 DB 사용
        with tempfile.TemporaryDirectory(prefix="tubearchive_test_") as tmp_dir:
            test_db_path = Path(tmp_dir) / "test_tubearchive.db"

            # 환경 변수 설정
            original_db_path = os.environ.get("TUBEARCHIVE_DB_PATH")
            os.environ["TUBEARCHIVE_DB_PATH"] = str(test_db_path)

            yield test_db_path

            # 환경 변수 복원
            if original_db_path is not None:
                os.environ["TUBEARCHIVE_DB_PATH"] = original_db_path
            else:
                os.environ.pop("TUBEARCHIVE_DB_PATH", None)


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """
    개별 테스트용 임시 DB 경로.

    각 테스트에서 독립적인 DB가 필요할 때 사용합니다.
    """
    return tmp_path / "test.db"


@pytest.fixture
def temp_video_dir(tmp_path: Path) -> Path:
    """
    테스트용 임시 비디오 디렉토리.

    테스트 비디오 파일을 생성할 디렉토리입니다.
    """
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    return video_dir


@pytest.fixture
def temp_output_dir(tmp_path: Path) -> Path:
    """
    테스트용 임시 출력 디렉토리.

    트랜스코딩 및 병합 출력 파일용 디렉토리입니다.
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return output_dir
