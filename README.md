# TubeArchive CLI (Project Komorebi)

[![CI](https://github.com/dizy64/tubearchive/actions/workflows/ci.yml/badge.svg)](https://github.com/dizy64/tubearchive/actions/workflows/ci.yml)

다양한 기기(Nikon, GoPro, DJI, iPhone)의 4K 영상을 HEVC 10-bit로 표준화하여 병합하는 CLI 도구.

## 주요 기능

- **스마트 파일 스캔**: 3가지 케이스 지원 (현재 디렉토리 / 특정 파일 / 디렉토리)
- **세로 영상 자동 레이아웃**: 블러 배경 + 중앙 전경
- **Resume 기능**: SQLite 기반 상태 추적, 중단된 작업 자동 재개
- **VideoToolbox 하드웨어 가속**: Mac M1/M2에서 고속 인코딩
- **기기별 자동 감지**: Nikon N-Log, iPhone, GoPro, DJI 자동 인식
- **Dip-to-Black 효과**: 0.5초 Fade In/Out 자동 적용
- **YouTube 업로드**: OAuth 인증, 병합 후 자동 업로드, 챕터 타임스탬프 자동 삽입

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
- uv (패키지 관리)

## 설치

### 0. 시스템 의존성 설치 (새 Mac에서 시작하는 경우)

#### Homebrew 설치
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Apple Silicon Mac의 경우 PATH 설정
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zshrc
source ~/.zshrc
```

#### FFmpeg 설치 (Brewfile 사용)
```bash
# 프로젝트 디렉토리에서 한 번에 설치
brew bundle

# 설치 확인 (videotoolbox 지원 여부)
ffmpeg -encoders 2>/dev/null | grep hevc_videotoolbox
# 출력 예: V..... hevc_videotoolbox    VideoToolbox H.265 Encoder (codec hevc)
```

또는 개별 설치:
```bash
brew install ffmpeg
```

#### Python & uv 설치
```bash
# uv 설치 (Python 버전 관리 포함)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc

# Python 3.14 설치
uv python install 3.14
```

#### 설치 확인
```bash
# 모든 의존성 확인
ffmpeg -version | head -1          # FFmpeg 버전
uv --version                       # uv 버전
uv python list | grep 3.14         # Python 3.14 설치 확인
```

### 1. 프로젝트 설치

```bash
# 저장소 클론
git clone <repository-url>
cd tubearchive

# 의존성 설치 (uv가 자동으로 Python 버전 관리)
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

# 오디오 노이즈 제거 (바람소리/배경 소음 저감)
uv run tubearchive --denoise --denoise-level medium ~/Videos/

# 상세 로그 출력
uv run tubearchive -v ~/Videos/

# 병렬 트랜스코딩 (4개 파일 동시 처리)
uv run tubearchive -j 4 ~/Videos/
```

### 병렬 트랜스코딩

여러 파일을 동시에 트랜스코딩하여 처리 속도를 높일 수 있습니다.

```bash
# CLI 옵션으로 지정
tubearchive -j 4 ~/Videos/           # 4개 파일 동시 처리
tubearchive --parallel 2 ~/Videos/   # 2개 파일 동시 처리

# 환경 변수로 기본값 설정 (~/.zshrc에 추가)
export TUBEARCHIVE_PARALLEL=4

# 환경 변수 설정 후 자동 적용
tubearchive ~/Videos/  # 4개 파일 동시 처리
```

**주의사항:**
- VideoToolbox 하드웨어 인코더는 동시 세션 수에 제한이 있을 수 있음
- 시스템 리소스(CPU, 메모리)에 따라 적절한 값 설정 권장
- 기본값: 1 (순차 처리)

### 리셋 기능

이미 처리된 기록을 초기화하여 다시 작업할 수 있습니다.

```bash
# 빌드 기록 초기화 (트랜스코딩/병합 다시 수행)
tubearchive --reset-build                    # 목록에서 선택
tubearchive --reset-build /path/to/output.mp4  # 특정 파일 지정

# 업로드 기록 초기화 (YouTube 다시 업로드)
tubearchive --reset-upload                   # 목록에서 선택
tubearchive --reset-upload /path/to/output.mp4 # 특정 파일 지정
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

### YouTube 업로드

병합된 영상을 YouTube에 바로 업로드할 수 있습니다.

#### 설정 상태 확인

```bash
# 현재 인증 상태 확인 및 설정 가이드 출력
tubearchive --setup-youtube
```

#### 사전 설정 (최초 1회)

1. **Google Cloud Console 설정**
   - [Google Cloud Console](https://console.cloud.google.com/) 접속
   - 새 프로젝트 생성 또는 기존 프로젝트 선택
   - "APIs & Services" → "Enabled APIs & services" → "YouTube Data API v3" 활성화
   - "APIs & Services" → "Credentials" → "Create Credentials" → "OAuth client ID"
   - Application type: "Desktop app" 선택
   - JSON 다운로드

2. **클라이언트 시크릿 설정**
   ```bash
   # 다운로드한 JSON 파일을 설정 디렉토리에 저장
   mkdir -p ~/.tubearchive
   mv ~/Downloads/client_secret_*.json ~/.tubearchive/client_secrets.json
   ```

3. **첫 실행 시 인증**
   ```bash
   # 첫 업로드 시 브라우저가 열리며 Google 계정 인증 요청
   tubearchive --upload-only video.mp4
   # → 브라우저에서 Google 계정 로그인 및 권한 승인
   # → 토큰이 ~/.tubearchive/youtube_token.json에 자동 저장
   ```

#### 업로드 방법

```bash
# 방법 1: 병합 후 바로 업로드
tubearchive ~/Videos/2024-01-15\ 도쿄\ 여행/ --upload

# 방법 2: 기존 파일 업로드 (병합 없이)
tubearchive --upload-only merged_output.mp4

# 제목 지정
tubearchive --upload-only video.mp4 --upload-title "나의 여행 영상"

# 공개 설정 변경 (기본: unlisted)
tubearchive --upload-only video.mp4 --upload-privacy public
```

#### 업로드 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--upload` | 병합 완료 후 YouTube에 업로드 | - |
| `--upload-only FILE` | 지정된 파일을 YouTube에 업로드 (병합 없이) | - |
| `--upload-title TITLE` | 영상 제목 | 파일명 또는 디렉토리명 |
| `--upload-privacy` | 공개 설정 (public/unlisted/private) | unlisted |
| `--playlist ID` | 업로드 후 플레이리스트에 추가 (여러 번 사용 가능) | - |
| `--list-playlists` | 내 플레이리스트 목록 조회 | - |

#### 플레이리스트에 추가

업로드 후 자동으로 플레이리스트에 추가할 수 있습니다.

```bash
# 플레이리스트 목록 조회 (ID 확인용)
tubearchive --list-playlists

# 출력 예시:
# 번호  제목                                     영상수   ID
# --------------------------------------------------------------------------------
# 1    여행 브이로그                              12       PLxxxxxxxxxxxxxxx
# 2    일상 기록                                  8        PLyyyyyyyyyyyyyyy
#
# 💡 환경 변수 설정 예시:
#    export TUBEARCHIVE_YOUTUBE_PLAYLIST=PLxxxxxxxxxxxxxxx

# 특정 플레이리스트에 추가
tubearchive ~/Videos/ --upload --playlist PLxxxxxxxxxxxxxxx

# 여러 플레이리스트에 동시 추가
tubearchive ~/Videos/ --upload --playlist PLaaaaa --playlist PLbbbbb

# 환경 변수로 기본 플레이리스트 설정 (~/.zshrc에 추가)
export TUBEARCHIVE_YOUTUBE_PLAYLIST=PLxxxxxxxxxxxxxxx
# 또는 여러 개 (쉼표로 구분)
export TUBEARCHIVE_YOUTUBE_PLAYLIST=PLaaaaa,PLbbbbb

# 환경 변수 설정 후에는 --playlist 없이도 자동 추가
tubearchive ~/Videos/ --upload
```

#### 자동 설명 생성

`--upload` 옵션 사용 시 Summary의 YouTube 챕터 타임스탬프가 자동으로 설명에 삽입됩니다.

```
# 자동 생성되는 설명 예시
0:00 clip1
1:30 clip2
3:45 clip3
```

### 전체 옵션

```
usage: tubearchive [-h] [-V] [-o OUTPUT] [--output-dir DIR] [--no-resume]
                   [--keep-temp] [--dry-run] [-v] [-j N]
                   [--denoise] [--denoise-level {light,medium,heavy}]
                   [--upload] [--upload-only FILE]
                   [--upload-title TITLE] [--upload-privacy {public,unlisted,private}]
                   [--playlist ID] [--setup-youtube] [--youtube-auth] [--list-playlists]
                   [--reset-build [PATH]] [--reset-upload [PATH]]
                   [targets ...]

다양한 기기의 4K 영상을 표준화하여 병합합니다.

positional arguments:
  targets               영상 파일 또는 디렉토리 (기본: 현재 디렉토리)

options:
  -h, --help            도움말 표시
  -V, --version         버전 출력
  -o, --output OUTPUT   출력 파일 경로 (기본: merged_output.mp4)
  --output-dir DIR      출력 파일 저장 디렉토리 (환경변수: TUBEARCHIVE_OUTPUT_DIR)
  --no-resume           Resume 기능 비활성화
  --keep-temp           임시 파일 보존 (디버깅용)
  --dry-run             실행 계획만 출력 (실제 실행 안 함)
  -v, --verbose         상세 로그 출력
  -j, --parallel N      병렬 트랜스코딩 수 (환경변수: TUBEARCHIVE_PARALLEL, 기본: 1)
  --denoise             FFmpeg 오디오 노이즈 제거 활성화 (afftdn)
  --denoise-level       노이즈 제거 강도 (light/medium/heavy, 기본: medium)
  --upload              병합 완료 후 YouTube에 업로드
  --upload-only FILE    지정된 파일을 YouTube에 업로드 (병합 없이)
  --upload-title TITLE  YouTube 업로드 시 영상 제목
  --upload-privacy      YouTube 공개 설정 (기본: unlisted)
  --playlist ID         업로드 후 플레이리스트에 추가 (여러 번 사용 가능)
  --setup-youtube       YouTube 인증 상태 확인 및 설정 가이드 출력
  --youtube-auth        YouTube 브라우저 인증 실행
  --list-playlists      내 플레이리스트 목록 조회
  --reset-build [PATH]  빌드 기록 초기화 (트랜스코딩/병합 다시 수행)
  --reset-upload [PATH] 업로드 기록 초기화 (YouTube 다시 업로드)
```

### 환경 변수

| 환경 변수 | 설명 | 기본값 |
|-----------|------|--------|
| `TUBEARCHIVE_OUTPUT_DIR` | 기본 출력 디렉토리 | 출력 파일과 같은 위치 |
| `TUBEARCHIVE_DB_PATH` | 데이터베이스 파일 경로 | `~/.tubearchive/tubearchive.db` |
| `TUBEARCHIVE_PARALLEL` | 병렬 트랜스코딩 수 | 1 (순차 처리) |
| `TUBEARCHIVE_DENOISE` | 오디오 노이즈 제거 기본 활성화 (true/false) | false |
| `TUBEARCHIVE_DENOISE_LEVEL` | 노이즈 제거 강도 (light/medium/heavy) | medium |
| `TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS` | OAuth 클라이언트 시크릿 경로 | `~/.tubearchive/client_secrets.json` |
| `TUBEARCHIVE_YOUTUBE_TOKEN` | OAuth 토큰 저장 경로 | `~/.tubearchive/youtube_token.json` |
| `TUBEARCHIVE_YOUTUBE_PLAYLIST` | 기본 플레이리스트 ID (쉼표로 여러 개 지정) | - |

```bash
# 환경 변수 설정 (~/.zshrc 또는 ~/.bashrc에 추가)
export TUBEARCHIVE_OUTPUT_DIR="$HOME/Videos/Processed"
export TUBEARCHIVE_DB_PATH="$HOME/.tubearchive/tubearchive.db"  # 기본값

# YouTube 설정 (기본 경로 외 다른 위치 사용 시)
export TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS="/path/to/client_secrets.json"
export TUBEARCHIVE_YOUTUBE_TOKEN="/path/to/youtube_token.json"

# 또는 일회성 실행
TUBEARCHIVE_OUTPUT_DIR=~/Videos tubearchive ~/Downloads/clips/
```

### 데이터베이스 위치

모든 작업 이력은 `~/.tubearchive/tubearchive.db`에 저장됩니다.
- 어디서 실행해도 동일한 DB 사용 (중앙화된 관리)
- `TUBEARCHIVE_DB_PATH` 환경 변수로 경로 변경 가능

### 임시 파일 경로

트랜스코딩 중 생성되는 임시 파일은 `/tmp/tubearchive/`에 저장됩니다.
- **작업 완료 시 자동 삭제** (폴더 전체 정리)
- 시스템 재부팅 시에도 자동 정리
- `--keep-temp` 옵션으로 임시 파일 보존 가능 (디버깅용)

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
├── youtube/
│   ├── __init__.py       # 모듈 exports
│   ├── auth.py           # OAuth 2.0 인증
│   ├── uploader.py       # YouTube 업로드 (Resumable)
│   └── playlist.py       # 플레이리스트 관리
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

### 버전/빌드

버전은 `pyproject.toml`과 `tubearchive/__init__.py`에 동시에 반영됩니다.

```bash
# 패치 버전 증가 (기본값)
scripts/bump_version.py

# 마이너/메이저 증가
scripts/bump_version.py --part minor
scripts/bump_version.py --part major

# 다음 버전만 확인 (파일 변경 없음)
scripts/bump_version.py --dry-run
```

빌드는 로컬에서만 사용하도록 `uv build`로 패키징합니다.

```bash
# 리패키징 (dist/ 생성)
scripts/repackage.py
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

### YouTube 업로드 오류

**인증 오류 (client_secrets.json not found)**
```bash
# client_secrets.json 위치 확인
ls -la ~/.tubearchive/client_secrets.json

# 환경 변수로 경로 지정
export TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS=/path/to/client_secrets.json
```

**토큰 만료 (Invalid Credentials)**
```bash
# 토큰 파일 삭제 후 재인증
rm ~/.tubearchive/youtube_token.json
tubearchive --upload-only video.mp4  # 브라우저 인증 다시 진행
```

**API 할당량 초과**
- 일일 업로드 한도: 약 6회 (10,000 유닛 / 업로드당 ~1,600 유닛)
- 24시간 후 자동 리셋

**업로드 실패 (네트워크 오류)**
- Resumable upload 사용으로 자동 재시도 (최대 10회)
- 지속적 실패 시 네트워크 연결 확인

## 라이선스

MIT
