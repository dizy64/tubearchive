# Sprint 1: Pipeline 피드백 + 알림 통합 설계

**날짜**: 2026-05-04  
**범위**: TUI 파일별 진행률/ETA 표시 + Slack/Telegram/Discord/macOS 알림 통합

---

## 목표

1. TUI 실행 화면에서 파일별 트랜스코딩 진행률(%) + ETA를 실시간으로 표시
2. 파이프라인 완료/오류 시 Slack·Telegram·Discord·macOS 알림 발송
3. `ValidatedArgs`·`run_pipeline` 시그니처를 최소한으로 변경하고, 기존 CLI 동작은 전혀 바꾸지 않는다

---

## 핵심 설계: `PipelineContext`

파이프라인에 사이드-채널 정보(진행률 콜백·알림 전송)를 전달하는 별도 dataclass.

```python
# tubearchive/app/cli/pipeline.py (또는 shared/context.py)

@dataclass
class PipelineContext:
    notifier: Notifier | None = None
    on_progress: Callable[[ProgressEvent], None] | None = None
```

`run_pipeline` 시그니처:

```python
def run_pipeline(
    args: ValidatedArgs,
    context: PipelineContext | None = None,
) -> Path:
```

- `context=None`이면 기존 동작과 완전 동일 (CLI, 테스트 하위 호환)
- `ValidatedArgs`는 변경 없음 → 기존 테스트 패치 경로 전부 유지

---

## ProgressEvent 타입

```python
# tubearchive/app/cli/pipeline.py (pipeline 내부 이벤트)

@dataclass(frozen=True)
class FileStartEvent:
    filename: str
    file_index: int   # 0-based
    total_files: int

@dataclass(frozen=True)
class FileProgressEvent:
    filename: str
    info: ProgressInfo   # shared/progress.py — percent, fps, eta_seconds

@dataclass(frozen=True)
class FileDoneEvent:
    filename: str
    success: bool

ProgressEvent = FileStartEvent | FileProgressEvent | FileDoneEvent
```

`pipeline.py` 내부에서 트랜스코딩 루프 진입/종료 시, 그리고 FFmpeg progress callback에서 `context.on_progress(event)`를 호출.  
`context`가 없으면 호출하지 않으므로 기존 CLI 경로는 완전히 영향 없음.

---

## 알림 통합

TUI에서 파이프라인을 시작할 때 `validated_args.notify` 플래그를 보고 `Notifier`를 조건부 생성:

```python
# screens/pipeline.py — _launch_pipeline()

notifier = None
if validated_args.notify:
    from tubearchive.infra.notification.notifier import Notifier
    from tubearchive.config import load_config
    cfg = load_config()
    notifier = Notifier(cfg.notification)

context = PipelineContext(notifier=notifier, on_progress=on_progress)
```

파이프라인 내부에서 완료/오류 시점에 `context.notifier.notify(event)` 호출.  
`Notifier` 인터페이스(`infra/notification/notifier.py`)는 TUI와 CLI 양쪽에서 모두 생성·전달 가능하다.  
CLI에서도 `validated_args.notify` 플래그가 있으면 `main.py`에서 `Notifier`를 생성해 `PipelineContext`에 주입한다.

---

## TUI 위젯: `FileProgressPanel`

**파일**: `tubearchive/app/tui/widgets/file_progress_panel.py`  
**기존 `ProgressPanel` 교체** (삭제 후 신규 작성)

### 레이아웃

```text
┌ 진행 중: 3개 파일 ──────────────────────────────────┐
│  →  clip_001.mov   ████████░░░░  45%  ETA 2:30     │
│  ·  clip_002.mov                                   │
│  ✓  clip_003.mov                       완료         │
│                                                    │
│  전체 ████████████░░░░░░░░  1/3 완료 (33%)          │
├────────────────────────────────────────────────────┤
│ INFO  트랜스코딩 시작: clip_001.mov                 │
│ INFO  loudnorm 1st pass...                         │
└────────────────────────────────────────────────────┘
```

아이콘: `·` 대기 / `→` 처리 중 / `✓` 완료 / `✗` 오류

### 공개 API (worker 스레드에서 `call_from_thread` 경유)

```python
class FileProgressPanel(Widget):
    def handle_event(self, event: ProgressEvent) -> None: ...
    def append_log(self, text: str) -> None: ...
    def finish(self, output_path: str) -> None: ...
    def error(self, message: str) -> None: ...
    # start() / finish() / error() — 기존 ProgressPanel과 동일 이름 유지
```

`FileStartEvent` 수신 시 `total_files` 기준으로 `_FileRow` 위젯을 동적으로 추가.  
전체 진행률 바는 `완료 파일 수 / total_files`로 계산.

### worker 연결 (`screens/pipeline.py`)

```python
@work(thread=True, exclusive=True)
def _run_pipeline_worker(self, validated_args, notifier) -> None:
    panel = self.query_one(FileProgressPanel)

    def on_progress(event: ProgressEvent) -> None:
        self.app.call_from_thread(panel.handle_event, event)

    context = PipelineContext(notifier=notifier, on_progress=on_progress)

    writer = _TuiWriter(lambda t: self.app.call_from_thread(panel.append_log, t))
    log_handler = _TuiLogHandler(lambda t: self.app.call_from_thread(panel.append_log, t))
    # ... 기존 redirect_stdout/addHandler 패턴 유지
    output_path = run_pipeline(validated_args, context=context)
    ...
```

---

## 구현 계획 (두 PR 분리)

### PR 1 — 백엔드 (pipeline.py 변경)
- `PipelineContext` dataclass 추가
- `ProgressEvent` 타입 정의
- `run_pipeline` 시그니처에 `context` 파라미터 추가
- 트랜스코딩 루프에서 `FileStartEvent` / `FileDoneEvent` emit
- FFmpeg progress callback에서 `FileProgressEvent` emit
- 완료/오류 시점에 `context.notifier` 호출

### PR 2 — TUI (screens + widgets 변경)
- `FileProgressPanel` 위젯 신규 작성
- `screens/pipeline.py`에서 `ProgressPanel` → `FileProgressPanel` 교체
- `_launch_pipeline()`에서 `notifier` 조건부 생성 후 `PipelineContext`로 전달
- `_run_pipeline_worker` 시그니처에 `notifier` 파라미터 추가

---

## 테스트 전략

| 대상 | 방식 |
|------|------|
| `PipelineContext` + `ProgressEvent` 타입 | 단위 테스트 — 이벤트 순서/내용 검증 |
| `FileProgressPanel.handle_event` | 단위 테스트 — mock ProgressEvent로 UI 상태 검증 (Textual pilot) |
| 알림 발송 | 기존 `test_notification.py` 패턴 — `Notifier` mock |
| 파이프라인 통합 | 기존 E2E 테스트 변경 없음 (`context=None`이면 기존 동작) |

---

## 변경 없는 것 (보장)

- `ValidatedArgs` 필드 — 추가/변경 없음
- 기존 `run_pipeline(args)` 호출 — `context` 기본값 `None`이라 그대로 동작
- test_cli.py patch 경로 — `tubearchive.app.cli.pipeline.run_pipeline` 등 그대로
- CLI 동작 — `notifier` 생성은 TUI 및 CLI 레이어(`main.py`)에서 `validated_args.notify` 플래그로 제어
