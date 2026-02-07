"""트랜스코딩 및 병합 작업 도메인 모델.

SQLite에 저장되는 작업 상태·이력을 표현하는 데이터클래스 정의.

클래스:
    - :class:`JobStatus`: 작업 상태 열거형 (pending → processing → completed/failed → merged)
    - :class:`TranscodingJob`: 개별 영상의 트랜스코딩 작업 레코드
    - :class:`MergeJob`: 여러 트랜스코딩 결과를 병합한 최종 출력 레코드
    - :class:`SplitJob`: 영상 분할 작업 레코드
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal


class JobStatus(Enum):
    """작업 상태 열거형.

    상태 전이: ``PENDING`` -> ``PROCESSING`` -> ``COMPLETED`` | ``FAILED``
    병합 완료 시: ``COMPLETED`` -> ``MERGED``
    """

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    MERGED = "merged"


@dataclass
class TranscodingJob:
    """개별 영상 트랜스코딩 작업 레코드.

    Attributes:
        id: DB 기본키 (신규 생성 시 ``None``)
        video_id: ``videos`` 테이블의 외래키
        temp_file_path: 트랜스코딩 출력 임시 파일 경로
        status: 현재 작업 상태
        progress_percent: 진행률 (0-100)
        started_at: 작업 시작 시각
        completed_at: 작업 완료 시각
        error_message: 실패 시 에러 메시지
    """

    id: int | None
    video_id: int
    temp_file_path: Path | None
    status: JobStatus
    progress_percent: int
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None

    def __post_init__(self) -> None:
        """검증."""
        if not 0 <= self.progress_percent <= 100:
            raise ValueError(f"Invalid progress: {self.progress_percent}")

    @property
    def is_resumable(self) -> bool:
        """이어받기(Resume) 가능 여부.

        상태가 PROCESSING이고 임시 파일 경로가 존재하는 경우에만
        이전 트랜스코딩을 이어서 진행할 수 있다.
        """
        return self.status == JobStatus.PROCESSING and self.temp_file_path is not None


@dataclass
class MergeJob:
    """병합 작업 레코드.

    여러 트랜스코딩 결과를 하나의 영상으로 병합한 이력.
    YouTube 업로드 상태와 요약 정보도 함께 저장한다.

    Attributes:
        id: DB 기본키 (신규 생성 시 ``None``)
        output_path: 최종 병합 영상 파일 경로
        video_ids: 병합에 포함된 ``videos`` 테이블 ID 목록
        status: 현재 작업 상태
        youtube_id: 업로드된 YouTube 영상 ID (미업로드 시 ``None``)
        created_at: 레코드 생성 시각
        title: 영상 제목 (YouTube 업로드용)
        date: 촬영 날짜 문자열
        total_duration_seconds: 전체 영상 길이 (초)
        total_size_bytes: 출력 파일 크기 (바이트)
        clips_info_json: 클립 정보 JSON (챕터 생성용)
        summary_markdown: 요약 마크다운 (YouTube 설명문/콘솔 출력용)
    """

    id: int | None
    output_path: Path
    video_ids: list[int]
    status: JobStatus
    youtube_id: str | None
    created_at: datetime
    # 출력 메타데이터
    title: str | None = None
    date: str | None = None
    total_duration_seconds: float | None = None
    total_size_bytes: int | None = None
    clips_info_json: str | None = None
    summary_markdown: str | None = None

    # video_ids 검증 제거 (기존 레코드 호환성)


@dataclass
class SplitJob:
    """영상 분할 작업 레코드.

    병합된 영상을 시간 또는 크기 기준으로 분할한 이력.

    Attributes:
        id: DB 기본키 (신규 생성 시 ``None``)
        merge_job_id: ``merge_jobs`` 테이블의 외래키
        split_criterion: 분할 기준 (``duration`` 또는 ``size``)
        split_value: 분할 값 문자열 (예: ``1h``, ``10G``)
        output_files: 분할된 출력 파일 경로 목록
        youtube_ids: 파트별 YouTube 영상 ID 목록
        status: 현재 작업 상태
        created_at: 레코드 생성 시각
        error_message: 실패 시 오류 메시지
    """

    id: int | None
    merge_job_id: int
    split_criterion: Literal["duration", "size"]
    split_value: str
    output_files: list[Path]
    status: JobStatus
    created_at: datetime
    youtube_ids: list[str] = field(default_factory=list)
    error_message: str | None = None
