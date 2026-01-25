# TubeArchive CLI - Claude 작업 지침

## 프로젝트 개요
- **목적**: 다양한 기기의 4K 영상을 HEVC 10-bit로 표준화하여 병합
- **핵심 제약**: Mac Studio M2 Max VideoToolbox 가속 필수
- **언어**: Python 3.14+ (Type Hints 엄격)

## 필수 준수 사항

### 1. FFmpeg 명령어 검증
- 모든 FFmpeg 필터 체인은 구현 전 CLI로 사전 검증
- hevc_videotoolbox 인코더 필수 사용 (libx265는 폴백만)
- 필터 문법 오류 시 단위 테스트로 검증

### 2. Resume 무결성
- 모든 DB 상태 변경은 트랜잭션 사용
- progress_percent는 0-100 범위 검증
- status 필드는 ENUM 제약 준수

### 3. 테스트 우선 개발
- 새 함수 작성 시 단위 테스트 먼저 작성 (RED)
- 테스트 통과 후에만 리팩터링 (GREEN → REFACTOR)
- 최소 70% 테스트 커버리지 유지

### 4. 파일 생성 시간 정렬
- macOS stat.st_birthtime 사용 (st_ctime 아님)
- 다른 플랫폼에서는 st_mtime 폴백 고려

### 5. 에러 처리
- FFmpeg 실패 시 stderr 전체 로그 저장
- VideoToolbox 실패 → libx265 폴백 자동 전환
- 손상된 파일은 스킵 (전체 작업 중단 금지)

## 금지 사항
- ❌ poetry 명령어 사용 (uv만 사용)
- ❌ Type Hints 생략
- ❌ FFmpeg 필터 문법 미검증
- ❌ 하드코딩된 경로 (모두 Path 객체 사용)
- ❌ print() 사용 (logger 사용)

## 커밋 메시지 규칙
```
<type>: <subject>

<body>

# Type:
# - feat: 새 기능
# - fix: 버그 수정
# - refactor: 리팩터링
# - test: 테스트 추가
# - docs: 문서 수정

# 예시:
feat: Nikon N-Log 메타데이터 감지 구현

ffprobe JSON 출력에서 color_transfer=smpte2084 감지
Rec.2020 컬러 스페이스 유지하도록 프로파일 설정
```

## 개발 워크플로우
1. **기능 브랜치 생성**: `git checkout -b feat/scanner`
2. **테스트 작성**: `tests/test_scanner.py`
3. **구현**: `tubearchive/core/scanner.py`
4. **테스트 실행**: `uv run pytest tests/test_scanner.py -v`
5. **커밋**: `git commit -m "feat: 파일 스캔 및 생성 시간 정렬 구현"`
6. **main 병합**: `git checkout main && git merge feat/scanner`

## 유용한 명령어
```bash
# 의존성 추가
uv add <package>
uv add --dev <package>

# 테스트 실행
uv run pytest tests/ -v
uv run pytest tests/test_scanner.py::test_sorts_by_creation_time -v

# 타입 체크
uv run mypy tubearchive/

# 린트
uv run ruff check tubearchive/
uv run ruff format tubearchive/

# CLI 실행
uv run tubearchive --help
uv run python -m tubearchive tests/fixtures/
```

## FFmpeg 필터 디버깅
```bash
# 세로 영상 레이아웃 테스트
ffmpeg -i tests/fixtures/portrait.mov \
  -filter_complex "[0:v]split=2[bg][fg];[bg]scale=3840:2160:force_original_aspect_ratio=increase,crop=3840:2160,boxblur=20:1[bg_blur];[fg]scale=2160:-1[fg_scaled];[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2" \
  -c:v hevc_videotoolbox -b:v 10M -t 5 test_output.mp4

# 메타데이터 확인
ffprobe -v quiet -print_format json -show_streams -show_format input.mov
```

## 성능 목표
- **4K 60fps 10분 영상**: 트랜스코딩 < 5분
- **병합 속도**: 10개 클립 < 10초 (codec copy)
- **Resume 오버헤드**: < 1%
