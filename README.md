# TubeArchive CLI (Project Komorebi)

다양한 기기(Nikon, GoPro, DJI, iPhone)의 4K 영상을 HEVC 10-bit로 표준화하여 병합하는 CLI 도구.

## 주요 기능

- ✅ 스마트 파일 스캔 (3가지 케이스: 현재 디렉토리 / 특정 파일 / 디렉토리)
- ✅ 세로 영상 자동 레이아웃 (블러 배경 + 중앙 전경)
- ✅ Resume 기능 (중단된 작업 이어서 실행)
- ✅ VideoToolbox 하드웨어 가속 (Mac M1/M2)
- ✅ 기기별 자동 감지 및 표준화

## 요구사항

- macOS (VideoToolbox 필수)
- Python 3.14+
- FFmpeg 8.0+ (VideoToolbox 지원)
- asdf, uv

## 설치

```bash
# asdf로 Python 설정
asdf install python 3.14.2
echo "python 3.14.2" > .tool-versions

# uv로 의존성 설치
uv sync
```

## 사용법

```bash
# 현재 디렉토리 모든 영상 병합
uv run tubearchive

# 특정 파일들만 병합
uv run tubearchive video1.mp4 video2.mov

# 특정 디렉토리 영상 병합
uv run tubearchive ~/Videos/Trip2024/ --output merged.mp4
```

## 개발

```bash
# 테스트 실행
uv run pytest tests/ -v

# 타입 체크
uv run mypy tubearchive/

# 린트
uv run ruff check tubearchive/
```

## 라이선스

MIT
