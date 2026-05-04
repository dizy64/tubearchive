"""watch.py 단위 테스트."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

import pytest


class TestWaitForStableFile:
    """_wait_for_stable_file: 파일 크기 안정화 로직."""

    def test_checks_zero_returns_immediately(self, tmp_path: Path) -> None:
        from tubearchive.app.cli.watch import _wait_for_stable_file

        f = tmp_path / "v.mp4"
        f.write_bytes(b"x" * 100)
        result = _wait_for_stable_file(f, checks=0, interval=0.0, stop_event=Event())
        assert result is True

    def test_returns_false_when_file_missing(self) -> None:
        from tubearchive.app.cli.watch import _wait_for_stable_file

        result = _wait_for_stable_file(
            Path("/nonexistent/path.mp4"),
            checks=2,
            interval=0.0,
            stop_event=Event(),
        )
        assert result is False

    def test_returns_true_when_size_stable(self, tmp_path: Path) -> None:
        from tubearchive.app.cli.watch import _wait_for_stable_file

        f = tmp_path / "v.mp4"
        f.write_bytes(b"x" * 100)
        result = _wait_for_stable_file(f, checks=2, interval=0.0, stop_event=Event())
        assert result is True

    def test_returns_false_when_stop_event_set(self, tmp_path: Path) -> None:
        from tubearchive.app.cli.watch import _wait_for_stable_file

        f = tmp_path / "v.mp4"
        f.write_bytes(b"x" * 100)
        stop = Event()
        stop.set()
        result = _wait_for_stable_file(f, checks=5, interval=0.0, stop_event=stop)
        assert result is False

    def test_resets_count_when_size_changes(self, tmp_path: Path) -> None:
        """파일 크기가 바뀌면 안정화 카운터가 리셋되어야 한다."""
        from tubearchive.app.cli.watch import _wait_for_stable_file

        f = tmp_path / "v.mp4"
        f.write_bytes(b"x" * 50)

        call_count = 0
        _orig_stat = Path.stat

        def _patched_stat(self_path: Path, *, follow_symlinks: bool = True) -> object:
            nonlocal call_count
            if self_path != f:
                return _orig_stat(self_path, follow_symlinks=follow_symlinks)
            call_count += 1
            result = _orig_stat(self_path, follow_symlinks=follow_symlinks)
            # 처음 두 번은 크기가 다르게 보이도록 패치
            if call_count <= 2:
                result_mock = MagicMock()
                result_mock.st_size = call_count * 100
                return result_mock
            return result

        with patch.object(Path, "stat", _patched_stat):
            stop = Event()
            # interval=0 이므로 바로 진행
            result = _wait_for_stable_file(f, checks=2, interval=0.0, stop_event=stop)

        # 크기가 변했다가 안정화되므로 True
        assert result is True


class TestSetupFileObserver:
    """_setup_file_observer: watchdog observer 생성."""

    def test_raises_runtime_error_without_watchdog(self, tmp_path: Path) -> None:
        import builtins

        from tubearchive.app.cli.watch import _setup_file_observer

        real_import = builtins.__import__

        def mock_import(name: str, *args: object, **kwargs: object) -> object:
            if name == "watchdog.events" or name == "watchdog.observers":
                raise ModuleNotFoundError("No module named 'watchdog'")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=mock_import),
            pytest.raises(RuntimeError, match="watchdog"),
        ):
            _setup_file_observer([tmp_path], lambda p: None)

    def test_returns_observer_and_handler(self, tmp_path: Path) -> None:
        from tubearchive.app.cli.watch import _setup_file_observer

        mock_observer = MagicMock()
        mock_observer_class = MagicMock(return_value=mock_observer)

        with (
            patch("tubearchive.app.cli.watch.Observer", mock_observer_class, create=True),
            patch("watchdog.observers.Observer", mock_observer_class, create=True),
            patch("watchdog.events.FileSystemEventHandler", MagicMock, create=True),
        ):
            try:
                observer, _handler = _setup_file_observer([tmp_path], lambda p: None)
                assert observer is mock_observer
                mock_observer.start.assert_called_once()
            except (ImportError, AttributeError):
                pytest.skip("watchdog not available")


class TestRunWatchPipeline:
    """_run_watch_pipeline: 단일 파일 파이프라인 실행."""

    @pytest.fixture
    def base_validated(self, tmp_path: Path) -> object:
        from tubearchive.app.cli.validators import ValidatedArgs

        return ValidatedArgs(
            targets=[tmp_path / "dummy.mp4"],
            output=None,
            output_dir=None,
            no_resume=False,
            keep_temp=False,
            dry_run=False,
            watch=True,
            watch_paths=[tmp_path],
            upload=False,
        )

    @patch("tubearchive.app.cli.watch.run_pipeline")
    def test_calls_run_pipeline_with_watch_false(
        self,
        mock_pipeline: MagicMock,
        tmp_path: Path,
        base_validated: object,
    ) -> None:
        from tubearchive.app.cli.watch import _run_watch_pipeline

        mock_pipeline.return_value = tmp_path / "output.mp4"

        args = MagicMock()
        test_file = tmp_path / "clip.mp4"
        test_file.touch()

        _run_watch_pipeline(test_file, args, base_validated)

        mock_pipeline.assert_called_once()
        call_args = mock_pipeline.call_args[0][0]
        assert call_args.watch is False
        assert call_args.targets == [test_file]

    @patch("tubearchive.app.cli.watch._upload_after_pipeline")
    @patch("tubearchive.app.cli.watch.run_pipeline")
    def test_calls_upload_when_upload_flag_set(
        self,
        mock_pipeline: MagicMock,
        mock_upload: MagicMock,
        tmp_path: Path,
        base_validated: object,
    ) -> None:
        from tubearchive.app.cli.watch import _run_watch_pipeline

        output = tmp_path / "output.mp4"
        mock_pipeline.return_value = output

        args = MagicMock()
        validated = dataclasses.replace(base_validated, upload=True)  # type: ignore[arg-type]
        test_file = tmp_path / "clip.mp4"
        test_file.touch()

        _run_watch_pipeline(test_file, args, validated)

        mock_upload.assert_called_once()
        assert mock_upload.call_args[1]["output_path"] == output

    @patch("tubearchive.app.cli.watch.run_pipeline")
    def test_no_upload_when_upload_flag_false(
        self,
        mock_pipeline: MagicMock,
        tmp_path: Path,
        base_validated: object,
    ) -> None:
        from tubearchive.app.cli.watch import _run_watch_pipeline

        mock_pipeline.return_value = tmp_path / "output.mp4"

        args = MagicMock()
        test_file = tmp_path / "clip.mp4"
        test_file.touch()

        with patch("tubearchive.app.cli.watch._upload_after_pipeline") as mock_upload:
            _run_watch_pipeline(test_file, args, base_validated)

        mock_upload.assert_not_called()
