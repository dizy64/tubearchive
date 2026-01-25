# TubeArchive CLI (Project Komorebi)

다양한 기기(Nikon, GoPro, DJI, iPhone)의 4K 영상을 HEVC 10-bit로 표준화하여 병합하는 CLI 도구.

## 주요 기능

- **스마트 파일 스캔**: 3가지 케이스 지원 (현재 디렉토리 / 특정 파일 / 디렉토리)
- **세로 영상 자동 레이아웃**: 블러 배경 + 중앙 전경
- **Resume 기능**: SQLite 기반 상태 추적, 중단된 작업 자동 재개
- **VideoToolbox 하드웨어 가속**: Mac M1/M2에서 고속 인코딩
- **기기별 자동 감지**: Nikon N-Log, iPhone, GoPro, DJI 자동 인식
- **Dip-to-Black 효과**: 0.5초 Fade In/Out 자동 적용

## 지원 기기 및 프로파일

| 기기 | 인코딩 프로파일 | 컬러 스페이스 |
|------|----------------|--------------|
| Nikon (N-Log) | HEVC 50Mbps 10-bit | Rec.2020 HDR |
| iPhone | HEVC 40Mbps 8-bit | Rec.709 SDR |
| GoPro | HEVC 50Mbps 8-bit | Rec.709 SDR |
| DJI | HEVC 50Mbps 8-bit | Rec.709 SDR |
| 기타 | HEVC 50Mbps 10-bit | 자동 감지 |

## 요구사항

- macOS 12+ (VideoToolbox 필수)
- Python 3.14+
- FFmpeg 6.0+ (VideoToolbox 지원 빌드)
- asdf (Python 버전 관리)
- uv (패키지 관리)

## 설치

### 1. 프로젝트 설치

```bash
# 저장소 클론
git clone <repository-url>
cd tubearchive

# Python 버전 설정 (asdf)
asdf install python 3.14.2
asdf local python 3.14.2

# 의존성 설치 (uv)
uv sync
```

### 2. 전역 CLI 도구로 설치 (권장)

프로젝트 디렉토리 외부에서도 `tubearchive` 명령어를 사용하려면:

```bash
# tubearchive 디렉토리에서 실행
cd /path/to/tubearchive
uv tool install .

# PATH 설정 (최초 1회, 쉘 재시작 필요)
uv tool update-shell
source ~/.zshrc  # 또는 터미널 재시작
```

설치 확인:
```bash
uv tool list
# 출력: tubearchive v0.1.0
```

업데이트:
```bash
cd /path/to/tubearchive
uv tool install . --force
```

제거:
```bash
uv tool uninstall tubearchive
```

## 사용법

### 기본 사용

전역 설치 후:
```bash
# Case 1: 현재 디렉토리의 모든 영상 병합
tubearchive

# Case 2: 특정 파일들만 병합 (파일 생성 시간 순 정렬)
tubearchive video1.mp4 video2.mov video3.mts

# Case 3: 특정 디렉토리의 영상 병합
tubearchive ~/Videos/Trip2024/
```

프로젝트 디렉토리에서 직접 실행:
```bash
cd /path/to/tubearchive
uv run tubearchive ~/Videos/Trip2024/
```

### 다른 경로에서 실행

전역 설치 없이 다른 경로에서 실행하려면 `--project` 옵션 사용:

```bash
# 어디서든 실행 가능
uv run --project /path/to/tubearchive tubearchive ~/Videos/Trip2024/

# 예시
cd ~/Downloads
uv run --project ~/Workspaces/dizy64/tubearchive tubearchive ./videos/ -o merged.mp4
```

### 옵션

```bash
# 출력 파일 지정
uv run tubearchive -o merged_output.mp4 ~/Videos/

# 실행 계획만 확인 (Dry Run)
uv run tubearchive --dry-run ~/Videos/

# Resume 기능 비활성화
uv run tubearchive --no-resume ~/Videos/

# 임시 파일 보존 (디버깅용)
uv run tubearchive --keep-temp ~/Videos/

# 상세 로그 출력
uv run tubearchive -v ~/Videos/
```

### 출력 요약 및 YouTube 정보

병합 완료 시 자동으로 요약 파일(`*_summary.md`)이 생성됩니다.

**디렉토리 네이밍 규칙**:
```
~/Videos/2024-01-15 도쿄 여행/
         ├── clip1.mp4
         ├── clip2.mp4
         └── clip3.mp4
```

위 구조로 실행하면 자동으로 제목과 날짜가 추출됩니다:
- **제목**: `도쿄 여행`
- **날짜**: `2024-01-15`

**생성되는 요약 파일 예시** (`merged_output_summary.md`):

```markdown
# 도쿄 여행

**촬영일**: 2024-01-15
**총 길이**: 5:30
**파일 크기**: 1.2 GB
**파일명**: merged_output.mp4

## YouTube 챕터

```
0:00 clip1
1:30 clip2
3:45 clip3
```

## 클립 목록

| # | 클립명 | 길이 | 시작 시간 |
|---|--------|------|-----------|
| 1 | clip1 | 1:30 | 0:00 |
| 2 | clip2 | 2:15 | 1:30 |
| 3 | clip3 | 1:45 | 3:45 |

## YouTube 설명 템플릿

```
2024-01-15에 촬영한 도쿄 여행 영상입니다.

📍 장소:
📷 장비:

⏱️ 타임라인
0:00 clip1
1:30 clip2
3:45 clip3

#vlog #여행 #일상
```
```

**DB 저장 정보**:
- 병합 작업 이력 (`tubearchive.db`)
- 클립별 시작 시간 및 길이
- 총 재생 시간 및 파일 크기

### 전체 옵션

```
usage: tubearchive [-h] [-o OUTPUT] [--output-dir DIR] [--no-resume]
                   [--keep-temp] [--dry-run] [-v] [targets ...]

다양한 기기의 4K 영상을 표준화하여 병합합니다.

positional arguments:
  targets              영상 파일 또는 디렉토리 (기본: 현재 디렉토리)

options:
  -h, --help           도움말 표시
  -o, --output OUTPUT  출력 파일 경로 (기본: merged_output.mp4)
  --output-dir DIR     출력 파일 저장 디렉토리 (환경변수: TUBEARCHIVE_OUTPUT_DIR)
  --no-resume          Resume 기능 비활성화
  --keep-temp          임시 파일 보존 (디버깅용)
  --dry-run            실행 계획만 출력 (실제 실행 안 함)
  -v, --verbose        상세 로그 출력
```

### 환경 변수

| 환경 변수 | 설명 | 예시 |
|-----------|------|------|
| `TUBEARCHIVE_OUTPUT_DIR` | 기본 출력 디렉토리 | `~/Videos/Processed` |

```bash
# 환경 변수 설정 (~/.zshrc 또는 ~/.bashrc에 추가)
export TUBEARCHIVE_OUTPUT_DIR="$HOME/Videos/Processed"

# 또는 일회성 실행
TUBEARCHIVE_OUTPUT_DIR=~/Videos tubearchive ~/Downloads/clips/
```

### 임시 파일 경로

트랜스코딩 중 생성되는 임시 파일은 `/tmp/tubearchive/`에 저장됩니다.
- 시스템 재부팅 시 자동 정리
- `--keep-temp` 옵션으로 임시 파일 보존 가능

## 프로젝트 구조

```
tubearchive/
├── cli.py                # CLI 인터페이스
├── __main__.py           # python -m 진입점
├── core/
│   ├── scanner.py        # 파일 스캔 (3가지 케이스)
│   ├── detector.py       # ffprobe 메타데이터 감지
│   ├── transcoder.py     # 트랜스코딩 엔진 (Resume 지원)
│   └── merger.py         # concat 병합 (codec copy)
├── database/
│   ├── schema.py         # SQLite 스키마
│   ├── repository.py     # CRUD 작업
│   └── resume.py         # Resume 상태 추적
├── ffmpeg/
│   ├── executor.py       # FFmpeg 실행 및 진행률
│   ├── effects.py        # 필터 (Portrait Layout, Fade)
│   └── profiles.py       # 기기별 인코딩 프로파일
├── models/
│   ├── video.py          # VideoFile, VideoMetadata
│   └── job.py            # TranscodingJob, MergeJob
└── utils/
    ├── validators.py     # 입력 검증
    ├── progress.py       # 진행률 표시
    └── temp_manager.py   # 임시 파일 관리
```

## 개발

### 테스트 실행

```bash
# 전체 테스트
uv run pytest tests/ -v

# 특정 테스트
uv run pytest tests/test_scanner.py -v

# 커버리지 포함
uv run pytest tests/ --cov=tubearchive --cov-report=term-missing
```

### 품질 검사

```bash
# 타입 체크 (mypy strict mode)
uv run mypy tubearchive/

# 린트 (ruff)
uv run ruff check tubearchive/ tests/

# 포맷팅
uv run ruff format tubearchive/ tests/
```

### 커밋 규칙

```
<type>: <subject>

# Type:
# - feat: 새 기능
# - fix: 버그 수정
# - refactor: 리팩터링
# - test: 테스트 추가
# - docs: 문서 수정
```

## FFmpeg 필터 참조

### 세로 영상 레이아웃

```bash
[0:v]split=2[bg][fg];
[bg]scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,boxblur=20:1[bg_blur];
[fg]scale=<width>:<height>[fg_scaled];
[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2,
fade=t=in:st=0:d=0.5,fade=t=out:st=<end>:d=0.5[v_out]
```

### 가로 영상

```bash
scale=3840:2160:force_original_aspect_ratio=decrease,
pad=3840:2160:(ow-iw)/2:(oh-ih)/2,
fade=t=in:st=0:d=0.5,fade=t=out:st=<end>:d=0.5
```

## 트러블슈팅

### PATH 설정 문제

`uv tool install` 후 `tubearchive: command not found` 오류가 발생하면:

```bash
# 방법 1: uv 자동 설정 (권장)
uv tool update-shell
source ~/.zshrc  # 또는 터미널 재시작

# 방법 2: 수동 설정 (~/.zshrc 또는 ~/.bashrc에 추가)
export PATH="$HOME/.local/bin:$PATH"
```

### VideoToolbox 실패

VideoToolbox 인코더 실패 시 자동으로 libx265 소프트웨어 인코더로 폴백합니다.

```
WARNING - VideoToolbox failed, trying libx265 fallback
```

### Resume 재시작

작업이 중단된 경우 동일 명령으로 재실행하면 자동으로 이어서 처리합니다.

```bash
# 중단 후 재실행
uv run tubearchive ~/Videos/Trip2024/
# → Resuming from 45.2s (38%)
```

### 디버깅

```bash
# 상세 로그 + 임시 파일 보존
uv run tubearchive -v --keep-temp ~/Videos/

# FFmpeg 명령어 확인
# 로그에서 "Running FFmpeg:" 라인 확인
```

## 라이선스

MIT
