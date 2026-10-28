"""Bounded, interruptible capture of a single local subprocess."""

import errno
import os
import subprocess
from collections.abc import Mapping, Sequence
from time import monotonic

from pywhales._capture_pipes import CapturePipes
from pywhales._execution_policy import ExecutionPolicy
from pywhales._launch_limits import argv_size, check_launch
from pywhales._observation import ObservationState, check_observation
from pywhales.exceptions import (
    DockerTimeout,
    ObservationCancelled,
    ObservationLimitError,
)
from pywhales.telemetry._lifecycle import ExecutionTelemetry


def bounded_capture(
    args: list[str],
    env: Mapping[str, str],
    pass_fds: Sequence[int],
    timeout: float | None,
    policy: ExecutionPolicy,
    observation: ObservationState,
    *,
    telemetry: ExecutionTelemetry,
) -> subprocess.CompletedProcess[bytes]:
    """Launch, capture within the observation budget, and reap the local child."""
    check_observation(observation)
    check_launch(args, env, observation.policy)
    check_observation(observation)
    telemetry.start()
    deadline = None if timeout is None else monotonic() + timeout
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            pass_fds=pass_fds,
        )
    except OSError as error:
        if error.errno == errno.E2BIG or getattr(error, "winerror", None) == 206:
            raise ObservationLimitError("native_argv", None, argv_size(args)) from None
        raise

    assert process.stdout is not None and process.stderr is not None
    stdout = bytearray()
    stderr = bytearray()
    read_bytes = {process.stdout.fileno(): 0, process.stderr.fileno(): 0}
    try:
        buffers = {process.stdout.fileno(): stdout, process.stderr.fileno(): stderr}
        with CapturePipes((process.stdout, process.stderr)) as pipes:
            while True:
                if any(token.is_set() for token in observation.cancel_tokens):
                    raise ObservationCancelled()
                now = monotonic()
                if (
                    deadline is not None
                    and now >= deadline
                    and (
                        observation.deadline is None or deadline < observation.deadline
                    )
                ):
                    if process.poll() is None:
                        process.kill()
                        process.wait()
                        raise DockerTimeout(
                            args,
                            process.returncode,
                            bytes(stdout),
                            bytes(stderr),
                            raw_diagnostics=policy.raw_diagnostics,
                        ) from None
                    deadline = None
                check_observation(observation)
                if not pipes and process.poll() is not None:
                    break
                wait = 0.05
                for end in (deadline, observation.deadline):
                    if end is not None:
                        wait = min(wait, max(0, end - now))
                for fd, available in pipes.ready(wait):
                    if available == 0:
                        pipes.discard(fd)
                        continue
                    remaining = (
                        observation.policy.max_response_bytes
                        - len(stdout)
                        - len(stderr)
                    )
                    chunk = os.read(fd, min(available, remaining + 1, 65536))
                    read_bytes[fd] += len(chunk)
                    if not chunk:
                        pipes.discard(fd)
                    elif len(chunk) > remaining:
                        raise ObservationLimitError(
                            "response",
                            observation.policy.max_response_bytes,
                            observation.policy.max_response_bytes + 1,
                        )
                    else:
                        buffers[fd].extend(chunk)
        check_observation(observation)
        return subprocess.CompletedProcess(
            args, process.wait(), bytes(stdout), bytes(stderr)
        )
    finally:
        if process.poll() is None:
            process.kill()
        process.stdout.close()
        process.stderr.close()
        process.wait()
        telemetry.return_code = process.returncode
        telemetry.stdout_bytes, telemetry.stderr_bytes = read_bytes.values()
