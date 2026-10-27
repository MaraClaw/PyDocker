import errno
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Event, Thread
from typing import TypedDict
from unittest.mock import patch

import pytest

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
