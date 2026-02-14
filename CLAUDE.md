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
```

## AI 가드레일

- 커밋 전 가드레일은 `pre-commit` 훅으로 실행한다.
- `pre-commit`은 스테이징된 Python 파일 대상을 대상으로 ruff lint/check, ruff format 체크, mypy, unit test만 빠르게 검증한다.
- `pre-push` 훅은 푸시 전 단위 테스트만 실행한다.
- 검증은 외부 판단이 아닌 테스트/스크립트 결과를 기준으로 한다.
- 빠른 피드백을 위해 기능 변경 시 우선 관련 unit 테스트로 실패를 조기에 잡는다.

### 실행 예시

```bash
# 훅 설치 (pre-commit + pre-push)
bash scripts/install-hooks.sh

# 수동 실행(개발자 선호 시)
uv run ruff check tubearchive/ tests/
uv run ruff format --check tubearchive/ tests/
uv run mypy tubearchive/
uv run pytest tests/unit/ -q
uv run pytest tests/e2e/ -q                    # 전체 e2e (필요 시 수동 실행)
```

```bash
# 오디오 처리
uv run tubearchive --normalize-audio ~/Videos/      # EBU R128 loudnorm 2-pass
uv run tubearchive --denoise ~/Videos/              # 오디오 노이즈 제거

# 영상 안정화 (vidstab 2-pass)
uv run tubearchive --stabilize ~/Videos/                             # 기본 안정화 (medium strength, crop)
uv run tubearchive --stabilize --stabilize-strength heavy ~/Videos/  # 강한 안정화
uv run tubearchive --stabilize --stabilize-crop expand ~/Videos/     # 가장자리 확장 (crop 대신)
uv run tubearchive --stabilize-strength light ~/Videos/              # strength 지정 시 암묵적 활성화

# BGM 믹싱
uv run tubearchive --bgm ~/Music/bgm.mp3 ~/Videos/                        # BGM 믹싱
uv run tubearchive --bgm ~/Music/bgm.mp3 --bgm-volume 0.3 ~/Videos/      # 볼륨 조절 (0.0~1.0)
uv run tubearchive --bgm ~/Music/bgm.mp3 --bgm-loop ~/Videos/            # BGM 루프 재생

# 무음 구간 감지 및 제거
uv run tubearchive --detect-silence ~/Videos/                    # 무음 구간 감지만
uv run tubearchive --trim-silence ~/Videos/                      # 시작/끝 무음 자동 제거
uv run tubearchive --trim-silence --silence-threshold -35dB ~/Videos/  # 커스텀 설정

# 타임랩스
uv run tubearchive --timelapse 10x ~/Videos/                      # 10배속 타임랩스 생성
uv run tubearchive --timelapse 30x --timelapse-audio ~/Videos/    # 오디오 유지 (atempo 가속)
uv run tubearchive --timelapse 5x --timelapse-resolution 1080p ~/Videos/  # 해상도 변환

# LUT 컬러 그레이딩
uv run tubearchive --lut ~/LUTs/nikon_rec709.cube ~/Videos/       # LUT 직접 지정
uv run tubearchive --auto-lut ~/Videos/                           # 기기별 자동 LUT 매칭
uv run tubearchive --no-auto-lut ~/Videos/                        # 자동 LUT 매칭 비활성화
uv run tubearchive --lut ~/LUTs/nlog.cube --lut-before-hdr ~/Videos/  # HDR 변환 전 적용

# 썸네일
uv run tubearchive --thumbnail ~/Videos/            # 기본 지점(10%, 33%, 50%) 썸네일
uv run tubearchive --thumbnail --thumbnail-at 00:01:30 ~/Videos/  # 특정 시점

# 영상 분할
uv run tubearchive --split-duration 1h ~/Videos/    # 1시간 단위 분할 (segment muxer, 재인코딩 없음)
uv run tubearchive --split-size 10G ~/Videos/       # 10GB 단위 분할

# YouTube 업로드
uv run tubearchive --setup-youtube                  # 인증 상태 확인
uv run tubearchive --upload ~/Videos/               # 병합 후 업로드
uv run tubearchive --upload-only video.mp4          # 파일만 업로드
# 분할 + 업로드: --upload와 --split-duration/--split-size 조합 시
# 분할 파일별 챕터 리매핑 + "(Part N/M)" 제목으로 순차 업로드

# 원본 파일 아카이브
uv run tubearchive --archive-originals ~/Videos/archive ~/Videos/  # 원본 파일을 지정 경로로 이동
uv run tubearchive --archive-force ~/Videos/                       # delete 정책 시 확인 프롬프트 우회

# 프로젝트 관리
uv run tubearchive --project "제주도 여행" ~/Videos/     # 병합 결과를 프로젝트에 연결 (자동 생성)
uv run tubearchive --project-list                        # 프로젝트 목록 조회
uv run tubearchive --project-list --json                 # JSON 형식 출력
uv run tubearchive --project-detail 1                    # 프로젝트 상세 조회 (ID: 1)
uv run tubearchive --project-detail 1 --json             # JSON 형식 출력

# 작업 현황
uv run tubearchive --status                         # 작업 현황 조회
uv run tubearchive --status-detail 1                # 특정 작업 상세 조회

# 통계 대시보드
uv run tubearchive --stats                          # 전체 통계 대시보드
uv run tubearchive --stats --period "2026-01"       # 특정 기간 통계 (연-월)

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
# stabilize = false                         # 영상 안정화 (TUBEARCHIVE_STABILIZE)
# stabilize_strength = "medium"             # light/medium/heavy (TUBEARCHIVE_STABILIZE_STRENGTH)
# stabilize_crop = "crop"                   # crop/expand (TUBEARCHIVE_STABILIZE_CROP)
# group_sequences = true                    # 연속 파일 시퀀스 그룹핑 (TUBEARCHIVE_GROUP_SEQUENCES)
# fade_duration = 0.5                       # 기본 페이드 시간 (초, TUBEARCHIVE_FADE_DURATION)

[bgm]
# bgm_path = "~/Music/bgm.mp3"             # 기본 BGM 파일 경로 (TUBEARCHIVE_BGM_PATH)
# bgm_volume = 0.2                          # 상대 볼륨 0.0~1.0 (TUBEARCHIVE_BGM_VOLUME)
# bgm_loop = false                          # 루프 재생 여부 (TUBEARCHIVE_BGM_LOOP)

[archive]
# policy = "keep"                           # keep/move/delete (TUBEARCHIVE_ARCHIVE_POLICY)
# destination = "~/Videos/archive"          # move 정책 시 이동 경로 (TUBEARCHIVE_ARCHIVE_DESTINATION)

[color_grading]
# auto_lut = true                           # 기기별 자동 LUT 매칭 (TUBEARCHIVE_AUTO_LUT)

[color_grading.device_luts]                 # 키워드=LUT경로 (부분 문자열 매칭, 대소문자 무시)
# nikon = "~/LUTs/nikon_nlog_to_rec709.cube"  # "NIKON Z6III" → 매칭
# gopro = "~/LUTs/gopro_flat_to_rec709.cube"
# iphone = "~/LUTs/apple_log_to_rec709.cube"

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
  → TranscodeOptions 생성 (LUT 옵션 포함)
  → Transcoder.transcode_video() (순차 또는 병렬, auto-lut 매칭 + lut3d 필터)
    → [_run_vidstab_analysis()]  ← 영상 안정화 1st pass (--stabilize 시)
  → Merger.merge()
  → [_apply_bgm_mixing()]  ← BGM 믹싱 (--bgm 옵션 시)
  → [TimelapseGenerator.generate()]
  → save_merge_job_to_db() + save_summary()
  → [_link_merge_job_to_project()]  ← 프로젝트 연결 (--project 옵션 시)
  → [VideoSplitter.split_video()] (--split-duration/--split-size)
  → [_archive_originals()]
  → [upload_to_youtube()]  ← 프로젝트 플레이리스트 자동 생성/재사용
```

### 핵심 컴포넌트

**cli.py**: CLI 인터페이스 및 파이프라인 오케스트레이터
- `run_pipeline()`: 메인 파이프라인 (스캔→그룹핑→트랜스코딩→병합→저장→[분할])
- `ValidatedArgs`: 검증된 CLI 인자 데이터클래스
- `TranscodeOptions`: 트랜스코딩 공통 옵션 (denoise, normalize_audio, stabilize, fade_map, lut_path, auto_lut, lut_before_hdr, device_luts 등)
- `TranscodeResult`: 단일 트랜스코딩 결과 (frozen dataclass)
- `ClipInfo`: NamedTuple (name, duration, device, shot_time) — 클립 메타데이터
- `_link_merge_job_to_project()`: 병합 결과를 프로젝트에 연결 (없으면 자동 생성, 날짜 범위 갱신)
- `_get_or_create_project_playlist()`: 프로젝트 플레이리스트 자동 생성/재사용
- `_upload_split_files()`: 분할 파일 순차 YouTube 업로드 (챕터 리매핑 + Part N/M 제목)
- `_upload_after_pipeline()`: 업로드 라우터 — split_jobs DB에 분할 파일이 있으면 순차 업로드, 없으면 단일 업로드
- `_apply_bgm_mixing()`: 병합 영상에 BGM 믹싱 (ffprobe 길이 확인 → create_bgm_filter → ffmpeg)
- `_get_media_duration()`: ffprobe로 미디어 길이 조회 헬퍼
- `_has_audio_stream()`: ffprobe로 오디오 스트림 존재 확인 헬퍼
- `database_session()`: DB 연결 자동 정리 context manager
- `truncate_path()`: 긴 경로 말줄임 유틸리티

**core/grouper.py**: 연속 파일 시퀀스 감지 및 그룹핑
- GoPro/DJI 카메라의 분할 파일을 자동 감지하여 하나의 촬영 단위로 묶음
- `group_sequences()`: 파일 목록 → `FileSequenceGroup` 리스트
- `compute_fade_map()`: 그룹 경계 기반 페이드 설정 맵 생성
- 내부 모델: `_GoProEntry` (챕터 순서), `_DjiEntry` (타임스탬프+시퀀스)

**core/splitter.py**: 영상 분할 엔진
- `SplitOptions`: 분할 옵션 (duration 또는 size)
- `VideoSplitter`: FFmpeg segment muxer를 사용한 영상 분할 (재인코딩 없음)
- `parse_duration()`: 시간 문자열 파싱 (`1h`, `30m`, `1h30m15s` → 초)
- `parse_size()`: 크기 문자열 파싱 (`10G`, `500M`, `1.5G` → 바이트)
- `split_video()`: 실제 분할 실행 → 출력 파일 목록 반환
- `probe_duration()`: ffprobe로 분할 파일의 실제 길이(초) 조회 (키프레임 기준 분할이라 요청 시간과 다를 수 있음)
- `probe_bitrate()`: ffprobe로 영상 비트레이트(bps) 조회 (크기 기준 분할 시 segment_time 추정에 사용)

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
- Vidstab: `_run_vidstab_analysis()` → 1st pass detect → 2nd pass transform (stabilize=True일 때, 실패 시 graceful skip)
- Auto-LUT: `_resolve_auto_lut()` — 기기 모델 부분 문자열 매칭 → 가장 긴 키워드 우선 → LUT 파일 경로 반환

**ffmpeg/executor.py**: FFmpeg 명령 실행 및 진행률 추적
- `FFmpegExecutor`: 명령 빌드(`build_*`) 및 실행(`run`, `run_analysis`) 오케스트레이터
- `build_transcode_command()`: 트랜스코딩 명령 빌드
- `build_concat_command()`: concat 병합 명령 빌드
- `build_loudness_analysis_command()`: loudnorm 1st pass 분석 명령 빌드 (`-af -vn`)
- `build_vidstab_detect_command()`: vidstab 1st pass 분석 명령 빌드 (`-vf -an`)
- `build_silence_detection_command()`: 무음 감지 명령 빌드
- `run()`: 진행률 파싱 + 콜백 실행, `run_analysis()`: stderr 반환
- `parse_progress_line()`: FFmpeg stderr에서 time/frame/fps/bitrate 파싱
- `FFmpegError`: 실패 시 stderr 포함 예외

**ffmpeg/effects.py**: 필터 생성기
- `create_combined_filter()`: 세로/가로 영상 → 3840x2160 표준화 (`stabilize_filter` 파라미터로 안정화 적용)
- 세로: split → blur background (`PORTRAIT_BLUR_RADIUS=20`) → overlay foreground
- HDR→SDR: `colorspace=all=bt709:iall=bt2020` (color_transfer가 HLG/PQ인 경우)
- Dip-to-Black: fade in/out 0.5초
- Silence: 무음 구간 감지 및 제거
  - `create_silence_detect_filter()` (1st pass) → `parse_silence_segments()` → `create_silence_remove_filter()` (2nd pass)
- Loudnorm: EBU R128 타겟 상수 (`LOUDNORM_TARGET_I=-14.0`, `TP=-1.5`, `LRA=11.0`)
  - `create_loudnorm_analysis_filter()` (1st pass) → `parse_loudnorm_stats()` → `create_loudnorm_filter()` (2nd pass)
- `create_audio_filter_chain()`: denoise → silence_remove → fade → loudnorm 오디오 필터 체인 통합
- BGM 믹싱: `create_bgm_filter()` — aloop(무한루프)+atrim / atrim+afade / volume → amix 필터 생성
- Vidstab: 영상 안정화 2-pass (vidstabdetect → vidstabtransform)
  - `StabilizeStrength` (light/medium/heavy), `StabilizeCrop` (crop/expand) 열거형
  - `create_vidstab_detect_filter()` (1st pass) → `create_vidstab_transform_filter()` (2nd pass)
  - `_VIDSTAB_PARAMS`: strength별 shakiness/accuracy/stepsize/smoothing 매핑
- Timelapse: `setpts=PTS/{speed}` 비디오 배속, `atempo` 체인 오디오 가속 (0.5~2.0 범위 자동 분할)
  - `create_timelapse_video_filter()`, `create_timelapse_audio_filter()`
  - 상수: `TIMELAPSE_MIN_SPEED=2`, `TIMELAPSE_MAX_SPEED=60`, `ATEMPO_MAX=2.0`
- LUT 컬러 그레이딩: `create_lut_filter()` — .cube/.3dl 파일 → `lut3d=file=<경로>` 필터
  - `LUT_SUPPORTED_EXTENSIONS = {".cube", ".3dl"}`
  - 필터 체인 위치: 기본(after) HDR→scale→**LUT**→fade / before: stab→**LUT**→HDR→scale→fade
  - LUT 우선순위: `--lut`(직접 지정) > `--auto-lut`(기기 매칭) > 없음
  - `--lut` + `--auto-lut` 동시 지정 시 `--lut`이 항상 우선

**ffmpeg/thumbnail.py**: 썸네일 추출
- 병합 영상에서 지정 시점(기본: 10%, 33%, 50%) JPEG 썸네일 생성
- `--thumbnail-at`으로 커스텀 시점, `--thumbnail-quality`로 품질 조절

**ffmpeg/profiles.py**: 메타데이터 기반 프로파일
- `PROFILE_SDR`: BT.709 (기본, concat 호환성용)
- `PROFILE_HDR_HLG/PQ`: BT.2020 (현재 미사용, SDR 통일)
- 모든 프로파일: `p010le`, `29.97fps`, `50Mbps`

**core/archiver.py**: 원본 파일 아카이브 관리
- `ArchivePolicy`: 아카이브 정책 열거형 (KEEP/MOVE/DELETE)
- `ArchiveStats`: 아카이브 결과 통계 (dataclass)
- `Archiver`: 정책에 따라 원본 파일 이동/삭제, `ArchiveHistoryRepository`를 통해 이력 기록
- 확인 프롬프트는 CLI 계층(`_prompt_archive_delete_confirmation`)에서 처리

**config.py**: TOML 설정 파일 관리
- `GeneralConfig`: output_dir, parallel, db_path, denoise, denoise_level, normalize_audio, stabilize, stabilize_strength, stabilize_crop, group_sequences, fade_duration
- `BGMConfig`: bgm_path, bgm_volume, bgm_loop
- `ArchiveConfig`: policy (keep/move/delete), destination
- `YouTubeConfig`: client_secrets, token, playlist, upload_chunk_mb, upload_privacy
- `ColorGradingConfig`: auto_lut, device_luts (기기 키워드→LUT 경로 매핑)
- `load_config()`: `~/.tubearchive/config.toml` 파싱 (에러 시 빈 config)
- `apply_config_to_env()`: 미설정 환경변수에만 config 값 주입
- `generate_default_config()`: 주석 포함 기본 템플릿 생성
- TOML 파싱 헬퍼: `_parse_str()`, `_parse_bool()`, `_parse_int()` (타입 안전 파싱)
- 환경변수 헬퍼: `_get_env_bool()` (공통 bool 환경변수 파싱)
- ENV 상수: `ENV_OUTPUT_DIR`, `ENV_PARALLEL`, `ENV_DENOISE` 등 (중앙 관리)

**commands/project.py**: 프로젝트 관리 CLI 커맨드
- `cmd_project_list()`: `--project-list` 진입점 (테이블/JSON 출력)
- `cmd_project_detail()`: `--project-detail` 진입점 (날짜별 그룹핑, 업로드 상태)
- `print_project_list()`: 프로젝트 목록 테이블 렌더링
- `print_project_detail()`: 프로젝트 상세 정보 렌더링 (merge_jobs, 날짜 그룹, 크기/시간 집계)

**commands/catalog.py**: 메타데이터 카탈로그/검색 CLI
- `cmd_catalog()`: DB 영상 메타데이터 조회 (기기별 그룹핑, JSON/CSV 출력)
- `cmd_search()`: 날짜/기기/상태 필터 검색
- `STATUS_ICONS`: 작업 상태 아이콘 매핑
- `format_duration()`: 초→분:초 변환

**commands/stats.py**: 통계 대시보드 CLI
- `cmd_stats()`: `--stats` CLI 진입점 (DB 집계 → 텍스트 대시보드 출력)
- `fetch_stats()`: 4개 Repository의 `get_stats()` 호출 → `StatsData` 조합
- `render_stats()`: 전체 요약, 트랜스코딩, 병합, 기기별 분포, 아카이브 섹션 렌더링
- `render_bar_chart()`: 기기별 분포 텍스트 막대 차트
- 데이터 모델: `StatsData`, `TranscodingStats`, `MergeStats`, `ArchiveStats`, `DeviceStat` (frozen dataclass)
- `--period` 필터: SQL LIKE 패턴으로 연/월/일 모두 지원

**utils/summary_generator.py**: Summary/챕터 생성
- `generate_chapters()`: 클립 목록 → YouTube 챕터 타임스탬프
- `generate_youtube_description()`: 병합 영상용 YouTube 설명
- `remap_chapters_for_splits()`: 분할 파일별 챕터 리매핑 (경계 걸침 시 양쪽 포함)
- `generate_split_youtube_description()`: 분할 파일 하나의 YouTube 설명 생성
- `_aggregate_clips_for_chapters()`: 연속 시퀀스 그룹을 하나의 챕터로 병합

**database/**: SQLite Resume 시스템 + Repository 패턴
- `videos`: 원본 영상 메타데이터
- `transcoding_jobs`: 작업 상태 (pending→processing→completed/failed)
- `merge_jobs`: 병합 이력, YouTube 챕터 정보, `youtube_id` 저장
- `split_jobs`: 영상 분할 이력 (merge_job FK, 분할 기준/값, 출력 파일 목록, `youtube_ids` JSON 배열, `error_message`)
- `archive_history`: 원본 파일 아카이브(이동/삭제) 이력
- `projects`: 프로젝트 메타데이터 (name UNIQUE, description, date_range, playlist_id)
- `project_merge_jobs`: 프로젝트 ↔ merge_jobs 다대다 관계 (복합 PK, CASCADE DELETE)
- DB 위치: `~/.tubearchive/tubearchive.db` (또는 `TUBEARCHIVE_DB_PATH`)
- Repository 클래스: `VideoRepository`, `TranscodingJobRepository`, `MergeJobRepository`, `SplitJobRepository`, `ArchiveHistoryRepository`, `ProjectRepository`
- **DB 접근 규칙**: cli.py에서 직접 SQL을 실행하지 않고 반드시 Repository 메서드를 사용
- DB 연결은 `database_session()` context manager로 자동 정리

**youtube/**: YouTube 업로드 모듈
- `auth.py`: OAuth 2.0 인증 (토큰 저장/갱신, 브라우저 인증 플로우)
- `uploader.py`: Resumable upload (청크 단위, 재시도 로직)
- `playlist.py`: 플레이리스트 관리 (목록 조회, 영상 추가, 플레이리스트 생성)
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
| `TUBEARCHIVE_STABILIZE` | 영상 안정화 vidstab (true/false) | false |
| `TUBEARCHIVE_STABILIZE_STRENGTH` | 안정화 강도 (light/medium/heavy) | medium |
| `TUBEARCHIVE_STABILIZE_CROP` | 안정화 크롭 모드 (crop/expand) | crop |
| `TUBEARCHIVE_GROUP_SEQUENCES` | 연속 파일 시퀀스 그룹핑 (true/false) | true |
| `TUBEARCHIVE_FADE_DURATION` | 기본 페이드 시간(초) | 0.5 |
| `TUBEARCHIVE_TRIM_SILENCE` | 무음 구간 제거 (true/false) | false |
| `TUBEARCHIVE_SILENCE_THRESHOLD` | 무음 기준 데시벨 | -30dB |
| `TUBEARCHIVE_SILENCE_MIN_DURATION` | 최소 무음 길이(초) | 2.0 |
| `TUBEARCHIVE_BGM_PATH` | 기본 BGM 파일 경로 | - |
| `TUBEARCHIVE_BGM_VOLUME` | BGM 상대 볼륨 (0.0~1.0) | 0.2 |
| `TUBEARCHIVE_BGM_LOOP` | BGM 루프 재생 (true/false) | false |
| `TUBEARCHIVE_ARCHIVE_POLICY` | 아카이브 정책 (keep/move/delete) | keep |
| `TUBEARCHIVE_ARCHIVE_DESTINATION` | move 정책 시 이동 경로 | - |
| `TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS` | OAuth 시크릿 경로 | `~/.tubearchive/client_secrets.json` |
| `TUBEARCHIVE_YOUTUBE_TOKEN` | 토큰 파일 경로 | `~/.tubearchive/youtube_token.json` |
| `TUBEARCHIVE_YOUTUBE_PLAYLIST` | 기본 플레이리스트 ID | - |
| `TUBEARCHIVE_UPLOAD_CHUNK_MB` | 업로드 청크 MB (1-256) | 32 |
| `TUBEARCHIVE_AUTO_LUT` | 기기별 자동 LUT 매칭 (true/false) | false |

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
- **E2E 테스트** (`tests/e2e/`): ffmpeg + ffprobe 필요. GitHub PR 워크플로에서만 실행.
- 새 테스트 추가 시 위 기준에 맞는 디렉토리에 배치
- 모든 테스트는 임시 DB/디렉토리 사용

### 입력 스트림 변형 테스트 체크리스트
새 기능(특히 오디오/필터 관련)을 추가할 때 아래 케이스를 반드시 고려:

| 케이스 | 발생 조건 | 테스트 포인트 |
|--------|----------|--------------|
| 비디오 + 정상 오디오 | Nikon, iPhone, GoPro 일반 촬영 | 기본 Happy Path |
| 비디오만 (오디오 스트림 없음) | DJI/GoPro 타임랩스 모드 | `-map 0:a:0` 실패 방지, `has_audio=False` 분기 |
| 비디오 + 무음 오디오 | DJI 드론 고고도 등 | loudnorm `-inf` 처리, FFmpeg 분석 결과 극단값 검증 |
| 비디오 + 다중 오디오 트랙 | 외부 마이크 + 내장 마이크 | 스트림 선택 로직 |
| 세로 영상 (`is_portrait`) | iPhone 세로 촬영 | blur background + overlay 필터 체인 |
| HDR 영상 (HLG/PQ) | iPhone Dolby Vision, DJI D-Log | colorspace 변환 필터 |

- FFmpeg 분석 출력(`loudnorm`, `silencedetect` 등)은 `float()` 변환 후 **유한성(`math.isinf`) 및 범위 검증** 필수
- 새 기기 지원 추가 시 `ffprobe -show_streams`로 스트림 구성을 먼저 확인할 것

## 개발 규칙

### 버전 관리
- **버전 위치**: `pyproject.toml`과 `tubearchive/__init__.py` 두 곳에서 관리 (동기화 필수)
- **버전 올리기**: 안정적인 기능 추가/변경 완료 시 **반드시 마이너 버전 증가** (예: 0.2.1 → 0.2.2)
- **이유**: uv가 버전 기반으로 wheel 캐시를 재사용하므로, 버전 미변경 시 이전 빌드가 설치될 수 있음

### 에이전트 문서 일원화
- 공통 지침은 `CLAUDE.md`를 단일 소스로 관리한다.
- 저장소의 `AGENTS.md`는 `CLAUDE.md`를 가리키는 심볼릭 링크(`AGENTS.md -> CLAUDE.md`)로 유지한다.
- 새 worktree 생성 후 `AGENTS.md`가 일반 파일로 생기거나 누락되면 즉시 심볼릭 링크 상태를 복구한다.

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
- **모든 DB 접근은 Repository 패턴** (`VideoRepository`, `TranscodingJobRepository`, `MergeJobRepository`, `SplitJobRepository`, `ProjectRepository`)
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
