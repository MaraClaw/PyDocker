from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import sys
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timedelta
from pathlib import Path
from queue import Queue
from subprocess import PIPE, Popen
from threading import Event, Thread
from types import MappingProxyType, TracebackType
from typing import (
    IO,
    Literal,
    Self,
    overload,
    override,
)

import pydantic

from pywhales.exceptions import (
    DockerException,
    NoSuchContainer,
    NoSuchImage,
    NoSuchNetwork,
    NoSuchService,
    NoSuchVolume,
    NotASwarmManager,
)

PROJECT_ROOT = Path(__file__).parents[1]

LOGGER = logging.getLogger(__name__)


def custom_parse_object_as[T](type_: type[T], obj: object) -> T:
    return pydantic.TypeAdapter(type_).validate_python(obj)


def title_if_necessary(string: str) -> str:
    if string.isupper():
        return string
    else:
        return string.title()


def to_docker_camel(string: str) -> str:
    try:
        special_cases = {
            "exec_ids": "ExecIDs",
            "sandbox_id": "SandboxID",
            "oom_killed": "OOMKilled",
            "rw": "RW",
            "link_local_ipv6_address": "LinkLocalIPv6Address",
            "link_local_ipv6_prefix_length": "LinkLocalIPv6PrefixLen",
            "secondary_ipv6_addresses": "SecondaryIPv6Addresses",
            "endpoint_id": "EndpointID",
            "global_ipv6_prefix_length": "GlobalIPv6PrefixLen",
            "ip_prefix_length": "IPPrefixLen",
            "ipv6_gateway": "IPv6Gateway",
            "network_id": "NetworkID",
            "ip_address": "IPAddress",
            "global_ipv6_address": "GlobalIPv6Address",
            "blkio_device_read_iops": "BlkioDeviceReadIOps",
            "blkio_device_write_iops": "BlkioDeviceWriteIOps",
            "device_ids": "DeviceIDs",
            "kernel_memory_tcp": "KernelMemoryTCP",
            "container_id_file": "ContainerIDFile",
            "uts_mode": "UTSMode",
            "root_fs": "RootFS",
            "enable_ipv6": "EnableIPv6",
            "ipv4_address": "IPv4Address",
            "ipv6_address": "IPv6Address",
            "ipam": "IPAM",
            "ipam_config": "IPAMConfig",
            "tls_info": "TLSInfo",
            "virtual_ips": "VirtualIPs",
            "default_api_version": "DefaultAPIVersion",
            "min_api_version": "MinAPIVersion",
        }
        return special_cases[string]
    except KeyError:
        return "".join(title_if_necessary(x) for x in string.split("_"))


class DockerCamelModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(
        populate_by_name=True,
        alias_generator=to_docker_camel,
        defer_build=True,
    )


def get_docker_exception_type(output: bytes | None) -> type[DockerException]:
    if output is None or len(output) == 0:
        return DockerException
    decoded = output.decode().lower()
    if "no such image" in decoded:
        return NoSuchImage
    if "no such service" in decoded or (
        "service" in decoded and "not found" in decoded
    ):
        return NoSuchService
    if "no such container" in decoded:
        return NoSuchContainer
    if "this node is not a swarm manager" in decoded:
        return NotASwarmManager
    if "no such volume" in decoded:
        return NoSuchVolume
    if "network" in decoded and "not found" in decoded:
        return NoSuchNetwork
    return DockerException


def _debug_enabled() -> bool:
    # PYDOCKER_DEBUG wins whenever it is set, even to "0"; the legacy variable is a fallback.
    debug_value = os.environ.get("PYDOCKER_DEBUG")
    if debug_value is None:
        debug_value = os.environ.get("PYTHON_ON_WHALES_DEBUG", "0")
    return debug_value == "1"


@overload
def run(
    args: Sequence[object],
    capture_stdout: bool = ...,
    capture_stderr: bool = ...,
    input: bytes | None = ...,
    return_stderr: Literal[False] = ...,
    env: Mapping[str, str] = ...,
    tty: bool = ...,
    pass_fds: Sequence[int] = ...,
) -> str: ...


@overload
def run(
    args: Sequence[object],
    capture_stdout: bool = ...,
    capture_stderr: bool = ...,
    input: bytes | None = ...,
    *,
    return_stderr: Literal[True],
    env: Mapping[str, str] = ...,
    tty: bool = ...,
    pass_fds: Sequence[int] = ...,
) -> tuple[str, str]: ...


def run(
    args: Sequence[object],
    capture_stdout: bool = True,
    capture_stderr: bool = True,
    input: bytes | None = None,
    return_stderr: bool = False,
    env: Mapping[str, str] = MappingProxyType({}),
    tty: bool = False,
    pass_fds: Sequence[int] = (),
) -> str | tuple[str, str]:
    args = [str(x) for x in args]
    subprocess_env = dict(os.environ)
    subprocess_env.update(env)
    if args[1] == "buildx":
        subprocess_env["DOCKER_CLI_EXPERIMENTAL"] = "enabled"
    if tty:
        stdout_dest = sys.stdout
    elif capture_stdout:
        stdout_dest = subprocess.PIPE
    else:
        stdout_dest = None
    if tty:
        stderr_dest = sys.stderr
    elif capture_stderr:
        stderr_dest = subprocess.PIPE
    else:
        stderr_dest = None
    if _debug_enabled():
        print("------------------------------")
        print("command: " + " ".join(args))
        print(f"Env: {subprocess_env}")
        print("------------------------------")
    LOGGER.debug("Running command: %s", shlex.join(args))
    completed_process = subprocess.run(
        args,
        input=input,
        stdout=stdout_dest,
        stderr=stderr_dest,
        env=subprocess_env,
        pass_fds=pass_fds,
    )

    if completed_process.returncode != 0:
        exception_type = get_docker_exception_type(completed_process.stderr)
        raise exception_type(
            args,
            completed_process.returncode,
            completed_process.stdout,
            completed_process.stderr,
        )

    if return_stderr:
        return (
            post_process_stream(completed_process.stdout),
            post_process_stream(completed_process.stderr),
        )
    else:
        return post_process_stream(completed_process.stdout)


def post_process_stream(stream: bytes | None) -> str:
    if stream is None:
        return ""
    return stream.decode(errors="replace").removesuffix("\n")


ValidPath = str | Path
ValidPortMapping = (
    tuple[str | int] | tuple[str | int, str | int] | tuple[str | int, str | int, str]
)


@overload
def to_list(x: str) -> list[str]: ...


@overload
def to_list(x: bytes) -> list[bytes]: ...


@overload
def to_list[T](x: Iterable[T]) -> list[T]: ...


@overload
def to_list[T](x: T) -> list[T]: ...


def to_list(x: object) -> list[object]:
    if isinstance(x, str | bytes):
        return [x]
    if isinstance(x, Iterable):
        return list(x)
    return [x]


class ProcessStream(Iterator[tuple[str, bytes]]):
    """Own a local CLI process, its pipes and its reader threads.

    Iterating over a ``ProcessStream`` yields ``(source, line)`` tuples.
    Exhaustion drains output and reports command failures. ``close()`` or
    context exit cancels the local CLI without reporting its cancellation as
    a command failure. A bare loop break does not close the stream, and closing
    it does not remove daemon-managed containers or other remote resources.
    """

    def __init__(self, process: Popen[bytes], full_cmd: list[str]) -> None:
        self._process = process
        self._full_cmd = full_cmd
        self.stdin = process.stdin
        self._queue: Queue[tuple[str, bytes] | None] = Queue()
        self._stop = Event()
        self._closed = False
        self._remaining_readers = 2
        self._stderr = bytearray()
        assert process.stdout is not None
        assert process.stderr is not None
        self._threads = tuple(
            Thread(target=self._read_pipe, args=(pipe, name), daemon=True)
            for pipe, name in ((process.stdout, "stdout"), (process.stderr, "stderr"))
        )
        for thread in self._threads:
            thread.start()

    def _read_pipe(self, pipe: IO[bytes], name: str) -> None:
        pending = b""
        try:
            with pipe:
                os.set_blocking(pipe.fileno(), False)
                while not self._stop.is_set():
                    try:
                        chunk = os.read(pipe.fileno(), 65536)
                    except BlockingIOError:
                        self._stop.wait(0.01)
                        continue
                    if not chunk:
                        if pending:
                            self._queue.put((name, pending))
                        break
                    *lines, pending = (pending + chunk).split(b"\n")
                    for line in lines:
                        self._queue.put((name, line + b"\n"))
        finally:
            self._queue.put(None)

    @override
    def __iter__(self) -> Self:
        return self

    @override
    def __next__(self) -> tuple[str, bytes]:
        if self._closed:
            raise StopIteration
        while self._remaining_readers:
            item = self._queue.get()
            if self._closed:
                raise StopIteration
            if item is None:
                self._remaining_readers -= 1
            else:
                source, line = item
                if source == "stderr":
                    self._stderr.extend(line)
                return item
        exit_code = self._process.wait()
        if self._closed:
            raise StopIteration
        self.close()
        if exit_code != 0:
            stderr = bytes(self._stderr)
            exception_type = get_docker_exception_type(stderr)
            raise exception_type(
                command_launched=self._full_cmd, return_code=exit_code, stderr=stderr
            )
        raise StopIteration

    def close(self) -> None:
        """Cancel consumption and reap the local CLI, even before iteration."""
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        self._queue.put(None)
        if self._process.poll() is None:
            with suppress(ProcessLookupError):
                self._process.kill()
            self._process.wait(timeout=1)
        if self.stdin is not None and not self.stdin.closed:
            os.set_blocking(self.stdin.fileno(), False)
            with suppress(BrokenPipeError, BlockingIOError):
                self.stdin.close()
        for thread in self._threads:
            thread.join()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class MappedProcessStream[T](Iterator[T]):
    """Carry process ownership through decoding, filtering or event parsing."""

    def __init__(self, stream: ProcessStream, iterator: Iterator[T]) -> None:
        self._stream = stream
        self._iterator = iterator

    @override
    def __iter__(self) -> Self:
        return self

    @override
    def __next__(self) -> T:
        yielded = False
        try:
            result = next(self._iterator)
            yielded = True
            return result
        finally:
            if not yielded:
                self.close()

    def close(self) -> None:
        """Release the process independently of whether mapping has started."""
        self._stream.close()
        self._iterator = iter(())

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def _start_process(
    full_cmd: Sequence[object],
    env: Mapping[str, str] | None = None,
    pass_fds: Sequence[int] = (),
    pipe_stdin: bool = False,
) -> ProcessStream:
    """Start a subprocess and wire up reader threads for stdout/stderr."""
    if env is None:
        subprocess_env = None
    else:
        subprocess_env = dict(os.environ)
        subprocess_env.update(env)

    process = Popen(
        [str(x) for x in full_cmd],
        stdin=PIPE if pipe_stdin else None,
        stdout=PIPE,
        stderr=PIPE,
        env=subprocess_env,
        pass_fds=pass_fds,
    )
    return ProcessStream(process, [str(x) for x in full_cmd])


def stream_stdout_and_stderr(
    full_cmd: Sequence[object],
    env: Mapping[str, str] | None = None,
    pass_fds: Sequence[int] = (),
    pipe_stdin: bool = False,
) -> ProcessStream:
    """Stream stdout/stderr from a subprocess.

    Always returns a :class:`ProcessStream`.  When *pipe_stdin* is
    ``True`` the subprocess is started with ``stdin=PIPE`` so that
    ``result.stdin`` is available for writing.  Otherwise ``result.stdin``
    is ``None``.
    """
    return _start_process(full_cmd, env, pass_fds, pipe_stdin)


def format_mapping_for_cli(
    mapping: Mapping[str, object], separator: str = "="
) -> list[str]:
    return [f"{key}{separator}{value}" for key, value in mapping.items()]


def read_env_file(env_file: Path) -> dict[str, str]:
    result_dict: dict[str, str] = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        try:
            first_sharp = line.index("#")
        except ValueError:
            pass
        else:
            line = line[:first_sharp]
        if not line:
            continue
        line = line.strip()
        key, value = line.split("=", 1)
        result_dict[key] = value
    return result_dict


def read_env_files(env_files: list[Path]) -> dict[str, str]:
    result_dict: dict[str, str] = {}
    for file in env_files:
        result_dict.update(read_env_file(file))
    return result_dict


def format_time_arg(time_object: datetime | timedelta | None) -> str | None:
    if time_object is None:
        return None
    else:
        return format_time_for_docker(time_object)


def format_time_for_docker(time_object: datetime | timedelta) -> str:
    if isinstance(time_object, datetime):
        return time_object.isoformat()
    return f"{time_object.total_seconds()}s"


def format_signal_arg(signal_object: int | str | None) -> str | None:
    if signal_object is None:
        return None
    else:
        return format_signal_for_docker(signal_object)


def format_signal_for_docker(signal_object: int | str) -> str:
    if isinstance(signal_object, int):
        return signal.Signals(signal_object).name
    elif isinstance(signal_object, str):
        if hasattr(signal.Signals, "SIG" + signal_object):
            return "SIG" + signal_object
        else:
            return signal_object
    else:
        raise TypeError(f"Got unexpected signal type {type(signal_object).__name__!r}")


def format_port_arg(port_object: ValidPortMapping) -> str:
    if len(port_object) == 1:
        return str(port_object[0])
    elif len(port_object) == 2:
        return f"{port_object[0]}:{port_object[1]}"
    elif len(port_object) == 3:
        return f"{port_object[0]}:{port_object[1]}/{port_object[2]}"
    else:
        raise ValueError(
            "The size of the tuples in the publish list must be 1, 2, or 3"
        )


def parse_ls_status_count(status_output: str, status: str) -> int:
    try:
        return int(status_output.split(status + "(")[1].split(")")[0])
    except IndexError:
        return 0


def join_if_not_none(sequence: Sequence[object] | None) -> str | None:
    if sequence is None:
        return None
    return ",".join(str(x) for x in sequence)


def format_cpuset(value: Iterable[int] | str | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return ",".join(str(x) for x in value)


def to_seconds(duration: int | timedelta | None) -> str | None:
    if duration is None:
        return None
    if isinstance(duration, timedelta):
        duration = int(duration.total_seconds())
    return f"{duration}s"
