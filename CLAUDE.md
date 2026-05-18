# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

다양한 기기(Nikon, GoPro, DJI, iPhone)의 4K 영상을 HEVC 10-bit로 표준화하여 병합하는 macOS CLI 도구.

- **핵심 제약**: VideoToolbox 하드웨어 가속 필수 (libx265는 폴백)
- **언어**: Python 3.14+ (strict mypy)
- **패키지 관리**: uv (poetry 사용 금지)

### 시스템 의존성 (사전 설치 필요)

| 도구 | 용도 | 설치 |
|------|------|------|
| `ffmpeg` / `ffprobe` | 트랜스코딩·메타데이터 추출 | `brew install ffmpeg` |
| `exiftool` | 카메라 기기 모델 감지 (Nikon/Canon/Sony 등 MakerNote) | `brew install exiftool` |

> exiftool이 없으면 iPhone/GoPro/DJI는 정상 감지되지만, Nikon·Canon·Sony 등 DSLR/미러리스 카메라의 기기 모델이 추출되지 않습니다 (경고 1회 출력).

## 명령어

```bash
# 개발 환경 설정
bash scripts/install-hooks.sh                                   # pre-commit + pre-push 훅 설치 (최초 1회)

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
- `pre-commit`은 스테이징된 Python 파일을 대상으로 ruff lint/check, ruff format 체크, mypy, unit test를 빠르게 검증한다. `uv.lock` 또는 `pyproject.toml`이 스테이징된 경우에는 pip-audit 보안 감사도 추가 실행된다.
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
uv run tubearchive --normalize-audio ~/Videos/      # EBU R128 loudnorm 2-pass (병합 후 전체 영상 1회 적용)
uv run tubearchive --denoise ~/Videos/              # 오디오 노이즈 제거
uv run tubearchive --external-audio ~/Audio/mic.wav video.mp4  # 외부 마이크 오디오로 교체
uv run tubearchive --external-audio-dir ~/Audio/Takes video.mp4  # 길이/시각 기반 외부 오디오 후보 자동 선택
uv run tubearchive --external-audio ~/Audio/recorder.wav --external-audio-scope long ~/Videos/day1/  # 긴 외부 녹음 클립별 구간 매칭
uv run tubearchive --external-audio ~/Audio/mic.wav --external-audio-mode mix --camera-audio-volume 0.1 video.mp4  # 외부 오디오+카메라 오디오 믹스
uv run tubearchive --external-audio ~/Audio/mic.wav --sync-audio-clap video.mp4  # 박수/피크 기반 자동 싱크
uv run tubearchive --external-audio ~/Audio/mic.wav --sync-audio-clap --external-audio-drift-correction video.mp4  # 시작/끝 기준음 drift 보정

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

### 레이어드 패키지 구조 (리팩토링 반영)
- `tubearchive/app/`: CLI 진입점·오케스트레이션·조회성 커맨드
  - `app/cli/main.py`: 기존 `cli.py` 역할(파서/검증/파이프라인/업로드 라우팅)
  - `app/queries/`: `catalog/project/stats/migrate` 조회·관리 커맨드
  - `app/tui/`: Textual 기반 TUI 대시보드 (app.py/bridge.py/models.py/screens/widgets)
- `tubearchive/domain/`: 순수 비즈니스 로직/도메인 모델
  - `domain/media/`: 스캔/정렬/트랜스코딩/병합/분할/아카이브/감지/백업/훅/품질/자막
  - `domain/models/`: `VideoFile`, `VideoMetadata`, Job 모델
  - `domain/services/`: 도메인 서비스 계층
- `tubearchive/infra/`: 외부 시스템 연동
  - `infra/ffmpeg/`: 필터/실행기/프로파일/썸네일
  - `infra/db/`: schema/repository/resume
  - `infra/youtube/`: auth/uploader/playlist
  - `infra/notification/`: notifier/providers/events
- `tubearchive/shared/`: 공통 유틸리티(progress/validators/summary/temp)

### 파이프라인 흐름 (app/cli/pipeline.py:run_pipeline)
```text
scan_videos() → group_sequences() → reorder_with_groups()
  → TranscodeOptions 생성 (LUT 옵션 포함)
  → _can_skip_transcoding()  ← PROFILE_SDR 정합 여부 판정 + metadata_cache 생성
      ├─ [스킵 가능] _run_skip_transcoding()  ← stream-copy concat 직행 (ffprobe N회)
      └─ [스킵 불가] Transcoder.transcode_video() (순차 또는 병렬, auto-lut + lut3d)
           → [select_external_audio_candidate()]  ← --external-audio-dir 지정 시 후보 자동 선택
           → [calculate_external_audio_segments()]  ← --external-audio-scope long 사전 분석
           → [calculate_clap_sync_offset()/calculate_clap_sync_drift()]  ← 외부 오디오 clap sync/drift
           → [_run_vidstab_analysis()]  ← 영상 안정화 1st pass (--stabilize 시)
  → Merger.merge()
  → [_apply_post_merge_loudnorm()]  ← EBU R128 loudnorm 2-pass, -c:v copy (--normalize-audio 시)
  → [_apply_bgm_mixing()]  ← BGM 믹싱 (--bgm 옵션 시)
  → [TimelapseGenerator.generate()]
  → save_merge_job_to_db() + save_summary()
  → [_link_merge_job_to_project()]  ← 프로젝트 연결 (--project 옵션 시)
  → [VideoSplitter.split_video()] (--split-duration/--split-size)
  → [_archive_originals()]
  → [upload_to_youtube()]  ← 프로젝트 플레이리스트 자동 생성/재사용
```

### 핵심 컴포넌트

**app/cli/pipeline.py** (+ **main.py** re-export): CLI 파이프라인 오케스트레이터
- `run_pipeline()`: 메인 파이프라인 (스캔→그룹핑→[스킵판정]→트랜스코딩→병합→후처리→저장→[분할])
- `TranscodeOptions`: 트랜스코딩 공통 옵션 (denoise, stabilize, fade_map, lut_path, auto_lut, lut_before_hdr, device_luts 등 — `normalize_audio`는 병합 후 loudnorm 적용 여부 제어)
- `TranscodeResult`: 단일 트랜스코딩 결과 (frozen dataclass)
- `ClipInfo`: NamedTuple (name, duration, device, shot_time) — 클립 메타데이터
- `_can_skip_transcoding()`: 모든 입력이 PROFILE_SDR과 정합하면 트랜스코딩 스킵 판정. `tuple[bool, str, dict[Path, VideoMetadata]]` 반환 — skip여부, 이유, metadata_cache
  - 스킵 조건: HEVC p010le, 29.97fps, 3840×2160, BT.709, AAC 48kHz, 단일 오디오 트랙, 균질 SAR, 필터 없음(LUT/vidstab/denoise/template 등 비활성)
  - 비스킵도 cache 반환 → non-skip 경로에서 ffprobe 재호출 생략
- `_run_skip_transcoding()`: 스킵 경로 — stream-copy concat 직행, DB에 COMPLETED 즉시 기록
- `_apply_post_merge_loudnorm()`: 병합된 영상 전체에 EBU R128 loudnorm 2-pass 적용. `-c:v copy`로 비디오 stream copy + 오디오만 AAC 재인코딩. 오디오 없음·분석 실패 시 원본 경로 그대로 반환(graceful)
- `_link_merge_job_to_project()`: 병합 결과를 프로젝트에 연결 (없으면 자동 생성, 날짜 범위 갱신)
- `_get_or_create_project_playlist()`: 프로젝트 플레이리스트 자동 생성/재사용
- `_upload_split_files()`: 분할 파일 순차 YouTube 업로드 (챕터 리매핑 + Part N/M 제목)
- `_upload_after_pipeline()`: 업로드 라우터 — split_jobs DB에 분할 파일이 있으면 순차 업로드, 없으면 단일 업로드
- `_apply_bgm_mixing()`: 병합 영상에 BGM 믹싱 (ffprobe 길이 확인 → create_bgm_filter → ffmpeg)
- `_get_media_duration()`: ffprobe로 미디어 길이 조회 헬퍼
- `_has_audio_stream()`: ffprobe로 오디오 스트림 존재 확인 헬퍼
- `database_session()`: DB 연결 자동 정리 context manager
- `truncate_path()`: 긴 경로 말줄임 유틸리티

**domain/media/grouper.py**: 연속 파일 시퀀스 감지 및 그룹핑
- GoPro/DJI 카메라의 분할 파일을 자동 감지하여 하나의 촬영 단위로 묶음
- `group_sequences()`: 파일 목록 → `FileSequenceGroup` 리스트
- `compute_fade_map()`: 그룹 경계 기반 페이드 설정 맵 생성
- 내부 모델: `_GoProEntry` (챕터 순서), `_DjiEntry` (타임스탬프+시퀀스)

**domain/media/splitter.py**: 영상 분할 엔진
- `SplitOptions`: 분할 옵션 (duration 또는 size)
- `VideoSplitter`: FFmpeg segment muxer를 사용한 영상 분할 (재인코딩 없음)
- `parse_duration()`: 시간 문자열 파싱 (`1h`, `30m`, `1h30m15s` → 초)
- `parse_size()`: 크기 문자열 파싱 (`10G`, `500M`, `1.5G` → 바이트)
- `split_video()`: 실제 분할 실행 → 출력 파일 목록 반환
- `probe_duration()`: ffprobe로 분할 파일의 실제 길이(초) 조회 (키프레임 기준 분할이라 요청 시간과 다를 수 있음)
- `probe_bitrate()`: ffprobe로 영상 비트레이트(bps) 조회 (크기 기준 분할 시 segment_time 추정에 사용)

**domain/media/timelapse.py**: 타임랩스 생성 엔진
- `TimelapseGenerator`: 배속 조절 타임랩스 영상 생성 (2x ~ 60x)
- `generate()`: setpts 기반 비디오 배속, atempo 체인 오디오 가속, 해상도 변환
- `_parse_resolution()`: 프리셋(4k/1080p/720p) 또는 WIDTHxHEIGHT 형식 파싱
- `RESOLUTION_PRESETS`: 해상도 프리셋 매핑
- 비디오 코덱: libx264 (호환성 우선), CRF 23, yuv420p
- 오디오: keep_audio=False면 제거(-an), True면 atempo 체인으로 가속 + AAC 128k

**domain/media/transcoder.py**: 트랜스코딩 엔진
- `detect_metadata()` → 프로파일 선택 → FFmpeg 실행
- VideoToolbox 실패 시 `_transcode_with_fallback()` (libx265)
- Resume: `ResumeManager`가 진행률 추적, 재시작 시 이어서 처리
- Loudnorm: 클립별 per-file loudnorm **제거됨** — 라우드니스 정규화는 병합 후 `_apply_post_merge_loudnorm()`(pipeline.py)에서 1회 처리 (클립 간 상대 음량 보존 목적)
- External Audio: `--external-audio` 또는 `--external-audio-dir` 지정 시 영상 내장 오디오 대신 외부 오디오 입력을 사용
  - `--external-audio-dir`: 디렉토리의 지원 오디오 파일 중 영상 길이/파일 시각 기반 최고 점수 후보 선택
  - `--external-audio-scope=single`: 외부 오디오 파일 1개를 영상 1개에 적용
  - `--external-audio-scope=long`: 긴 외부 녹음 1개를 여러 영상 클립에 클립별 구간 매칭
  - `external_audio_mode=replace`: 외부 오디오를 `1:a:0`으로 매핑하여 내장 오디오 교체
  - `external_audio_mode=mix`: 외부 오디오와 `volume={camera_audio_volume}` 처리한 카메라 오디오를 `amix`로 합성
  - `--sync-audio-clap`: `calculate_clap_sync_offset()` 결과와 `--external-audio-offset`을 합산해 FFmpeg `-itsoffset` 적용
  - `--external-audio-drift-correction`: 두 개 이상 transient로 `calculate_clap_sync_drift()`를 실행하고 외부 오디오 `atempo` 적용
  - `scope=long`: pipeline 사전 분석에서 `ExternalAudioSegment` 맵을 만들고, `Transcoder`에는 클립별 `external_audio_start`/`external_audio_duration`만 전달
- Vidstab: `_run_vidstab_analysis()` → 1st pass detect → 2nd pass transform (stabilize=True일 때, 실패 시 graceful skip)
- Auto-LUT: `_resolve_auto_lut()` — 기기 모델 부분 문자열 매칭 → 가장 긴 키워드 우선 → LUT 파일 경로 반환
- `register_video()` (public): 스킵 경로(`_run_skip_transcoding`)에서 DB 등록 시 호출

**domain/media/audio_sync.py**: 외부 오디오 자동 싱크
- `extract_mono_pcm_samples()`: FFmpeg로 미디어의 첫 오디오 스트림을 저해상도 mono PCM으로 추출
- `find_transient_candidates()`: 박수/딱 소리 같은 짧고 큰 transient 후보 검출
- `estimate_clap_sync_offset()`: 내장 오디오 기준 피크와 외부 오디오 피크의 시간 차이 계산
- `calculate_clap_sync_offset()`: 실제 미디어 파일 2개에서 샘플 추출 후 offset/confidence 반환
- `estimate_clap_sync_with_drift()` / `calculate_clap_sync_drift()`: 시작/끝 기준음 등 2개 이상 transient로 offset과 `atempo` 비율 산출
- `select_external_audio_candidate()`: 지원 오디오 확장자를 스캔하고 영상 길이/파일 시각 점수로 최적 후보 선택
- `estimate_external_audio_segment()` / `calculate_external_audio_segments()`: 카메라 내장 오디오 energy envelope와 긴 외부 녹음의 envelope 상관관계로 클립별 시작 구간 산출
- `ExternalAudioSegment`: 긴 외부 녹음 파일 경로, 시작 시점, 클립 길이, confidence, tempo_ratio를 담는 클립별 매칭 결과
- 주의: 음역대/스펙트럼 유사도는 후보 매칭 보조 지표이고, 싱크 확정은 transient 또는 envelope cross-correlation 같은 시간축 검증이 필요

**infra/ffmpeg/executor.py**: FFmpeg 명령 실행 및 진행률 추적
- `FFmpegExecutor`: 명령 빌드(`build_*`) 및 실행(`run`, `run_analysis`) 오케스트레이터
- `build_transcode_command()`: 트랜스코딩 명령 빌드
  - 외부 오디오 replace: `-i video [-ss start -t duration] [-itsoffset offset] -i external -map 0:v:0 -map 1:a:0`
  - 외부 오디오 replace: 외부 오디오가 짧아도 영상 길이를 보존하도록 `apad` + `-shortest` 적용
  - 긴 외부 녹음: 외부 오디오 입력 앞에 `-ss {segment_start} -t {clip_duration}`를 적용해 클립별 구간만 사용
  - 외부 오디오 mix: `filter_complex`에서 `[0:a:0]volume=...`와 `[1:a:0]`를 `amix=duration=first`로 합성 후 `[a_out]` 매핑
  - drift correction: 외부 오디오 입력 필터에 `atempo={external_audio_tempo}` 적용
- `build_concat_command()`: concat 병합 명령 빌드
- `build_loudness_analysis_command()`: loudnorm 1st pass 분석 명령 빌드 (`-af -vn`)
- `build_vidstab_detect_command()`: vidstab 1st pass 분석 명령 빌드 (`-vf -an`)
- `build_silence_detection_command()`: 무음 감지 명령 빌드
- `run()`: 진행률 파싱 + 콜백 실행, `run_analysis()`: stderr 반환
- `parse_progress_line()`: FFmpeg stderr에서 time/frame/fps/bitrate 파싱
- `FFmpegError`: 실패 시 stderr 포함 예외

### 외부 오디오 설계 메모

#### Scope 선택
- `single`: 영상 1개 ↔ 외부 오디오 1개. 기존 `--external-audio-dir` 후보 선택도 최종적으로 단일 외부 파일을 선택해 이 경로를 사용한다.
- `long`: 긴 외부 오디오 1개 ↔ 영상 여러 개. pipeline에서 트랜스코딩 전에 모든 메인 영상 클립을 분석하고, 클립별 `ExternalAudioSegment`를 만들어 순차/병렬 트랜스코딩 공통 옵션에 넣는다.
- TUI의 `AudioBrowserPane`은 `single`/`long`/`dir` 선택을 `OptionsPane` 필드로만 반영한다. 실제 검증과 `ValidatedArgs` 변환은 CLI validator/bridge 경로를 재사용해 CLI와 TUI 동작 차이를 만들지 않는다.

#### long 모드 사전 분석 규칙
- `main_video_files` 기준으로 분석한다. 인트로/아웃트로 템플릿은 외부 녹음 구간 매칭 대상이 아니다.
- 각 클립은 카메라 내장 오디오가 있어야 한다. `metadata.has_audio=False`인 클립은 envelope 매칭 기준이 없으므로 실패시킨다.
- 긴 외부 녹음 검색은 이전 클립의 매칭 종료 시점 이후부터 진행한다. 이렇게 해야 같은 패턴이 앞쪽에서 반복될 때 이전 구간을 다시 선택하지 않는다.
- confidence가 낮으면 실패시키고 사용자에게 기준음 추가 또는 입력 순서/파일 확인을 유도한다.
- `scope=long`에서 `--sync-audio-clap`은 클립별 segment가 이미 산출되므로 각 클립 트랜스코딩 단계에서는 다시 단일 clap offset을 적용하지 않는다.

#### 테스트 기준
- 단위 테스트: `estimate_external_audio_segment()`의 시작점 탐색, `search_start_seconds` 준수, `FFmpegExecutor`의 `-ss/-t` 입력 인자, CLI/TUI 옵션 전달을 검증한다.
- E2E 테스트: pulse 오디오가 포함된 여러 영상 클립과 긴 외부 WAV를 생성해 `--external-audio-scope long`이 최종 병합 영상까지 성공하는지 검증한다.
- 새 보정 알고리즘을 추가할 때는 무음/반복 패턴/짧은 클립/오디오 없는 클립을 최소 단위 테스트로 먼저 고정한다.

**infra/ffmpeg/effects.py**: 필터 생성기
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

**infra/ffmpeg/thumbnail.py**: 썸네일 추출
- 병합 영상에서 지정 시점(기본: 10%, 33%, 50%) JPEG 썸네일 생성
- `--thumbnail-at`으로 커스텀 시점, `--thumbnail-quality`로 품질 조절

**infra/ffmpeg/profiles.py**: 메타데이터 기반 프로파일
- `PROFILE_SDR`: BT.709 (기본, concat 호환성용). 트랜스코딩 스킵 판정의 기준 프로파일 — `hevc` 코덱, `p010le`/`yuv420p10le`(10-bit 4:2:0), `29.97fps(30000/1001)`, `3840×2160`, `aac 48kHz`와 일치하면 스킵 가능
- `PROFILE_HDR_HLG/PQ`: BT.2020 (현재 미사용, SDR 통일)
- 모든 프로파일: `p010le`, `29.97fps`, `50Mbps`

**domain/media/archiver.py**: 원본 파일 아카이브 관리
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

**app/queries/project.py**: 프로젝트 관리 CLI 커맨드
- `cmd_project_list()`: `--project-list` 진입점 (테이블/JSON 출력)
- `cmd_project_detail()`: `--project-detail` 진입점 (날짜별 그룹핑, 업로드 상태)
- `print_project_list()`: 프로젝트 목록 테이블 렌더링
- `print_project_detail()`: 프로젝트 상세 정보 렌더링 (merge_jobs, 날짜 그룹, 크기/시간 집계)

**app/queries/catalog.py**: 메타데이터 카탈로그/검색 CLI
- `cmd_catalog()`: DB 영상 메타데이터 조회 (기기별 그룹핑, JSON/CSV 출력)
- `cmd_search()`: 날짜/기기/상태 필터 검색
- `STATUS_ICONS`: 작업 상태 아이콘 매핑
- `format_duration()`: 초→분:초 변환

**app/queries/stats.py**: 통계 대시보드 CLI
- `cmd_stats()`: `--stats` CLI 진입점 (DB 집계 → 텍스트 대시보드 출력)
- `fetch_stats()`: 4개 Repository의 `get_stats()` 호출 → `StatsData` 조합
- `render_stats()`: 전체 요약, 트랜스코딩, 병합, 기기별 분포, 아카이브 섹션 렌더링
- `render_bar_chart()`: 기기별 분포 텍스트 막대 차트
- 데이터 모델: `StatsData`, `TranscodingStats`, `MergeStats`, `ArchiveStats`, `DeviceStat` (frozen dataclass)
- `--period` 필터: SQL LIKE 패턴으로 연/월/일 모두 지원

**shared/summary_generator.py**: Summary/챕터 생성
- `generate_chapters()`: 클립 목록 → YouTube 챕터 타임스탬프
- `generate_youtube_description()`: 병합 영상용 YouTube 설명
- `remap_chapters_for_splits()`: 분할 파일별 챕터 리매핑 (경계 걸침 시 양쪽 포함)
- `generate_split_youtube_description()`: 분할 파일 하나의 YouTube 설명 생성
- `_aggregate_clips_for_chapters()`: 연속 시퀀스 그룹을 하나의 챕터로 병합

**domain/media/detector.py**: 영상 메타데이터 감지
- `detect_metadata()`: ffprobe 서브프로세스 → `VideoMetadata` 반환 (해상도/코덱/fps/픽셀포맷/색공간/길이/비트레이트 + 오디오 스트림 정보)
  - 오디오 필드: `audio_codec`, `audio_sample_rate`, `audio_channels` (첫 번째 스트림), `audio_stream_count` (전체 개수)
  - SAR: `0:1` 또는 미감지 시 `None`으로 정규화
- exiftool을 통한 GPS 좌표 파싱 (ISO6709 / lat-lon 태그 형식 지원)
- 기기 모델 감지: iPhone/GoPro/DJI는 ffprobe 태그로, Nikon·Canon·Sony 등은 exiftool MakerNote로 추출
- `get_device_model()`: exiftool로 카메라 기기 모델 추출 (exiftool 미설치 시 경고 1회 후 None 반환)

**domain/media/backup.py**: 클라우드 백업 실행
- `BackupExecutor`: `rclone copy` 래퍼 — 실패해도 파이프라인 전체를 중단하지 않음
- `BackupResult`: 백업 결과 (source/remote/success/message, frozen dataclass)

**domain/media/hooks.py**: 후처리 이벤트 훅
- `run_hooks()`: `on_transcode` / `on_merge` / `on_upload` / `on_error` 이벤트를 subprocess로 실행
- `HookContext`: 훅에 전달되는 실행 컨텍스트 (output_path/youtube_id/input_paths/error_message)
- 환경변수로 컨텍스트 전달 (`TUBEARCHIVE_OUTPUT_PATH`, `TUBEARCHIVE_YOUTUBE_ID` 등)

**domain/media/quality.py**: 트랜스코딩 품질 평가
- `QualityReport`: SSIM/PSNR/VMAF 점수 (frozen dataclass)
- `compute_quality_report()`: 원본 ↔ 트랜스코딩 결과 비교 — ffmpeg libvmaf 필터 기반
- 지표별 지원 여부를 확인 후 실패 시 Graceful skip (전체 파이프라인 중단 없음)
- `parse_ssim_output()`, `parse_psnr_output()`, `parse_vmaf_output()`: stderr 파싱 헬퍼

**domain/media/subtitle.py**: Whisper 기반 자막 생성
- `SubtitleGenerator`: openai-whisper로 오디오 전사 → SRT/VTT 자막 파일 생성
- `SubtitleModel`: tiny/base/small/medium/large 열거형
- `SubtitleFormat`: srt/vtt 열거형
- `SubtitleGenerationResult`: 생성 결과 (subtitle_path/detected_language/output_format)
- whisper 미설치 시 `SubtitleGenerationError` 발생

**app/tui/**: Textual 기반 TUI 대시보드
- `app.py`: `TubeArchiveApp` — Textual 앱 진입점, 탭 기반 화면 구성
- `bridge.py`: CLI 파이프라인 ↔ TUI 이벤트 브리지 (비동기 진행률 전달)
- `models.py`: TUI 전용 상태 모델 (`TuiOptionState`)과 옵션 카테고리 정의. 외부 오디오 path/dir/scope/mode/clap/drift/offset/confidence 옵션 포함
- `screens/pipeline.py`: 파이프라인 실행 화면. 파일 브라우저, 외부 오디오 브라우저, 옵션 패널을 조합하고 선택된 오디오 경로를 옵션 상태에 반영
- `screens/youtube.py`: YouTube 업로드 화면
- `screens/history.py`: 작업 이력 조회 화면
- `screens/stats.py`: 통계 대시보드 화면
- `screens/projects.py`: 프로젝트 관리 화면
- `screens/presets.py`: 사전 설정 화면
- `widgets/file_browser.py`: 파일 탐색 위젯
- `widgets/audio_browser.py`: 외부 오디오 파일/후보 폴더 탐색 위젯. 지원 오디오 확장자만 표시하고 단일 파일/긴 녹음/후보 폴더 적용 메시지 발행
- `widgets/option_panels.py`: 옵션 패널 위젯
- `widgets/progress_panel.py`: 진행률 표시 위젯

**app/queries/migrate.py**: DB 마이그레이션 관리
- `cmd_migrate()`: `--export-db` / `--import-db` 진입점
- DB 스키마 버전 간 안전한 마이그레이션 실행

**infra/db/**: SQLite Resume 시스템 + Repository 패턴
- `videos`: 원본 영상 메타데이터
- `transcoding_jobs`: 작업 상태 (pending→processing→completed/failed)
- `merge_jobs`: 병합 이력, YouTube 챕터 정보, `youtube_id` 저장
- `split_jobs`: 영상 분할 이력 (merge_job FK, 분할 기준/값, 출력 파일 목록, `youtube_ids` JSON 배열, `error_message`)
- `archive_history`: 원본 파일 아카이브(이동/삭제) 이력
- `backup_history`: rclone 클라우드 백업 이력 (merge_job FK, source/remote/success/message)
- `projects`: 프로젝트 메타데이터 (name UNIQUE, description, date_range, playlist_id)
- `project_merge_jobs`: 프로젝트 ↔ merge_jobs 다대다 관계 (복합 PK, CASCADE DELETE)
- DB 위치: `~/.tubearchive/tubearchive.db` (또는 `TUBEARCHIVE_DB_PATH`)
- Repository 클래스: `VideoRepository`, `TranscodingJobRepository`, `MergeJobRepository`, `SplitJobRepository`, `ArchiveHistoryRepository`, `BackupHistoryRepository`, `ProjectRepository`
- **DB 접근 규칙**: app/cli/main.py에서 직접 SQL을 실행하지 않고 반드시 Repository 메서드를 사용
- DB 연결은 `database_session()` context manager로 자동 정리

**infra/youtube/**: YouTube 업로드 모듈
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
| `TUBEARCHIVE_TEMPLATE_INTRO` | 인트로 템플릿 영상 경로 | - |
| `TUBEARCHIVE_TEMPLATE_OUTRO` | 아웃트로 템플릿 영상 경로 | - |
| `TUBEARCHIVE_BACKUP_REMOTE` | rclone 백업 원격 경로 | - |
| `TUBEARCHIVE_BACKUP_INCLUDE_ORIGINALS` | 원본 파일도 백업 대상에 포함 (true/false) | false |
| `TUBEARCHIVE_WATCH_PATHS` | 감시 대상 경로 (쉼표 구분) | - |
| `TUBEARCHIVE_WATCH_POLL_INTERVAL` | 파일 안정화 폴링 간격(초) | 1.0 |
| `TUBEARCHIVE_WATCH_STABILITY_CHECKS` | 안정화 확인 횟수 | 2 |
| `TUBEARCHIVE_WATCH_LOG_PATH` | 감시 모드 로그 파일 경로 | - |
| `TUBEARCHIVE_SUBTITLE_LANG` | 자막 언어 코드 (예: ko, en) | - |
| `TUBEARCHIVE_SUBTITLE_MODEL` | Whisper 모델 (tiny/base/small/medium/large) | - |
| `TUBEARCHIVE_SUBTITLE_FORMAT` | 자막 포맷 (srt/vtt) | - |
| `TUBEARCHIVE_SUBTITLE_BURN` | 자막 영상에 하드코딩 (true/false) | false |
| `TUBEARCHIVE_NOTIFY` | 알림 활성화 (true/false) | false |
| `TUBEARCHIVE_NOTIFY_MACOS` | macOS 네이티브 알림 (true/false) | false |
| `TUBEARCHIVE_NOTIFY_MACOS_SOUND` | macOS 알림 사운드 이름 | - |
| `TUBEARCHIVE_NOTIFY_TELEGRAM` | Telegram 알림 (true/false) | false |
| `TUBEARCHIVE_TELEGRAM_BOT_TOKEN` | Telegram 봇 토큰 | - |
| `TUBEARCHIVE_TELEGRAM_CHAT_ID` | Telegram 채팅 ID | - |
| `TUBEARCHIVE_NOTIFY_DISCORD` | Discord 알림 (true/false) | false |
| `TUBEARCHIVE_DISCORD_WEBHOOK_URL` | Discord 웹훅 URL | - |
| `TUBEARCHIVE_NOTIFY_SLACK` | Slack 알림 (true/false) | false |
| `TUBEARCHIVE_SLACK_WEBHOOK_URL` | Slack 웹훅 URL | - |

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
| 비디오 + 다중 오디오 트랙 | 외부 마이크 + 내장 마이크 | 스트림 선택 로직, `audio_stream_count > 1` → 트랜스코딩 스킵 차단 |
| 비디오 + 외부 레코더 오디오 | 전용 보이스레코더 등 별도 WAV 녹음 | replace/mix 매핑, 후보 선택, offset, clap sync 신뢰도, drift tempo |
| 세로 영상 (`is_portrait`) | iPhone 세로 촬영 | blur background + overlay 필터 체인, 트랜스코딩 스킵 차단 |
| HDR 영상 (HLG/PQ) | iPhone Dolby Vision, DJI D-Log | colorspace 변환 필터, 트랜스코딩 스킵 차단 |

- FFmpeg 분석 출력(`loudnorm`, `silencedetect` 등)은 `float()` 변환 후 **유한성(`math.isinf`) 및 범위 검증** 필수
- 새 기기 지원 추가 시 `ffprobe -show_streams`로 스트림 구성을 먼저 확인할 것
- **트랜스코딩 스킵 안전 조건**: LUT/vidstab/denoise/template intro·outro/HDR/portrait/VFR/다중오디오 중 하나라도 활성이면 반드시 스킵 차단 (`_can_skip_transcoding` early-exit 목록 참고)
- 외부 오디오 자동 싱크는 “음역대가 비슷함”만으로 확정하지 않는다. 음역대/스펙트럼은 후보 점수로만 쓰고, 최종 offset은 clap/tone transient 또는 시간축 envelope correlation으로 검증한다.
- 긴 외부 녹음 구간 매칭은 `--external-audio-scope long`에서 사전 분석으로 수행한다. 각 클립에 카메라 내장 오디오가 없으면 envelope 매칭을 할 수 없으므로 실패시킨다.
- `external_audio_mode=mix`는 카메라 오디오가 있어야 의미가 있다. 오디오 없는 영상에서는 replace와 동일하게 외부 오디오만 사용하는 경로를 유지한다.

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
- **모든 DB 접근은 Repository 패턴** (`VideoRepository`, `TranscodingJobRepository`, `MergeJobRepository`, `SplitJobRepository`, `ArchiveHistoryRepository`, `BackupHistoryRepository`, `ProjectRepository`)
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
