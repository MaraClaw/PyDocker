import os
import select
import signal
import sys
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import pytest

import pydocker.utils
from pydocker.exceptions import NoSuchContainer
from pydocker.utils import (
    MappedProcessStream,
    ProcessStream,
    format_cpuset,
    stream_stdout_and_stderr,
)


def test_mapped_process_stream_owns_unstarted_mapping() -> None:
    child = stream_stdout_and_stderr([sys.executable, "-c", "input()"], pipe_stdin=True)
    stream = MappedProcessStream(child, (line for _, line in child))

    with stream as entered:
        assert entered is stream

    stream.close()
    assert list(stream) == []
    _assert_process_released(child)


def test_format_cpuset_none() -> None:
    assert format_cpuset(None) is None


def test_format_cpuset_list_of_ints() -> None:
    assert format_cpuset([0, 2, 3]) == "0,2,3"


def test_format_cpuset_empty_list() -> None:
    assert format_cpuset([]) == ""


def test_format_cpuset_string_passthrough() -> None:
    # cpuset strings accepted by the CLI must be passed through unmodified so
    # ranges like "0-3" and mixes like "0-3,7" reach docker as-is.
    assert format_cpuset("0-3") == "0-3"
    assert format_cpuset("0,2") == "0,2"
    assert format_cpuset("0-3,7") == "0-3,7"


def test_format_cpuset_generator() -> None:
    assert format_cpuset(iter([1, 4])) == "1,4"


def test_environment_variables_propagation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_VARIABLE", "dododada")

    stdout = pydocker.utils.run(
        ["bash", "-c", "echo $SOME_VARIABLE && echo $OTHER_VARIABLE"],
        capture_stdout=True,
        env={"OTHER_VARIABLE": "dudu"},
    )
    assert stdout == "dododada\ndudu"


def test_stream_stdout_and_stderr_interactive() -> None:
    proc = stream_stdout_and_stderr(["cat"], pipe_stdin=True)
    assert isinstance(proc, ProcessStream)
    assert proc.stdin is not None
    proc.stdin.write(b"hello world\n")
    proc.stdin.close()
    output = list(proc)
    assert ("stdout", b"hello world\n") in output


def test_stream_stdout_and_stderr_interactive_multiple_lines() -> None:
    proc = stream_stdout_and_stderr(["cat"], pipe_stdin=True)
    assert isinstance(proc, ProcessStream)
    assert proc.stdin is not None
    proc.stdin.write(b"line1\n")
    proc.stdin.write(b"line2\n")
    proc.stdin.close()
    output = list(proc)
    stdout_lines = [line for source, line in output if source == "stdout"]
    assert b"line1\n" in stdout_lines
    assert b"line2\n" in stdout_lines


def test_stream_stdout_and_stderr_non_interactive() -> None:
    """Non-interactive mode returns ProcessStream with stdin=None."""
    result = stream_stdout_and_stderr(["echo", "hi"])
    assert isinstance(result, ProcessStream)
    assert result.stdin is None
    output = list(result)
    assert ("stdout", b"hi\n") in output


def test_process_stream_close_after_eof() -> None:
    stream = stream_stdout_and_stderr([sys.executable, "-c", "print('done')"])
    assert list(stream) == [("stdout", b"done\n")]

    stream.close()
    stream.close()

    assert list(stream) == []


def _assert_process_released(stream: ProcessStream) -> None:
    assert stream._process.poll() is not None
    assert all(not thread.is_alive() for thread in stream._threads)
    assert all(
        pipe.closed
        for pipe in (stream.stdin, stream._process.stdout, stream._process.stderr)
        if pipe is not None
    )


@pytest.mark.parametrize("consume", [False, True])
def test_process_stream_closes_idle_child(consume: bool) -> None:
    with stream_stdout_and_stderr(
        [sys.executable, "-c", "print('ready', flush=True); input()"], pipe_stdin=True
    ) as stream:
        if consume:
            assert next(stream) == ("stdout", b"ready\n")

        started = monotonic()
        stream.close()
        stream.close()

        assert monotonic() - started < 3
        assert list(stream) == []
        _assert_process_released(stream)


def test_process_stream_closes_with_pending_output() -> None:
    with stream_stdout_and_stderr(
        [sys.executable, "-c", "print('queued\\n' * 100000, flush=True); input()"],
        pipe_stdin=True,
    ) as stream:
        assert stream._queue.get(timeout=3) == ("stdout", b"queued\n")

        started = monotonic()
        stream.close()

        assert monotonic() - started < 3
        _assert_process_released(stream)


def test_process_stream_context_closes_before_iteration() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True
    )

    with stream as entered:
        assert entered is stream

    _assert_process_released(stream)


def test_process_stream_context_preserves_body_error() -> None:
    stream = stream_stdout_and_stderr(
        [sys.executable, "-c", "print('ready', flush=True); input()"], pipe_stdin=True
    )
    error = RuntimeError("consumer failed")

    with pytest.raises(RuntimeError) as raised, stream:
        assert next(stream) == ("stdout", b"ready\n")
        raise error

    assert raised.value is error
    _assert_process_released(stream)


def test_process_stream_close_interrupts_pending_next() -> None:
    with stream_stdout_and_stderr(
        [sys.executable, "-c", "input()"], pipe_stdin=True
    ) as stream:
        entered = Event()
        finished = Event()
        output: list[tuple[str, bytes]] = []

        def consume() -> None:
            entered.set()
            output.extend(stream)
            finished.set()

        consumer = Thread(target=consume, daemon=True)
        consumer.start()
        assert entered.wait(timeout=3)

        stream.close()

        assert finished.wait(timeout=3)
        consumer.join(timeout=3)
        assert output == []
        _assert_process_released(stream)


@pytest.mark.skipif(
    sys.platform == "win32", reason="SIGTERM can only be ignored on POSIX"
)
def test_process_stream_kills_child_that_ignores_termination() -> None:
    with stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "print('ready', flush=True); input()",
        ],
        pipe_stdin=True,
    ) as stream:
        assert next(stream) == ("stdout", b"ready\n")

        started = monotonic()
        stream.close()

        assert monotonic() - started < 3
        _assert_process_released(stream)


def test_process_stream_eof_preserves_pipe_order_and_unterminated_lines() -> None:
    stream = stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import os; "
            "[(os.write(1, f'out{n}\\n'.encode()), os.write(2, f'err{n}\\n'.encode())) "
            "for n in range(1000)]; os.write(1, b'tail'); os.write(2, b'error tail')",
        ]
    )

    output = list(stream)

    assert [line for origin, line in output if origin == "stdout"] == [
        *(f"out{number}\n".encode() for number in range(1000)),
        b"tail",
    ]
    assert [line for origin, line in output if origin == "stderr"] == [
        *(f"err{number}\n".encode() for number in range(1000)),
        b"error tail",
    ]
    _assert_process_released(stream)


def test_process_stream_eof_preserves_command_failure() -> None:
    stream = stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import sys; print('output'); "
            "sys.stderr.write('No such container: missing\\n'); sys.exit(7)",
        ],
        pipe_stdin=True,
    )
    output: list[tuple[str, bytes]] = []

    with pytest.raises(NoSuchContainer) as raised:
        output.extend(stream)

    assert ("stdout", b"output\n") in output
    assert ("stderr", b"No such container: missing\n") in output
    assert raised.value.return_code == 7
    assert raised.value.stderr == "No such container: missing\n"
    _assert_process_released(stream)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal handlers")
def test_process_stream_close_never_runs_signal_handler(tmp_path: Path) -> None:
    forwarded = tmp_path / "forwarded"
    with stream_stdout_and_stderr(
        [
            sys.executable,
            "-c",
            "import signal, sys; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, "
            "lambda *_: Path(sys.argv[1]).write_bytes(b'forwarded')); "
            "print('ready', flush=True)\n"
            "while True: signal.pause()",
            forwarded,
        ]
    ) as stream:
        assert stream._queue.get(timeout=3) == ("stdout", b"ready\n")

        stream.close()

        assert not forwarded.exists()
        assert stream._process.returncode == -signal.SIGKILL
        _assert_process_released(stream)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX fork and inherited fds")
def test_process_stream_close_discards_buffered_stdin_held_by_descendant() -> None:
    release_read, release_write = os.pipe()
    status_read, status_write = os.pipe()
    with (
        os.fdopen(release_read, "rb", buffering=0) as release_reader,
        os.fdopen(release_write, "wb", buffering=0) as release_writer,
        os.fdopen(status_read, "rb", buffering=0) as status_reader,
        os.fdopen(status_write, "wb", buffering=0) as status_writer,
    ):
        stream = stream_stdout_and_stderr(
            [
                sys.executable,
                "-c",
                "import os, signal, sys\n"
                "release, status = map(int, sys.argv[1:])\n"
                "if os.fork() == 0:\n"
                "    os.write(status, b'R')\n"
                "    os.read(release, 1)\n"
                "    os._exit(0)\n"
                "os.close(release)\n"
                "os.close(status)\n"
                "signal.pause()\n",
                release_reader.fileno(),
                status_writer.fileno(),
            ],
            pass_fds=(release_reader.fileno(), status_writer.fileno()),
            pipe_stdin=True,
        )
        closer = Thread(target=stream.close, daemon=True)
        try:
            release_reader.close()
            status_writer.close()
            assert select.select([status_reader], [], [], 3)[0]
            assert status_reader.read(1) == b"R"
            assert stream.stdin is not None
            stdin_fd = stream.stdin.fileno()
            os.set_blocking(stdin_fd, False)
            with pytest.raises(BlockingIOError):
                while True:
                    os.write(stdin_fd, b"x" * 4096)
            assert stream.stdin.write(b"buffered") == len(b"buffered")
            os.set_blocking(stdin_fd, True)

            closer.start()
            closer.join(timeout=3)

            assert not closer.is_alive(), "close blocked flushing buffered stdin"
            _assert_process_released(stream)
            assert not select.select([status_reader], [], [], 0)[0]
        finally:
            release_writer.close()
            if closer.ident is None:
                closer.start()
            closer.join(timeout=3)
            assert not closer.is_alive()
            assert select.select([status_reader], [], [], 3)[0]
            assert status_reader.read(1) == b""
            _assert_process_released(stream)
