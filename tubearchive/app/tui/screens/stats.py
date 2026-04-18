"""Stats 탭 화면.

DB에서 집계 통계를 조회하여 섹션별로 표시한다.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.widgets import Label, Static

from tubearchive.infra.db import database_session
from tubearchive.shared.progress import format_size


def _fmt_dur(seconds: float) -> str:
    """초를 'Xh Ym Zs' 형식으로 변환한다."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class StatsPane(Static):
    """통계 대시보드 패널."""

    def compose(self) -> ComposeResult:
        with ScrollableContainer():
            yield Vertical(id="stats-content")

    def on_mount(self) -> None:
        self.load_data()

    def load_data(self) -> None:
        """DB에서 통계 데이터를 조회하고 화면을 갱신한다."""
        try:
            from tubearchive.app.queries.stats import fetch_stats

            with database_session() as conn:
                data = fetch_stats(conn)
            self._render(data)
        except Exception as exc:
            container = self.query_one("#stats-content", Vertical)
            container.remove_children()
            container.mount(Label(f"[red]데이터 로드 실패: {exc}[/]"))

    def _render(self, data: object) -> None:  # type: ignore[override]
        from tubearchive.app.queries.stats import StatsData

        if not isinstance(data, StatsData):
            return

        container = self.query_one("#stats-content", Vertical)
        container.remove_children()

        lines: list[str] = []

        # 전체 요약
        lines.append("[bold $accent]── 전체 요약 ──[/]")
        lines.append(f"  등록 영상     {data.total_videos:,}개")
        lines.append(f"  총 재생 시간  {_fmt_dur(data.total_duration)}")
        lines.append("")

        # 트랜스코딩
        tc = data.transcoding
        lines.append("[bold $accent]── 트랜스코딩 ──[/]")
        lines.append(f"  전체     {tc.total:,}")
        lines.append(f"  완료     {tc.completed:,}")
        lines.append(f"  실패     {tc.failed:,}")
        lines.append(f"  대기     {tc.pending:,}")
        if tc.avg_encoding_speed is not None:
            lines.append(f"  평균 속도  {tc.avg_encoding_speed:.1f}x")
        lines.append("")

        # 병합
        mg = data.merge
        lines.append("[bold $accent]── 병합 ──[/]")
        lines.append(f"  전체     {mg.total:,}")
        lines.append(f"  완료     {mg.completed:,}")
        lines.append(f"  업로드   {mg.uploaded:,}")
        lines.append(f"  출력 크기  {format_size(mg.total_size_bytes)}")
        if mg.total_duration:
            lines.append(f"  총 길이    {_fmt_dur(mg.total_duration)}")
        lines.append("")

        # 아카이브
        ar = data.archive
        if ar.moved or ar.deleted:
            lines.append("[bold $accent]── 아카이브 ──[/]")
            lines.append(f"  이동됨   {ar.moved:,}개")
            lines.append(f"  삭제됨   {ar.deleted:,}개")
            lines.append("")

        # 기기별 분포
        if data.devices:
            lines.append("[bold $accent]── 기기별 분포 ──[/]")
            total_dev = sum(d.count for d in data.devices)
            bar_max = 30
            for dev in data.devices:
                filled = int(bar_max * dev.count / total_dev) if total_dev else 0
                bar = "█" * filled + "░" * (bar_max - filled)
                pct = dev.count / total_dev * 100 if total_dev else 0
                name = (dev.device or "미상")[:20]
                lines.append(f"  {name:<20} {bar} {dev.count:4}개 ({pct:.0f}%)")

        container.mount(Static("\n".join(lines)))
