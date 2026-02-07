# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

다양한 기기(Nikon, GoPro, DJI, iPhone)의 4K 영상을 HEVC 10-bit로 표준화하여 병합하는 macOS CLI 도구.

- **핵심 제약**: VideoToolbox 하드웨어 가속 필수 (libx265는 폴백)
- **언어**: Python 3.14+ (strict mypy)
- **패키지 관리**: uv (poetry 사용 금지)

## 명령어

```bash
# 개발 환경 설정
bash scripts/install-hooks.sh                                   # pre-commit hook 설치 (최초 1회)

# 테스트
uv run pytest tests/ -v                                         # 전체 (unit + e2e)
uv run pytest tests/unit/ -v                                    # 단위 테스트만
uv run pytest tests/e2e/ -v                                     # E2E 테스트만 (ffmpeg 필요)
uv run pytest tests/unit/test_scanner.py::test_specific -v      # 단일

# 품질 검사
uv run mypy tubearchive/
uv run ruff check tubearchive/ tests/
uv run ruff format tubearchive/ tests/

# CLI 실행
uv run tubearchive ~/Videos/
uv run tubearchive --dry-run ~/Videos/

# 오디오 처리
uv run tubearchive --normalize-audio ~/Videos/      # EBU R128 loudnorm 2-pass
uv run tubearchive --denoise ~/Videos/              # 오디오 노이즈 제거

# 무음 구간 감지 및 제거
uv run tubearchive --detect-silence ~/Videos/                    # 무음 구간 감지만
uv run tubearchive --trim-silence ~/Videos/                      # 시작/끝 무음 자동 제거
uv run tubearchive --trim-silence --silence-threshold -35dB ~/Videos/  # 커스텀 설정

# 타임랩스
uv run tubearchive --timelapse 10x ~/Videos/                      # 10배속 타임랩스 생성
uv run tubearchive --timelapse 30x --timelapse-audio ~/Videos/    # 오디오 유지 (atempo 가속)
uv run tubearchive --timelapse 5x --timelapse-resolution 1080p ~/Videos/  # 해상도 변환

# 썸네일
uv run tubearchive --thumbnail ~/Videos/            # 기본 지점(10%, 33%, 50%) 썸네일
uv run tubearchive --thumbnail --thumbnail-at 00:01:30 ~/Videos/  # 특정 시점

# YouTube 업로드
uv run tubearchive --setup-youtube                  # 인증 상태 확인
uv run tubearchive --upload ~/Videos/               # 병합 후 업로드
uv run tubearchive --upload-only video.mp4          # 파일만 업로드

# 작업 현황
uv run tubearchive --status                         # 작업 현황 조회
uv run tubearchive --status-detail 1                # 특정 작업 상세 조회

# 설정 파일
uv run tubearchive --init-config                    # ~/.tubearchive/config.toml 생성
uv run tubearchive --config /path/to/config.toml    # 커스텀 설정 파일 지정
```

### 설정 파일 (config.toml)

위치: `~/.tubearchive/config.toml`

우선순위: **CLI 옵션 > 환경변수 > config.toml > 기본값**

환경변수 Shim 패턴: config 값을 환경변수에 주입 (미설정인 경우만). 기존 모듈의 `os.environ.get()` 코드 변경 없이 동작.

```toml
[general]
# output_dir = "~/Videos/output"            # TUBEARCHIVE_OUTPUT_DIR
# parallel = 1                              # TUBEARCHIVE_PARALLEL
# db_path = "~/.tubearchive/tubearchive.db" # TUBEARCHIVE_DB_PATH
# denoise = false                           # TUBEARCHIVE_DENOISE
# denoise_level = "medium"                  # light/medium/heavy (TUBEARCHIVE_DENOISE_LEVEL)
# normalize_audio = true                    # EBU R128 loudnorm (TUBEARCHIVE_NORMALIZE_AUDIO)
# group_sequences = true                    # 연속 파일 시퀀스 그룹핑 (TUBEARCHIVE_GROUP_SEQUENCES)
# fade_duration = 0.5                       # 기본 페이드 시간 (초, TUBEARCHIVE_FADE_DURATION)

[youtube]
# client_secrets = "~/.tubearchive/client_secrets.json"
# token = "~/.tubearchive/youtube_token.json"
# playlist = ["PLxxxxxxxx"]
# upload_chunk_mb = 32                      # 1-256 (TUBEARCHIVE_UPLOAD_CHUNK_MB)
# upload_privacy = "unlisted"               # public/unlisted/private
```

에러 정책: 파일 없음 → 빈 config, TOML 문법 오류 → warning + 빈 config, 타입 오류 → 해당 필드 무시

## 아키텍처

### 파이프라인 흐름 (cli.py:run_pipeline)
```
scan_videos() → group_sequences() → reorder_with_groups()
  → TranscodeOptions 생성
  → Transcoder.transcode_video() (순차 또는 병렬)
  → Merger.merge()
  → [TimelapseGenerator.generate()]
  → save_merge_job_to_db() + save_summary()
  → [upload_to_youtube()]
```

### 핵심 컴포넌트

**cli.py**: CLI 인터페이스 및 파이프라인 오케스트레이터
- `run_pipeline()`: 메인 파이프라인 (스캔→그룹핑→트랜스코딩→병합→저장)
- `ValidatedArgs`: 검증된 CLI 인자 데이터클래스
- `TranscodeOptions`: 트랜스코딩 공통 옵션 (denoise, normalize_audio, fade_map 등)
- `TranscodeResult`: 단일 트랜스코딩 결과 (frozen dataclass)
- `ClipInfo`: NamedTuple (name, duration, device, shot_time) — 클립 메타데이터
- `database_session()`: DB 연결 자동 정리 context manager
- `truncate_path()`: 긴 경로 말줄임 유틸리티

**core/grouper.py**: 연속 파일 시퀀스 감지 및 그룹핑
- GoPro/DJI 카메라의 분할 파일을 자동 감지하여 하나의 촬영 단위로 묶음
- `group_sequences()`: 파일 목록 → `FileSequenceGroup` 리스트
- `compute_fade_map()`: 그룹 경계 기반 페이드 설정 맵 생성
- 내부 모델: `_GoProEntry` (챕터 순서), `_DjiEntry` (타임스탬프+시퀀스)

**core/timelapse.py**: 타임랩스 생성 엔진
- `TimelapseGenerator`: 배속 조절 타임랩스 영상 생성 (2x ~ 60x)
- `generate()`: setpts 기반 비디오 배속, atempo 체인 오디오 가속, 해상도 변환
- `_parse_resolution()`: 프리셋(4k/1080p/720p) 또는 WIDTHxHEIGHT 형식 파싱
- `RESOLUTION_PRESETS`: 해상도 프리셋 매핑
- 비디오 코덱: libx264 (호환성 우선), CRF 23, yuv420p
- 오디오: keep_audio=False면 제거(-an), True면 atempo 체인으로 가속 + AAC 128k

**core/transcoder.py**: 트랜스코딩 엔진
- `detect_metadata()` → 프로파일 선택 → FFmpeg 실행
- VideoToolbox 실패 시 `_transcode_with_fallback()` (libx265)
- Resume: `ResumeManager`가 진행률 추적, 재시작 시 이어서 처리
- Loudnorm: `_run_loudnorm_analysis()` → 1st pass 분석 → 2nd pass 적용 (normalize_audio=True일 때)

**ffmpeg/effects.py**: 필터 생성기
- `create_combined_filter()`: 세로/가로 영상 → 3840x2160 표준화
- 세로: split → blur background (`PORTRAIT_BLUR_RADIUS=20`) → overlay foreground
- HDR→SDR: `colorspace=all=bt709:iall=bt2020` (color_transfer가 HLG/PQ인 경우)
- Dip-to-Black: fade in/out 0.5초
- Silence: 무음 구간 감지 및 제거
  - `create_silence_detect_filter()` (1st pass) → `parse_silence_segments()` → `create_silence_remove_filter()` (2nd pass)
- Loudnorm: EBU R128 타겟 상수 (`LOUDNORM_TARGET_I=-14.0`, `TP=-1.5`, `LRA=11.0`)
  - `create_loudnorm_analysis_filter()` (1st pass) → `parse_loudnorm_stats()` → `create_loudnorm_filter()` (2nd pass)
- `create_audio_filter_chain()`: denoise → silence_remove → fade → loudnorm 오디오 필터 체인 통합
- Timelapse: `setpts=PTS/{speed}` 비디오 배속, `atempo` 체인 오디오 가속 (0.5~2.0 범위 자동 분할)
  - `create_timelapse_video_filter()`, `create_timelapse_audio_filter()`
  - 상수: `TIMELAPSE_MIN_SPEED=2`, `TIMELAPSE_MAX_SPEED=60`, `ATEMPO_MAX=2.0`

**ffmpeg/thumbnail.py**: 썸네일 추출
- 병합 영상에서 지정 시점(기본: 10%, 33%, 50%) JPEG 썸네일 생성
- `--thumbnail-at`으로 커스텀 시점, `--thumbnail-quality`로 품질 조절

**ffmpeg/profiles.py**: 메타데이터 기반 프로파일
- `PROFILE_SDR`: BT.709 (기본, concat 호환성용)
- `PROFILE_HDR_HLG/PQ`: BT.2020 (현재 미사용, SDR 통일)
- 모든 프로파일: `p010le`, `29.97fps`, `50Mbps`

**config.py**: TOML 설정 파일 관리
- `GeneralConfig`: output_dir, parallel, db_path, denoise, denoise_level, normalize_audio, group_sequences, fade_duration
- `YouTubeConfig`: client_secrets, token, playlist, upload_chunk_mb, upload_privacy
- `load_config()`: `~/.tubearchive/config.toml` 파싱 (에러 시 빈 config)
- `apply_config_to_env()`: 미설정 환경변수에만 config 값 주입
- `generate_default_config()`: 주석 포함 기본 템플릿 생성
- TOML 파싱 헬퍼: `_parse_str()`, `_parse_bool()`, `_parse_int()` (타입 안전 파싱)
- 환경변수 헬퍼: `_get_env_bool()` (공통 bool 환경변수 파싱)
- ENV 상수: `ENV_OUTPUT_DIR`, `ENV_PARALLEL`, `ENV_DENOISE` 등 (중앙 관리)

**commands/catalog.py**: 메타데이터 카탈로그/검색 CLI
- `cmd_catalog()`: DB 영상 메타데이터 조회 (기기별 그룹핑, JSON/CSV 출력)
- `cmd_search()`: 날짜/기기/상태 필터 검색
- `STATUS_ICONS`: 작업 상태 아이콘 매핑
- `format_duration()`: 초→분:초 변환

**database/**: SQLite Resume 시스템 + Repository 패턴
- `videos`: 원본 영상 메타데이터
- `transcoding_jobs`: 작업 상태 (pending→processing→completed/failed)
- `merge_jobs`: 병합 이력, YouTube 챕터 정보, `youtube_id` 저장
- DB 위치: `~/.tubearchive/tubearchive.db` (또는 `TUBEARCHIVE_DB_PATH`)
- Repository 클래스: `VideoRepository`, `TranscodingJobRepository`, `MergeJobRepository`
- **DB 접근 규칙**: cli.py에서 직접 SQL을 실행하지 않고 반드시 Repository 메서드를 사용
- DB 연결은 `database_session()` context manager로 자동 정리

**youtube/**: YouTube 업로드 모듈
- `auth.py`: OAuth 2.0 인증 (토큰 저장/갱신, 브라우저 인증 플로우)
- `uploader.py`: Resumable upload (청크 단위, 재시도 로직)
- `playlist.py`: 플레이리스트 관리 (목록 조회, 영상 추가)
- 설정 파일: `~/.tubearchive/client_secrets.json`, `~/.tubearchive/youtube_token.json`
- 환경 변수:
  - `TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS`: OAuth 클라이언트 시크릿 경로
  - `TUBEARCHIVE_YOUTUBE_TOKEN`: 토큰 파일 경로
  - `TUBEARCHIVE_YOUTUBE_PLAYLIST`: 기본 플레이리스트 ID (쉼표로 여러 개 지정 가능)
  - `TUBEARCHIVE_UPLOAD_CHUNK_MB`: 업로드 청크 크기 MB (1-256, 기본: 32)

### 환경 변수 요약

| 환경 변수 | 설명 | 기본값 |
|-----------|------|--------|
| `TUBEARCHIVE_OUTPUT_DIR` | 출력 디렉토리 | 입력과 같은 위치 |
| `TUBEARCHIVE_PARALLEL` | 병렬 트랜스코딩 수 | 1 |
| `TUBEARCHIVE_DB_PATH` | DB 경로 | `~/.tubearchive/tubearchive.db` |
| `TUBEARCHIVE_DENOISE` | 오디오 노이즈 제거 (true/false) | false |
| `TUBEARCHIVE_DENOISE_LEVEL` | 노이즈 제거 강도 (light/medium/heavy) | medium |
| `TUBEARCHIVE_NORMALIZE_AUDIO` | EBU R128 loudnorm (true/false) | true |
| `TUBEARCHIVE_GROUP_SEQUENCES` | 연속 파일 시퀀스 그룹핑 (true/false) | true |
| `TUBEARCHIVE_FADE_DURATION` | 기본 페이드 시간(초) | 0.5 |
| `TUBEARCHIVE_TRIM_SILENCE` | 무음 구간 제거 (true/false) | false |
| `TUBEARCHIVE_SILENCE_THRESHOLD` | 무음 기준 데시벨 | -30dB |
| `TUBEARCHIVE_SILENCE_MIN_DURATION` | 최소 무음 길이(초) | 2.0 |
| `TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS` | OAuth 시크릿 경로 | `~/.tubearchive/client_secrets.json` |
| `TUBEARCHIVE_YOUTUBE_TOKEN` | 토큰 파일 경로 | `~/.tubearchive/youtube_token.json` |
| `TUBEARCHIVE_YOUTUBE_PLAYLIST` | 기본 플레이리스트 ID | - |
| `TUBEARCHIVE_UPLOAD_CHUNK_MB` | 업로드 청크 MB (1-256) | 32 |

### 테스트 구조
```
tests/
├── conftest.py          # 공유 fixture (DB 격리, 임시 디렉토리)
├── unit/                # 단위 테스트 (I/O/DB는 mock/stub)
│   ├── test_scanner.py
│   ├── test_effects.py
│   └── ...
└── e2e/                 # E2E 테스트 (실제 ffmpeg 사용, CI에서 macOS 러너)
    └── test_e2e.py
```
- **단위 테스트** (`tests/unit/`): 외부 의존성 없이 실행 가능. CI에서 ubuntu-latest
- **E2E 테스트** (`tests/e2e/`): ffmpeg + ffprobe 필요. CI에서 macos-latest (VideoToolbox)
- 새 테스트 추가 시 위 기준에 맞는 디렉토리에 배치
- 모든 테스트는 임시 DB/디렉토리 사용

## 개발 규칙

### 버전 관리
- **버전 위치**: `pyproject.toml`과 `tubearchive/__init__.py` 두 곳에서 관리 (동기화 필수)
- **버전 올리기**: 안정적인 기능 추가/변경 완료 시 **반드시 마이너 버전 증가** (예: 0.2.1 → 0.2.2)
- **이유**: uv가 버전 기반으로 wheel 캐시를 재사용하므로, 버전 미변경 시 이전 빌드가 설치될 수 있음

```bash
# 버전 확인
grep -E "^version|^__version__" pyproject.toml tubearchive/__init__.py
```

### uv 캐시 관리
개발 중 캐시 문제로 이전 버전이 실행될 경우:

```bash
# 방법 1: 개발 중에는 uv run 사용 (권장, 항상 현재 소스 실행)
uv run tubearchive --version

# 방법 2: 캐시 정리 후 재설치
uv cache clean --force && uv tool install . --force

# 방법 3: wheel 재빌드 강제
uv tool install . --reinstall

# 캐시 위치 확인
ls ~/.cache/uv/
```

**권장 워크플로우**:
- 개발 중: `uv run tubearchive ...`
- 배포/릴리즈: 버전 올린 후 `uv tool install .`

### FFmpeg 필터 검증
모든 필터 체인은 구현 전 CLI로 사전 검증:
```bash
ffprobe -v quiet -print_format json -show_streams input.mov  # 메타데이터 확인
ffmpeg -i input.mov -filter_complex "..." -c:v hevc_videotoolbox -t 5 test.mp4
```

### DB 작업
- **모든 DB 접근은 Repository 패턴** (`VideoRepository`, `TranscodingJobRepository`, `MergeJobRepository`)
- CLI에서 raw SQL 직접 실행 금지 — 새 쿼리가 필요하면 Repository에 메서드 추가
- DB 연결은 `database_session()` context manager 사용 (자동 close 보장)
- 상태 변경은 트랜잭션 사용
- `progress_percent`: 0-100 범위 체크 제약
- `status`: ENUM 제약 ('pending', 'processing', 'completed', 'failed', 'merged')

### 플랫폼 고려사항
- 파일 생성 시간: macOS `stat.st_birthtime` (st_ctime 아님)
- 임시 파일: `/tmp/tubearchive/` (작업 완료 시 자동 삭제)

## 금지 사항
- poetry 사용 (uv만)
- Type Hints 생략 (strict mypy)
- print() 사용 (logger 사용)
- 하드코딩 경로 (Path 객체 사용)
