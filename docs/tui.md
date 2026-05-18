# TubeArchive TUI 가이드

[README로 돌아가기](../README.md) | [English](tui.en.md) | [日本語](tui.ja.md)

TubeArchive TUI는 파일 선택, 옵션 조정, 진행률 확인, 프로젝트/통계/이력/YouTube 관리를 터미널 안에서 처리하는 대시보드입니다.

## 실행

```bash
# 현재 디렉토리를 기준으로 실행
tubearchive tui

# 특정 촬영 폴더를 열어서 시작
tubearchive tui ~/Videos/Trip2026/

# 개발 환경에서 실행
uv run tubearchive tui ~/Videos/Trip2026/
```

## 탭

| 탭 | 용도 |
|----|------|
| Pipeline | 파일/폴더 선택, 인코딩 옵션 조정, 파이프라인 실행 |
| Projects | 프로젝트 목록과 날짜별 작업 상태 확인 |
| Stats | 전체 처리 통계, 기기별 분포, 아카이브 통계 확인 |
| History | 트랜스코딩/병합/업로드 이력 조회 |
| YouTube | 인증 상태, 플레이리스트, 업로드 옵션 확인 |

## 단축키

| 키 | 동작 |
|----|------|
| `1` | Pipeline 탭 |
| `2` | Projects 탭 |
| `3` | Stats 탭 |
| `4` | History 탭 |
| `5` | YouTube 탭 |
| `r` | 현재 탭 새로고침 |
| `t` | 테마 전환 |
| `q` | 종료 |

## 외부 오디오 선택

Pipeline 탭의 외부 오디오 패널에서 오디오 파일이나 후보 폴더를 선택할 수 있습니다.

| 버튼 | 적용 옵션 | 용도 |
|------|-----------|------|
| 단일 파일 | `--external-audio ... --external-audio-scope single` | 영상 1개에 외부 오디오 파일 1개 적용 |
| 긴 녹음 | `--external-audio ... --external-audio-scope long` | 긴 녹음에서 여러 클립의 구간 자동 매칭 |
| 후보 폴더 | `--external-audio-dir ...` | 폴더 안의 오디오 파일 중 길이/시각 기반 후보 자동 선택 |
