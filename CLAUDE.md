# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

다양한 기기(Nikon, GoPro, DJI, iPhone)의 4K 영상을 HEVC 10-bit로 표준화하여 병합하는 macOS CLI 도구.

- **핵심 제약**: VideoToolbox 하드웨어 가속 필수 (libx265는 폴백)
- **언어**: Python 3.14+ (strict mypy)
- **패키지 관리**: uv (poetry 사용 금지)

## 명령어

```bash
# 테스트
uv run pytest tests/ -v                                    # 전체
uv run pytest tests/test_scanner.py::test_specific -v      # 단일

# 품질 검사
uv run mypy tubearchive/
uv run ruff check tubearchive/ tests/
uv run ruff format tubearchive/ tests/

# CLI 실행
uv run tubearchive ~/Videos/
uv run tubearchive --dry-run ~/Videos/
```

## 아키텍처

### 파이프라인 흐름 (cli.py:run_pipeline)
```
scan_videos() → Transcoder.transcode_video() → Merger.merge() → save_summary()
```

### 핵심 컴포넌트

**core/transcoder.py**: 트랜스코딩 엔진
- `detect_metadata()` → 프로파일 선택 → FFmpeg 실행
- VideoToolbox 실패 시 `_transcode_with_fallback()` (libx265)
- Resume: `ResumeManager`가 진행률 추적, 재시작 시 이어서 처리

**ffmpeg/effects.py**: 필터 생성기
- `create_combined_filter()`: 세로/가로 영상 → 3840x2160 표준화
- 세로: split → blur background → overlay foreground
- HDR→SDR: `colorspace=all=bt709:iall=bt2020` (color_transfer가 HLG/PQ인 경우)
- Dip-to-Black: fade in/out 0.5초

**ffmpeg/profiles.py**: 메타데이터 기반 프로파일
- `PROFILE_SDR`: BT.709 (기본, concat 호환성용)
- `PROFILE_HDR_HLG/PQ`: BT.2020 (현재 미사용, SDR 통일)
- 모든 프로파일: `p010le`, `29.97fps`, `50Mbps`

**database/**: SQLite Resume 시스템
- `videos`: 원본 영상 메타데이터
- `transcoding_jobs`: 작업 상태 (pending→processing→completed/failed)
- `merge_jobs`: 병합 이력, YouTube 챕터 정보
- DB 위치: `~/.tubearchive/tubearchive.db` (또는 `TUBEARCHIVE_DB_PATH`)

### 테스트 구조
- `conftest.py`: session-scoped 테스트 DB 격리 (운영 DB와 분리)
- 모든 테스트는 임시 DB/디렉토리 사용

## 개발 규칙

### FFmpeg 필터 검증
모든 필터 체인은 구현 전 CLI로 사전 검증:
```bash
ffprobe -v quiet -print_format json -show_streams input.mov  # 메타데이터 확인
ffmpeg -i input.mov -filter_complex "..." -c:v hevc_videotoolbox -t 5 test.mp4
```

### DB 작업
- 상태 변경은 트랜잭션 사용
- `progress_percent`: 0-100 범위 체크 제약
- `status`: ENUM 제약 ('pending', 'processing', 'completed', 'failed')

### 플랫폼 고려사항
- 파일 생성 시간: macOS `stat.st_birthtime` (st_ctime 아님)
- 임시 파일: `/tmp/tubearchive/` (작업 완료 시 자동 삭제)

## 금지 사항
- poetry 사용 (uv만)
- Type Hints 생략 (strict mypy)
- print() 사용 (logger 사용)
- 하드코딩 경로 (Path 객체 사용)
