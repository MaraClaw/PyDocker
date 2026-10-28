import os
import signal
import sys
from collections.abc import Buffer
from contextlib import suppress
from subprocess import Popen
from time import monotonic
from types import FrameType

import pytest

from pywhales import _stdin_pump, utils
from pywhales._stdin_pump import StdinPump
from pywhales.client_config import ClientConfig
from pywhales.telemetry import CompletionRecord, TelemetryRecord
from pywhales.telemetry._lifecycle import ExecutionTelemetry


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX signals")
@pytest.mark.parametrize("timeout", [None, 10.0])
@pytest.mark.parametrize("original", [ValueError("signal"), KeyboardInterrupt()])
def test_signal_interrupt_kills_and_reaps_measured_input_before_completion(
    timeout: float | None,
    original: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given a child that signals after accepting input, then stays alive.
    records: list[TelemetryRecord] = []
    pumps: list[StdinPump] = []
    children: list[Popen[bytes]] = []
    written: list[int] = []
    write = os.write
    config = ClientConfig(
        client_call=[sys.executable], telemetry_observer=records.append
    )

    def pump(
        process: Popen[bytes], data: bytes | None, telemetry: ExecutionTelemetry
    ) -> StdinPump:
        result = StdinPump(process, data, telemetry)
        pumps.append(result)
        children.append(process)
        return result

    def measure(fd: int, data: Buffer) -> int:
        count = write(fd, data)
        written.append(count)
        return count

    def interrupt(signum: int, frame: FrameType | None) -> None:
        raise original

    monkeypatch.setattr(utils, "StdinPump", pump)
    monkeypatch.setattr(_stdin_pump.os, "write", measure)
    previous = signal.signal(signal.SIGALRM, interrupt)
    code = (
        "import os,signal,time; os.read(0,1); "
        "os.kill(os.getppid(),signal.SIGALRM); time.sleep(6)"
    )
    started = monotonic()
    try:
        # When a real signal raises through communicate, with or without a deadline.
        with pytest.raises(type(original)) as raised:
            utils.run(
                [*config.docker_cmd, "-c", code],
                input=b"x" * (2 * 1024 * 1024),
                timeout=timeout,
            )

        # Then the same exception returns promptly after all local cleanup.
        assert raised.value is original
        assert monotonic() - started < 3
        child = children[0]
        assert child.returncode == -signal.SIGKILL
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
        assert all(
            pipe.closed
            for pipe in (child.stdin, child.stdout, child.stderr)
            if pipe is not None
        )
        writer = pumps[0]._thread
        assert writer is not None and not writer.is_alive()
        assert len(records) == 2
        completed = records[-1]
        assert isinstance(completed, CompletionRecord)
        assert completed.outcome == "failure"
        assert completed.return_code == child.returncode
        assert completed.stdin_bytes == sum(written) > 0
    finally:
        signal.signal(signal.SIGALRM, previous)
        for child in children:
            with suppress(ProcessLookupError):
                child.kill()
            child.wait()
