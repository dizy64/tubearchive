# TubeArchive CLI (Project Komorebi)

[![CI](https://github.com/dizy64/tubearchive/actions/workflows/ci.yml/badge.svg)](https://github.com/dizy64/tubearchive/actions/workflows/ci.yml)

다양한 기기(Nikon, GoPro, DJI, iPhone)의 4K 영상을 HEVC 10-bit로 표준화하여 병합하는 CLI 도구.

## 주요 기능

- **스마트 파일 스캔**: 3가지 케이스 지원 (현재 디렉토리 / 특정 파일 / 디렉토리)
- **세로 영상 자동 레이아웃**: 블러 배경 + 중앙 전경
- **Resume 기능**: SQLite 기반 상태 추적, 중단된 작업 자동 재개
- **VideoToolbox 하드웨어 가속**: Mac M1/M2에서 고속 인코딩
- **기기별 자동 감지**: Nikon N-Log, iPhone, GoPro, DJI 자동 인식
- **연속 시퀀스 그룹핑**: GoPro/DJI 분할 파일 자동 감지, 그룹 내 이음새 없이 연결
- **Dip-to-Black 효과**: 0.5초 Fade In/Out 자동 적용 (그룹 경계에서만)
- **오디오 라우드니스 정규화**: EBU R128 loudnorm 2-pass 자동 보정
- **오디오 노이즈 제거**: FFmpeg afftdn 기반 바람소리/배경 소음 저감
- **외부 마이크 오디오 치환**: 박수/피크 기반 자동 싱크 후 외부 음원 사용
- **무음 구간 감지/제거**: 시작/끝 무음 자동 감지 및 트리밍
- **영상 안정화**: vidstab 2-pass 기반 손떨림 보정 (light/medium/heavy)
- **BGM 믹싱**: 배경음악 자동 믹싱, 볼륨 조절, 루프 재생
- **LUT 컬러 그레이딩**: .cube/.3dl LUT 파일 적용, 기기별 자동 매칭
- **타임랩스 생성**: 2x~60x 배속, 해상도 변환, 오디오 가속 지원
- **영상 분할**: 시간/크기 기준 분할 (segment muxer, 재인코딩 없음)
- **썸네일 자동 생성**: 병합 영상에서 주요 지점 JPEG 썸네일 추출
- **YouTube 업로드**: OAuth 인증, 병합 후 자동 업로드, 챕터 타임스탬프 자동 삽입
- **원본 파일 아카이브**: 처리 완료 후 원본 이동/삭제 관리
- **파일 필터링/정렬**: 글로브 패턴 필터, 정렬 기준 변경, 수동 순서 편집
- **설정 파일**: `~/.tubearchive/config.toml`로 기본값 관리
- **프로젝트 관리**: 여러 날의 촬영을 하나의 프로젝트로 묶어 관리, 날짜별 그룹핑 및 상태 조회
- **작업 현황 조회**: 트랜스코딩/병합/업로드 이력 확인
- **통계 대시보드**: 전체 처리 통계, 기기별 분포, 기간별 필터

## 지원 기기 및 프로파일

모든 입력은 **HEVC 50Mbps 10-bit (p010le), 29.97fps**로 통일 트랜스코딩됩니다.

| 기기 | 감지 기준 | 출력 프로파일 |
|------|----------|--------------|
| Nikon (N-Log) | color_transfer: arib-std-b67 / smpte2084 | SDR BT.709 (HDR→SDR 변환) |
| iPhone | 기본 SDR 소스 | SDR BT.709 |
| GoPro | 기본 SDR 소스 | SDR BT.709 |
| DJI | 기본 SDR 소스 | SDR BT.709 |
| 기타 | 자동 감지 | SDR BT.709 (기본) |

> **참고**: concat 병합 호환성을 위해 모든 출력은 SDR (BT.709)로 통일됩니다. HDR 소스는 자동 SDR 변환됩니다.

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
# 출력: tubearchive v0.2.25
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
tubearchive -o merged_output.mp4 ~/Videos/

# 실행 계획만 확인 (Dry Run)
tubearchive --dry-run ~/Videos/

# Resume 기능 비활성화
tubearchive --no-resume ~/Videos/

# 임시 파일 보존 (디버깅용)
tubearchive --keep-temp ~/Videos/

# 상세 로그 출력
tubearchive -v ~/Videos/

# 병렬 트랜스코딩 (4개 파일 동시 처리)
tubearchive -j 4 ~/Videos/

# 훅 이벤트 수동 실행
tubearchive --run-hook on_merge
```

### 오디오 처리

```bash
# EBU R128 오디오 라우드니스 정규화
tubearchive --normalize-audio ~/Videos/

# 오디오 노이즈 제거 (바람소리/배경 소음 저감)
tubearchive --denoise --denoise-level medium ~/Videos/

# 외부 마이크 오디오를 영상 오디오 대신 사용
tubearchive --external-audio ~/Audio/mic.wav video.mp4

# 디렉토리에서 영상 길이/촬영 시각에 가장 가까운 외부 오디오 자동 선택
tubearchive --external-audio-dir ~/Audio/Takes video.mp4

# 외부 마이크를 메인으로 쓰되 카메라 내장 오디오를 낮게 섞기
tubearchive --external-audio ~/Audio/mic.wav --external-audio-mode mix --camera-audio-volume 0.1 video.mp4

# 박수/클랩 같은 공통 피크를 찾아 외부 오디오 자동 싱크
tubearchive --external-audio ~/Audio/mic.wav --sync-audio-clap video.mp4

# 긴 외부 녹음 하나를 여러 영상 클립에 자동 구간 매칭
tubearchive --external-audio ~/Audio/recorder.wav --external-audio-scope long ~/Videos/day1/

# 시작/끝 기준음 2개 이상으로 장시간 drift 보정
tubearchive --external-audio ~/Audio/mic.wav --sync-audio-clap --external-audio-drift-correction video.mp4

# 자동 싱크 결과에 수동 offset을 추가 보정
tubearchive --external-audio ~/Audio/mic.wav --sync-audio-clap --external-audio-offset 0.12 video.mp4
```

> `--external-audio-scope long`은 긴 외부 녹음 1개를 여러 영상 클립에 자동 구간 매칭합니다. 각 영상에는 매칭 기준이 되는 카메라 내장 오디오가 필요합니다.

TUI에서는 Pipeline 탭 오른쪽의 **외부 오디오 선택** 패널에서 오디오 파일이나 후보 폴더를 바로 적용할 수 있습니다.

| TUI 버튼 | 적용 옵션 | 용도 |
|----------|-----------|------|
| 단일 파일 | `--external-audio ... --external-audio-scope single` | 영상 1개에 외부 오디오 파일 1개를 적용합니다. |
| 긴 녹음 | `--external-audio ... --external-audio-scope long` | 긴 외부 녹음 1개에서 여러 영상 클립의 구간을 자동으로 찾아 적용합니다. |
| 후보 폴더 | `--external-audio-dir ...` | 폴더 안의 오디오 파일 중 길이/시각이 가까운 후보를 자동 선택합니다. |

Audio 옵션 섹션에서 `replace`/`mix`, 카메라 오디오 볼륨, clap sync, drift 보정, 수동 offset, 최소 신뢰도, 후보 매칭 창을 조정할 수 있습니다.

#### 외부 마이크 오디오 싱크 가이드

전용 보이스레코더로 녹음한 외부 음원은 영상 내장 오디오와 함께 녹음된 공통 소리를 기준으로 싱크합니다.

##### 적용 범위 선택

| 상황 | 권장 옵션 | 설명 |
|------|-----------|------|
| 영상 1개와 외부 오디오 1개 | `--external-audio ... --external-audio-scope single` | 기본값입니다. 외부 오디오 전체를 해당 영상에 적용합니다. |
| 영상 1개와 외부 오디오 후보 폴더 | `--external-audio-dir ...` | 후보 폴더에서 영상 길이와 파일 시각이 가까운 파일을 자동 선택합니다. |
| 여러 영상 클립과 긴 외부 녹음 1개 | `--external-audio ... --external-audio-scope long` | 긴 외부 녹음에서 각 영상 클립에 대응하는 구간을 찾아 잘라 씁니다. |

`long` 모드는 아래 순서로 동작합니다.

1. 영상 클립을 촬영 시간/정렬 규칙에 따라 최종 순서로 정렬합니다.
2. 각 영상 클립의 카메라 내장 오디오를 저해상도 energy envelope로 분석합니다.
3. 긴 외부 녹음의 envelope에서 각 클립과 가장 비슷한 구간을 순서대로 찾습니다.
4. 트랜스코딩 시 외부 오디오 입력에 `-ss <시작초> -t <클립길이>`를 적용해 해당 구간만 사용합니다.

이 방식은 “음역대가 비슷한 파일”을 고르는 것이 아니라, 시간축의 소리 변화 패턴이 실제로 맞는 구간을 찾습니다.

##### 주요 옵션

- `--external-audio-mode replace`: 영상 내장 오디오를 외부 오디오로 완전 교체합니다. 기본값입니다.
- `--external-audio-dir`: 디렉토리 안의 지원 오디오 파일 중 영상 길이와 파일 시각이 가장 가까운 후보를 자동 선택합니다.
- `--external-audio-scope single`: 외부 오디오 파일 1개를 영상 1개에 적용합니다. 기본값입니다.
- `--external-audio-scope long`: 긴 외부 녹음에서 각 영상 클립과 맞는 구간을 사전 분석 후 자동으로 잘라 적용합니다.
- `--external-audio-mode mix`: 외부 오디오를 메인으로 사용하고 카메라 내장 오디오를 낮은 볼륨으로 섞습니다.
- `--camera-audio-volume`: `mix` 모드에서 카메라 내장 오디오 볼륨을 지정합니다. 권장 시작값은 `0.08`~`0.12`입니다.
- `--sync-audio-clap`: 양쪽 오디오에서 박수/딱 소리 같은 공통 피크를 찾아 자동 offset을 계산합니다.
- `--external-audio-drift-correction`: 두 개 이상의 공통 피크가 있으면 외부 오디오 `atempo` 비율을 추정해 장시간 drift를 보정합니다.
- `--external-audio-offset`: 자동 싱크 결과에 수동 보정값을 추가합니다. 양수는 외부 오디오를 늦추고, 음수는 앞당깁니다.
- `--external-audio-match-window`: `--external-audio-dir` 후보 선택 시 파일 시각을 비교할 시간 창입니다. 기본값은 `300`초입니다.

##### 합성 방식

| 모드 | 결과 | 권장 상황 |
|------|------|-----------|
| `replace` | 출력 영상의 오디오를 외부 오디오로 교체 | 전용 보이스레코더 음질을 최종 오디오로 쓸 때 |
| `mix` | 외부 오디오에 카메라 내장 오디오를 낮은 볼륨으로 섞음 | 현장감이나 싱크 검증용 카메라 오디오를 조금 남기고 싶을 때 |

`mix`에서 카메라 오디오가 너무 크게 들리면 `--camera-audio-volume 0.05`처럼 낮추고, 카메라 오디오가 필요 없으면 기본 `replace`를 사용하세요.

촬영 팁:

- 녹화 시작 직후 박수 1회 또는 보이스레코더의 카메라 싱크용 톤처럼 명확한 기준음을 넣으면 자동 싱크 성공률이 높습니다.
- 장시간 촬영은 시작/끝에 기준음을 한 번씩 넣어야 drift 보정을 사용할 수 있습니다.
- 여러 클립에 긴 외부 녹음 하나를 쓸 때는 각 클립 시작부에 짧은 기준음을 남기면 구간 매칭 신뢰도가 올라갑니다.
- 전용 보이스레코더는 영상 작업 기준으로 WAV 48kHz, 가능하면 32-bit float로 녹음하는 것을 권장합니다.
- 자동 싱크 실패 시 먼저 `--external-audio-offset`으로 수동 보정하고, 디렉토리 자동 매칭은 길이/시각이 가까운 파일명 구조와 함께 사용합니다.

##### 실패 조건과 대응

| 증상 | 원인 | 대응 |
|------|------|------|
| `long` 모드에서 매칭 실패 | 카메라 내장 오디오가 없거나 무음에 가까움 | 해당 클립은 외부 녹음 자동 구간 매칭이 불가능합니다. 카메라 오디오가 있는 원본을 사용하세요. |
| 잘못된 외부 구간이 선택됨 | 비슷한 박수/소리 패턴이 여러 번 반복됨 | 클립 시작부마다 서로 구분되는 기준음을 넣거나 `--external-audio-min-confidence`를 높입니다. |
| 긴 촬영 후반부 싱크가 조금 밀림 | 카메라와 외부 레코더의 클럭 차이 | 단일 긴 클립에는 `--external-audio-drift-correction`을 사용하고, 여러 클립은 클립별 구간 매칭으로 누적 drift를 줄입니다. |
| 출력이 너무 일찍 끝남 | 외부 오디오가 영상보다 짧음 | `replace` 경로는 짧은 외부 오디오를 패딩해 영상 길이를 보존합니다. 계속 재현되면 원본 길이를 `ffprobe`로 확인하세요. |

권장 검증 절차:

1. 먼저 `--dry-run`으로 정렬 순서와 옵션을 확인합니다.
2. 짧은 샘플 폴더로 `--external-audio-scope long`을 실행합니다.
3. 결과 영상 초반/클립 경계/후반부를 들어보고 싱크가 유지되는지 확인합니다.
4. 매칭이 흔들리면 각 클립 시작부에 기준음을 추가한 촬영 방식으로 재시도합니다.

### 무음 구간 감지/제거

```bash
# 무음 구간 감지만 (제거하지 않음)
tubearchive --detect-silence ~/Videos/

# 시작/끝 무음 자동 제거
tubearchive --trim-silence ~/Videos/

# 커스텀 설정 (기준 dB, 최소 무음 길이)
tubearchive --trim-silence --silence-threshold -35dB --silence-duration 3.0 ~/Videos/
```

### 화질 지표 리포트

트랜스코딩 전/후 품질을 SSIM/PSNR/VMAF로 출력합니다.

```bash
# 트랜스코딩 후 지표(가능한 필터만) 출력
tubearchive --quality-report ~/Videos/
```

지원되지 않는 필터는 실패하지 않고 미지원 항목으로 표시되며, 파이프라인은 계속 진행됩니다.

### 영상 안정화

vidstab 2-pass 기반 손떨림 보정. 트랜스코딩 시간이 증가합니다.

```bash
# 기본 안정화 (medium strength, crop 모드)
tubearchive --stabilize ~/Videos/

# 강한 안정화
tubearchive --stabilize --stabilize-strength heavy ~/Videos/

# 가장자리 확장 (crop 대신 검은색 채움)
tubearchive --stabilize --stabilize-crop expand ~/Videos/

# strength 지정 시 --stabilize 암묵적 활성화
tubearchive --stabilize-strength light ~/Videos/
```

| 강도 | 설명 |
|------|------|
| `light` | 미세한 흔들림만 보정, 원본 느낌 유지 |
| `medium` | 일반적 손떨림 보정 (기본값) |
| `heavy` | 강한 흔들림 보정, 크롭 영역 증가 |

### BGM 믹싱

병합 영상에 배경음악을 자동으로 믹싱합니다.

```bash
# BGM 믹싱
tubearchive --bgm ~/Music/bgm.mp3 ~/Videos/

# 볼륨 조절 (0.0~1.0, 기본: 0.2)
tubearchive --bgm ~/Music/bgm.mp3 --bgm-volume 0.3 ~/Videos/

# BGM 루프 재생 (BGM이 영상보다 짧을 때)
tubearchive --bgm ~/Music/bgm.mp3 --bgm-loop ~/Videos/
```

### LUT 컬러 그레이딩

.cube 또는 .3dl 형식의 LUT 파일을 트랜스코딩 시 적용합니다.

```bash
# LUT 직접 지정
tubearchive --lut ~/LUTs/nikon_rec709.cube ~/Videos/

# 기기별 자동 LUT 매칭 (config.toml의 device_luts 설정 필요)
tubearchive --auto-lut ~/Videos/

# 자동 LUT 매칭 비활성화
tubearchive --no-auto-lut ~/Videos/

# HDR 변환 전에 LUT 적용 (Nikon N-Log 등)
tubearchive --lut ~/LUTs/nlog.cube --lut-before-hdr ~/Videos/
```

> **우선순위**: `--lut`(직접 지정) > `--auto-lut`(기기 매칭) > 없음

### 타임랩스 생성

병합 영상에서 배속 타임랩스를 생성합니다.

```bash
# 10배속 타임랩스
tubearchive --timelapse 10x ~/Videos/

# 오디오 유지 (atempo 가속)
tubearchive --timelapse 30x --timelapse-audio ~/Videos/

# 해상도 변환 (프리셋: 4k, 1080p, 720p 또는 WIDTHxHEIGHT)
tubearchive --timelapse 5x --timelapse-resolution 1080p ~/Videos/
```

- 배속 범위: 2x ~ 60x
- 비디오: libx264, CRF 23
- 오디오: 기본 제거, `--timelapse-audio` 시 atempo 체인으로 가속

### 영상 분할

FFmpeg segment muxer를 사용하여 재인코딩 없이 분할합니다.

```bash
# 시간 기준 분할 (1시간 단위)
tubearchive --split-duration 1h ~/Videos/

# 파일 크기 기준 분할 (10GB 단위)
tubearchive --split-size 10G ~/Videos/

# 분할 + YouTube 업로드 (분할 파일별 챕터 리매핑 + "Part N/M" 제목)
tubearchive --split-duration 1h --upload ~/Videos/
```

시간 형식: `1h`, `30m`, `1h30m15s`
크기 형식: `10G`, `500M`, `1.5G`

### 썸네일

```bash
# 기본 지점(10%, 33%, 50%) 썸네일
tubearchive --thumbnail ~/Videos/

# 특정 시점에서 썸네일 추출
tubearchive --thumbnail --thumbnail-at 00:01:30 --thumbnail-at 00:03:00 ~/Videos/

# 업로드 시 썸네일 직접 지정
tubearchive --set-thumbnail /path/to/cover.jpg --upload --thumbnail ~/Videos/

# 병합 없이 파일 업로드 + 썸네일 지정
tubearchive --set-thumbnail /path/to/cover.jpg --upload-only merged_output.mp4
```

`--set-thumbnail`은 YouTube 업로드에서 우선 사용됩니다. 값이 없으면 `--thumbnail`로 생성한 썸네일을 다음 순서로 선택합니다.

- 생성된 썸네일이 1개면 자동 사용
- 생성된 썸네일이 여러 개면 업로드 단계에서 0~N 선택 대화형 입력

YouTube는 썸네일 규격을 `최소 1280x720`, `최대 2MB`로 요구합니다. 조건 미달/초과 시 업로드 전에 자동으로 `*_youtube.jpg`로 재인코딩합니다.

### 원본 파일 아카이브

트랜스코딩 완료 후 원본 파일을 관리합니다.

```bash
# 원본 파일을 지정 경로로 이동
tubearchive --archive-originals ~/Videos/archive ~/Videos/

# delete 정책 시 확인 프롬프트 우회
tubearchive --archive-force ~/Videos/
```

config.toml에서 기본 정책을 설정할 수도 있습니다 (`[archive]` 섹션 참조).

### 파일 필터링 및 정렬

```bash
# 특정 패턴의 파일 제외 (글로브, 반복 가능)
tubearchive --exclude "GH*" --exclude "*.mts" ~/Videos/

# 특정 패턴의 파일만 포함
tubearchive --include-only "*.mp4" ~/Videos/

# 정렬 기준 변경 (기본: time)
tubearchive --sort name ~/Videos/     # 이름순
tubearchive --sort size ~/Videos/     # 크기순
tubearchive --sort device ~/Videos/   # 기기순

# 인터랙티브 모드로 클립 순서 수동 편집
tubearchive --reorder ~/Videos/
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

### 설정 파일 (config.toml)

기본값을 설정 파일로 관리할 수 있습니다. 우선순위: **CLI 옵션 > 환경변수 > config.toml > 기본값**

```bash
# 기본 설정 파일 생성
tubearchive --init-config
# → ~/.tubearchive/config.toml 생성

# 커스텀 설정 파일 사용
tubearchive --config /path/to/config.toml ~/Videos/
```

설정 파일 예시:

```toml
[general]
# output_dir = "~/Videos/output"
# parallel = 1
# db_path = "~/.tubearchive/tubearchive.db"
# denoise = false
# denoise_level = "medium"              # light/medium/heavy
# normalize_audio = true                # EBU R128 loudnorm
# stabilize = false                     # 영상 안정화
# stabilize_strength = "medium"         # light/medium/heavy
# stabilize_crop = "crop"               # crop/expand
# group_sequences = true
# fade_duration = 0.5

[bgm]
# bgm_path = "~/Music/bgm.mp3"         # 기본 BGM 파일 경로
# bgm_volume = 0.2                      # 상대 볼륨 0.0~1.0
# bgm_loop = false                      # 루프 재생 여부

[archive]
# policy = "keep"                       # keep/move/delete
# destination = "~/Videos/archive"      # move 정책 시 이동 경로

[color_grading]
# auto_lut = true                       # 기기별 자동 LUT 매칭

[color_grading.device_luts]             # 키워드=LUT경로 (부분 문자열 매칭, 대소문자 무시)
# nikon = "~/LUTs/nikon_nlog_to_rec709.cube"
# gopro = "~/LUTs/gopro_flat_to_rec709.cube"
# iphone = "~/LUTs/apple_log_to_rec709.cube"

[youtube]
# client_secrets = "~/.tubearchive/client_secrets.json"
# token = "~/.tubearchive/youtube_token.json"
# playlist = ["PLxxxxxxxx"]
# upload_chunk_mb = 32                  # 1-256
# upload_privacy = "unlisted"           # public/unlisted/private

[hooks]
# timeout_sec = 60                     # 훅 타임아웃(초)
# on_transcode = ["/path/to/transcode_hook.sh"]
# on_merge = "/path/to/merge_hook.sh"
# on_upload = ["/path/to/upload_hook.sh"]
# on_error = "/path/to/error_hook.sh"
```

에러 정책: 파일 없음 → 빈 config, TOML 문법 오류 → warning + 빈 config, 타입 오류 → 해당 필드 무시

### 훅 사용

훅은 파이프라인 이벤트(`on_transcode`, `on_merge`, `on_upload`, `on_error`)에 따라
실행됩니다. `--run-hook`은 특정 이벤트 훅을 즉시 수동 실행합니다.

```bash
# 훅 이벤트 수동 실행
tubearchive --run-hook on_upload --config /path/to/config.toml
```

훅 실행 환경 변수:

- `TUBEARCHIVE_OUTPUT_PATH`: 이벤트 대상 출력 경로
- `TUBEARCHIVE_YOUTUBE_ID`: 업로드 이벤트의 YouTube ID. 분할 업로드 시에는 여러 ID가 세미콜론(`;`)으로 구분되어 전달됩니다.
- `TUBEARCHIVE_INPUT_PATHS`: `;`로 연결한 입력 경로 목록
- `TUBEARCHIVE_INPUT_COUNT`: 입력 파일 개수
- `TUBEARCHIVE_ERROR_MESSAGE`: `on_error` 예외 메시지

### 작업 현황 조회

```bash
# 전체 작업 현황 (트랜스코딩, 병합, 업로드)
tubearchive --status

# 특정 작업 상세 조회 (merge_job ID)
tubearchive --status-detail 1
```

### 통계 대시보드

```bash
# 전체 처리 통계 (트랜스코딩, 병합, 기기별 분포, 아카이브)
tubearchive --stats

# 특정 기간 통계 (연-월)
tubearchive --stats --period "2026-01"
```

### 프로젝트 관리

여행, 이벤트 등 여러 날에 걸친 촬영을 하나의 프로젝트로 묶어 관리할 수 있습니다.

```bash
# 병합 결과를 프로젝트에 자동 연결 (프로젝트가 없으면 생성)
tubearchive --project "제주도 여행" ~/Videos/Day1/
tubearchive --project "제주도 여행" ~/Videos/Day2/

# 프로젝트 목록 조회
tubearchive --project-list

# 프로젝트 상세 조회 (날짜별 영상 그룹핑, 업로드 상태)
tubearchive --project-detail 1

# JSON 형식 출력
tubearchive --project-list --json
tubearchive --project-detail 1 --json
```

프로젝트에 영상을 추가하면 날짜 범위가 자동으로 갱신됩니다. `--upload` 옵션과 함께 사용하면 프로젝트 전용 YouTube 플레이리스트가 자동 생성됩니다.

### 메타데이터 카탈로그

DB에 저장된 영상 메타데이터를 날짜/기기/상태별로 조회합니다.

```bash
# 전체 영상 목록 (기기별 그룹핑)
tubearchive --catalog

# 날짜 패턴 검색
tubearchive --search "2026-01"

# 기기 필터 + 상태 필터
tubearchive --search --device "GoPro" --status completed

# JSON / CSV 출력
tubearchive --catalog --json
tubearchive --search "2026-01" --csv
```

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

# 썸네일을 지정해 업로드
tubearchive --set-thumbnail /path/to/cover.jpg --upload-only merged_output.mp4

# 제목 지정
tubearchive --upload-only video.mp4 --upload-title "나의 여행 영상"

# 공개 설정 변경 (기본: unlisted)
tubearchive --upload-only video.mp4 --upload-privacy public

# 분할 + 업로드 (분할 파일별 챕터 리매핑 + "Part N/M" 제목)
tubearchive --split-duration 1h --upload ~/Videos/
```

#### 업로드 옵션

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `--upload` | 병합 완료 후 YouTube에 업로드 | - |
| `--upload-only FILE` | 지정된 파일을 YouTube에 업로드 (병합 없이) | - |
| `--upload-title TITLE` | 영상 제목 | 파일명 또는 디렉토리명 |
| `--set-thumbnail PATH` | 업로드 썸네일 이미지 경로 (jpg/jpeg/png) | - |
| `--upload-privacy` | 공개 설정 (public/unlisted/private) | unlisted |
| `--upload-chunk MB` | 업로드 청크 크기 MB (1-256) | 32 |
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

### 환경 변수

| 환경 변수 | 설명 | 기본값 |
|-----------|------|--------|
| `TUBEARCHIVE_OUTPUT_DIR` | 기본 출력 디렉토리 | 출력 파일과 같은 위치 |
| `TUBEARCHIVE_DB_PATH` | 데이터베이스 파일 경로 | `~/.tubearchive/tubearchive.db` |
| `TUBEARCHIVE_PARALLEL` | 병렬 트랜스코딩 수 | 1 (순차 처리) |
| `TUBEARCHIVE_DENOISE` | 오디오 노이즈 제거 (true/false) | false |
| `TUBEARCHIVE_DENOISE_LEVEL` | 노이즈 제거 강도 (light/medium/heavy) | medium |
| `TUBEARCHIVE_NORMALIZE_AUDIO` | EBU R128 loudnorm 정규화 (true/false) | true |
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
| `TUBEARCHIVE_AUTO_LUT` | 기기별 자동 LUT 매칭 (true/false) | false |
| `TUBEARCHIVE_YOUTUBE_CLIENT_SECRETS` | OAuth 클라이언트 시크릿 경로 | `~/.tubearchive/client_secrets.json` |
| `TUBEARCHIVE_YOUTUBE_TOKEN` | OAuth 토큰 저장 경로 | `~/.tubearchive/youtube_token.json` |
| `TUBEARCHIVE_YOUTUBE_PLAYLIST` | 기본 플레이리스트 ID (쉼표로 여러 개 지정) | - |
| `TUBEARCHIVE_UPLOAD_CHUNK_MB` | 업로드 청크 크기 MB (1-256) | 32 |

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

트랜스코딩 중 생성되는 임시 파일은 `/tmp/tubearchive/{uuid8}/`에 저장됩니다.
- 실행마다 UUID 서브디렉토리를 생성하여 **동시 실행 시 서로 간섭하지 않음**
- **작업 완료 시 자동 삭제** (해당 실행의 서브디렉토리만 정리)
- 시스템 재부팅 시에도 자동 정리
- `--keep-temp` 옵션으로 임시 파일 보존 가능 (디버깅용)

## 프로젝트 구조

```
tubearchive/
├── __init__.py           # 버전 정보
├── __main__.py           # python -m 진입점
├── config.py             # TOML 설정 파일 관리 (환경변수 Shim)
├── app/                  # CLI 진입점 및 오케스트레이션
│   ├── cli/
│   │   └── main.py       # 파서·검증·파이프라인·업로드 라우팅
│   └── queries/
│       ├── catalog.py    # 메타데이터 카탈로그/검색 CLI
│       ├── project.py    # 프로젝트 관리 CLI (목록/상세 조회)
│       └── stats.py      # 통계 대시보드 CLI
├── domain/               # 순수 비즈니스 로직
│   ├── media/
│   │   ├── scanner.py    # 파일 스캔 (3가지 케이스)
│   │   ├── grouper.py    # 연속 파일 시퀀스 그룹핑 (GoPro/DJI)
│   │   ├── ordering.py   # 파일 정렬/필터링/수동 재배열
│   │   ├── transcoder.py # 트랜스코딩 엔진 (Resume 지원)
│   │   ├── merger.py     # concat 병합 (codec copy, 샘플레이트·길이 검증)
│   │   ├── splitter.py   # 영상 분할 (segment muxer, 재인코딩 없음)
│   │   ├── timelapse.py  # 타임랩스 생성 (배속/해상도/오디오)
│   │   └── archiver.py   # 원본 파일 아카이브 관리
│   └── models/
│       ├── video.py      # VideoFile, VideoMetadata, FadeConfig
│       └── job.py        # TranscodingJob, MergeJob, SplitJob, Project, JobStatus
├── infra/                # 외부 시스템 연동
│   ├── ffmpeg/
│   │   ├── executor.py   # FFmpeg 실행 및 진행률
│   │   ├── effects.py    # 필터 (Portrait, Fade, Loudnorm, Denoise, Vidstab, BGM, LUT, Timelapse, Silence)
│   │   ├── profiles.py   # 기기별 인코딩 프로파일 (-ar 48000 고정)
│   │   └── thumbnail.py  # 썸네일 추출
│   ├── db/
│   │   ├── schema.py     # SQLite 스키마
│   │   ├── repository.py # CRUD Repository (Video/TranscodingJob/MergeJob/Split/Archive/Project)
│   │   └── resume.py     # Resume 상태 추적
│   ├── youtube/
│   │   ├── auth.py       # OAuth 2.0 인증
│   │   ├── uploader.py   # YouTube 업로드 (Resumable)
│   │   └── playlist.py   # 플레이리스트 관리
│   └── notification/     # 훅 이벤트 알림
└── shared/               # 공통 유틸리티
    ├── validators.py     # 입력 검증
    ├── progress.py       # 진행률 표시 (MultiProgressBar)
    ├── summary_generator.py  # 요약 파일 / YouTube 챕터 생성
    └── temp_manager.py   # 임시 파일 관리
```

## 개발

### 테스트 실행

```bash
# 전체 테스트
uv run pytest tests/ -v

# 특정 테스트
uv run pytest tests/unit/test_scanner.py -v

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
