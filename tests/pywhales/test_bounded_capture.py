import errno
import os
import select
import signal
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Thread
from typing import TypedDict, override
from unittest.mock import patch

import pytest

import pywhales._readiness
from pywhales._capture_pipes import CapturePipes
from pywhales._observation import observation
from pywhales.client_config import ClientConfig
from pywhales.exceptions import (
    DockerTimeout,
    NoSuchContainer,
    ObservationCancelled,
    ObservationLimitError,
    ObservationTimeout,
)
from pywhales.inventory import InspectionPolicy
from pywhales.utils import run


def policy(response: int = 1024, argv: int = 65536) -> InspectionPolicy:
    return InspectionPolicy(
        max_references=1, max_argv_bytes=argv, max_response_bytes=response
    )


class RunOptions(TypedDict, total=False):
    tty: bool
    capture_stdout: bool
    capture_stderr: bool
    input: bytes


def test_combined_streams_at_exact_limit_and_decode() -> None:
    with observation(policy(8)):
        result = run(
            [
                sys.executable,
                "-c",
                "import os; os.write(1,b'ab\\xff\\n'); os.write(2,b'err!')",
            ],
            return_stderr=True,
        )
    assert result == ("ab\ufffd", "err!")


@pytest.mark.parametrize("descriptor", [1, 2])
def test_unterminated_output_over_limit_is_rejected(descriptor: int) -> None:
    with observation(policy(8)), pytest.raises(ObservationLimitError) as failure:
        run([sys.executable, "-c", f"import os; os.write({descriptor}, b'x'*9)"])
    assert (failure.value.reason, failure.value.limit, failure.value.measured) == (
        "response",
        8,
        9,
    )


def test_stdout_and_stderr_share_the_budget() -> None:
    with observation(policy(8)), pytest.raises(ObservationLimitError):
        run(
            [
                sys.executable,
                "-c",
                "import os; os.write(1,b'12345'); os.write(2,b'6789')",
            ]
        )


def test_effective_environment_is_checked_before_launch() -> None:
    with (
        observation(policy()),
        pytest.raises(ObservationLimitError) as failure,
    ):
        run([sys.executable, "-c", "pass"], env={"PRIVATE": "secret\0value"})
    assert failure.value.reason == "environment"
    assert "secret" not in str(failure.value)


@pytest.mark.parametrize("layer", ["client", "operation"])
def test_invalid_environment_during_observation_is_safe_and_never_launches(
    layer: str,
) -> None:
    config = ClientConfig(client_call=[sys.executable])
    command = [*config.docker_cmd, "-c", "pass"]
    invalid = {"PRIVATE": "secret\0value"}
    if layer == "client":
        config.environment = invalid
    with (
        patch("pywhales._bounded_capture.subprocess.Popen") as launch,
        observation(policy()),
        pytest.raises(ObservationLimitError) as failure,
    ):
        run(command, env=invalid if layer == "operation" else {})
    assert failure.value.reason == "environment"
    assert "secret" not in "".join(traceback.format_exception(failure.value))
    launch.assert_not_called()


@pytest.mark.parametrize(
    "options",
    [
        {"tty": True},
        {"capture_stdout": False},
        {"capture_stderr": False},
        {"input": b""},
    ],
)
def test_unsupported_modes_fail_before_launch(options: RunOptions) -> None:
    with observation(policy()), pytest.raises(ValueError):
        run(["does-not-exist"], **options)


def test_pre_cancelled_scope_never_launches(tmp_path: Path) -> None:
    marker = tmp_path / "launched"
    cancel = Event()
    with pytest.raises(ObservationCancelled), observation(policy(), cancel=cancel):
        cancel.set()
        run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path(__import__('sys').argv[1]).touch()",
                marker,
            ]
        )
    assert not marker.exists()


def test_expired_scope_never_launches(tmp_path: Path) -> None:
    marker = tmp_path / "launched"
    with pytest.raises(ObservationTimeout), observation(policy(), deadline=0):
        run(
            [
                sys.executable,
                "-c",
                "from pathlib import Path; Path(__import__('sys').argv[1]).touch()",
                marker,
            ]
        )
    assert not marker.exists()


def test_cancellation_interrupts_read_and_reaps_child() -> None:
    cancel = Event()
    processes: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def launch(
        args: list[str],
        *,
        stdout: int,
        stderr: int,
        env: Mapping[str, str],
        pass_fds: Sequence[int],
    ) -> subprocess.Popen[bytes]:
        process = popen(args, stdout=stdout, stderr=stderr, env=env, pass_fds=pass_fds)
        processes.append(process)
        return process

    timer = Thread(target=lambda: (time.sleep(0.3), cancel.set()))
    timer.start()
    try:
        with (
            patch("pywhales._bounded_capture.subprocess.Popen", side_effect=launch),
            pytest.raises(ObservationCancelled),
            observation(policy(), cancel=cancel),
        ):
            run([sys.executable, "-c", "import threading; threading.Event().wait()"])
    finally:
        timer.join()
    assert len(processes) == 1
    process = processes[0]
    assert process.returncode is not None
    assert process.stdout is not None and process.stdout.closed
    assert process.stderr is not None and process.stderr.closed


@pytest.mark.parametrize("selected_first", [True, False])
def test_earliest_deadline_wins(selected_first: bool) -> None:
    expected = DockerTimeout if selected_first else ObservationTimeout
    with (
        pytest.raises(expected),
        observation(
            policy(), deadline=time.monotonic() + (2 if selected_first else 0.2)
        ),
    ):
        run(
            [sys.executable, "-c", "import threading; threading.Event().wait()"],
            timeout=0.2 if selected_first else 2,
        )


@pytest.mark.parametrize("raw", [False, True])
def test_selected_timeout_preserves_partial_output_and_diagnostics(raw: bool) -> None:
    secret = "partial-secret"
    config = ClientConfig(client_call=[sys.executable], raw_diagnostics=raw)
    with pytest.raises(DockerTimeout) as failure, observation(policy()):
        run(
            [
                *config.docker_cmd,
                "-c",
                f"import os,threading; os.write(1,{secret.encode()!r}); threading.Event().wait()",
            ],
            timeout=0.5,
        )
    assert failure.value.stdout == secret
    assert (secret in str(failure.value)) is raw


@pytest.mark.parametrize("raw", [False, True])
def test_failure_preserves_classification_and_diagnostics(raw: bool) -> None:
    secret = "No such container: secret-value"
    config = ClientConfig(client_call=[sys.executable], raw_diagnostics=raw)
    with observation(policy()), pytest.raises(NoSuchContainer) as failure:
        run(
            [
                *config.docker_cmd,
                "-c",
                f"import sys; sys.stderr.write({secret!r}); sys.exit(7)",
            ]
        )
    assert failure.value.stderr == secret
    assert (secret in str(failure.value)) is raw


def test_native_e2big_is_translated_without_cause() -> None:
    with (
        observation(policy()),
        patch(
            "pywhales._bounded_capture.subprocess.Popen",
            side_effect=OSError(errno.E2BIG, "secret"),
        ),
        pytest.raises(ObservationLimitError) as failure,
    ):
        run([sys.executable, "-c", "pass"])
    assert failure.value.reason == "native_argv"
    assert failure.value.__cause__ is None
    assert "secret" not in str(failure.value)


@pytest.mark.skipif(os.name == "nt", reason="POSIX fork and inherited descriptors")
@pytest.mark.parametrize("timeout", [None, 0.1])
def test_scope_deadline_interrupts_descendant_pipe_after_direct_child_exit(
    timeout: float | None,
) -> None:
    release_read, release_write = os.pipe()
    status_read, status_write = os.pipe()
    try:
        code = (
            "import os,sys\n"
            "release,status=map(int,sys.argv[1:])\n"
            "if os.fork()==0:\n"
            " os.write(status,b'R'); os.read(release,1); os._exit(0)\n"
            "os._exit(0)\n"
        )
        with (
            pytest.raises(ObservationTimeout),
            observation(policy(), deadline=time.monotonic() + 0.5),
        ):
            run(
                [sys.executable, "-c", code, release_read, status_write],
                pass_fds=(release_read, status_write),
                timeout=timeout,
            )
        assert os.read(status_read, 1) == b"R"
    finally:
        os.close(release_read)
        os.close(status_write)
        os.close(release_write)
        assert os.read(status_read, 1) == b""
        os.close(status_read)


@dataclass(frozen=True)
class BackgroundCapture:
    """A bounded capture running in its own thread and its eventual outcome."""

    thread: Thread
    outcome: list[str | BaseException]


def capture_in_thread(
    args: Sequence[object],
    *,
    pass_fds: Sequence[int] = (),
    timeout: float | None = None,
    cancel: Event | None = None,
) -> BackgroundCapture:
    outcome: list[str | BaseException] = []

    def capture() -> None:
        try:
            with observation(policy(), cancel=cancel):
                outcome.append(run(args, pass_fds=pass_fds, timeout=timeout))
        except BaseException as error:
            outcome.append(error)

    thread = Thread(target=capture, daemon=True)
    thread.start()
    return BackgroundCapture(thread, outcome)


@pytest.fixture
def ready_timeouts(monkeypatch: pytest.MonkeyPatch) -> list[float | None]:
    """Record the timeout of every capture wait."""
    timeouts: list[float | None] = []
    ready = CapturePipes.ready

    def record(self: CapturePipes, timeout: float | None) -> list[tuple[int, int]]:
        timeouts.append(timeout)
        return ready(self, timeout)

    monkeypatch.setattr(CapturePipes, "ready", record)
    return timeouts


_CAPTURE_HOLDER = (
    "import os,sys\n"
    "release,status=map(int,sys.argv[1:3])\n"
    "if os.fork()==0:\n"
    " os.write(status,b'R'); os.read(release,1); os._exit(0)\n"
)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptors")
@pytest.mark.parametrize("scenario", ["eof_before_exit", "exit_before_eof"])
def test_capture_notices_exit_independently_of_pipe_eof_without_polling(
    scenario: str, ready_timeouts: list[float | None]
) -> None:
    release_read, release_write = os.pipe()
    status_read, status_write = os.pipe()
    eof_first = scenario == "eof_before_exit"
    code = (
        # The child closes both outputs, then lives until released.
        "import os,sys\nrelease,status=map(int,sys.argv[1:3])\n"
        "os.close(1); os.close(2); os.write(status,b'R'); os.read(release,1)\n"
        if eof_first
        # The child exits at once while its descendant holds both outputs.
        else _CAPTURE_HOLDER + "os.write(1,b'done')\nos._exit(0)\n"
    )
    before = set(threading.enumerate())
    try:
        capture = capture_in_thread(
            [sys.executable, "-c", code, release_read, status_write],
            pass_fds=(release_read, status_write),
            timeout=None if eof_first else 0.2,
        )
        assert select.select([status_read], [], [], 3)[0]
        assert os.read(status_read, 1) == b"R"

        # When the selected timeout passes and the state is otherwise idle.
        time.sleep(0.6)
        settled = len(ready_timeouts)
        time.sleep(0.3)

        # Then the capture still waits, untimed and without spinning.
        assert capture.thread.is_alive() and not capture.outcome
        assert len(ready_timeouts) == settled <= 5
        assert ready_timeouts[-1] is None

        # And finishes as soon as the child exits or the descendant lets go.
        os.write(release_write, b"x")
        capture.thread.join(3)
        assert not capture.thread.is_alive()
        assert capture.outcome == ["" if eof_first else "done"]
        assert set(threading.enumerate()) == before
    finally:
        os.write(release_write, b"x")
        for descriptor in (release_read, release_write, status_write):
            os.close(descriptor)
        assert os.read(status_read, 1) == b""
        os.close(status_read)


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptors")
def test_selected_timeout_waits_for_the_remaining_time_in_one_wait(
    ready_timeouts: list[float | None],
) -> None:
    before = set(threading.enumerate())

    with observation(policy()), pytest.raises(DockerTimeout):
        run(
            [sys.executable, "-c", "import threading; threading.Event().wait()"],
            timeout=0.6,
        )

    assert 1 <= len(ready_timeouts) <= 2
    assert ready_timeouts[0] is not None and 0.3 < ready_timeouts[0] <= 0.6
    assert set(threading.enumerate()) == before


def test_cancel_token_keeps_bounded_waits(ready_timeouts: list[float | None]) -> None:
    cancel = Event()
    capture = capture_in_thread(
        [sys.executable, "-c", "import threading; threading.Event().wait()"],
        cancel=cancel,
    )
    time.sleep(0.3)

    cancel.set()
    capture.thread.join(3)

    assert not capture.thread.is_alive()
    assert isinstance(capture.outcome[0], ObservationCancelled)
    assert ready_timeouts
    assert all(timeout is not None and timeout <= 0.05 for timeout in ready_timeouts)


def test_capture_helper_start_failure_unwinds_child_and_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def launch(
        args: list[str],
        *,
        stdout: int,
        stderr: int,
        env: Mapping[str, str],
        pass_fds: Sequence[int],
    ) -> subprocess.Popen[bytes]:
        process = popen(args, stdout=stdout, stderr=stderr, env=env, pass_fds=pass_fds)
        processes.append(process)
        return process

    class FailingThread(Thread):
        @override
        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(pywhales._readiness, "Thread", FailingThread)
    before = set(threading.enumerate())

    with (
        patch("pywhales._bounded_capture.subprocess.Popen", side_effect=launch),
        observation(policy()),
        pytest.raises(RuntimeError),
    ):
        run([sys.executable, "-c", "import threading; threading.Event().wait()"])

    (process,) = processes
    assert process.returncode == -signal.SIGKILL
    assert process.stdout is not None and process.stdout.closed
    assert process.stderr is not None and process.stderr.closed
    assert set(threading.enumerate()) == before
