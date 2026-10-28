import sys
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from subprocess import Popen
from threading import Event
from types import GeneratorType
from unittest.mock import patch

import pytest

from pywhales import DockerClient
from pywhales._archive_deadline import ArchiveDeadline
from pywhales.exceptions import DockerTimeout
from pywhales.telemetry import CompletionRecord, TelemetryRecord


def test_unstarted_archive_save_close_has_no_launch_or_telemetry() -> None:
    # Given an archive iterator whose command has not started.
    records: list[TelemetryRecord] = []
    client = DockerClient(
        client_call=[sys.executable], telemetry_observer=records.append
    )
    with (
        patch.object(client.image, "inspect", return_value=[]),
        patch("pywhales.components.image.cli_wrapper.Popen") as launch,
    ):
        archive = client.image.save("sample")
        assert isinstance(archive, GeneratorType)

        # When the unused iterator is closed.
        archive.close()

    # Then it remains inert.
    launch.assert_not_called()
    assert records == []


@pytest.mark.parametrize("timeout", [None, 3.0])
def test_archive_save_close_completes_after_pipe_process_and_thread_cleanup(
    timeout: float | None,
) -> None:
    records: list[TelemetryRecord] = []
    owners: list[ArchiveDeadline] = []
    cleanup: list[bool] = []

    def observe(record: TelemetryRecord) -> None:
        records.append(record)
        if isinstance(record, CompletionRecord):
            owner = owners[0]
            cleanup.append(
                owner.process.returncode is not None
                and not owner.thread.is_alive()
                and all(
                    pipe.closed
                    for pipe in (owner.process.stdout, owner.process.stderr)
                    if pipe is not None
                )
            )

    def make_owner(
        process: Popen[bytes], command: list[str], deadline: float | None, *, raw: bool
    ) -> ArchiveDeadline:
        owner = ArchiveDeadline(process, command, deadline, raw=raw)
        owners.append(owner)
        return owner

    code = "import os,threading; os.write(1,b'first\\n'); threading.Event().wait()"
    client = DockerClient(
        client_call=[sys.executable, "-c", code],
        default_timeout=timeout,
        telemetry_observer=observe,
    )
    with (
        patch.object(client.image, "inspect", return_value=[]),
        patch("pywhales.components.image.cli_wrapper.ArchiveDeadline", make_owner),
    ):
        archive = client.image.save("sample")
        assert isinstance(archive, GeneratorType)
        try:
            assert next(archive) == b"first\n"
            assert len(records) == 1

            archive.close()
        finally:
            archive.close()

    assert cleanup == [True]
    assert len(records) == 2
    completed = records[-1]
    assert isinstance(completed, CompletionRecord)
    assert (completed.outcome, completed.stdin_bytes, completed.stdout_bytes) == (
        "closed",
        None,
        6,
    )
    assert list(archive) == []


def test_paused_archive_save_timeout_counts_prior_reads_once_after_cleanup() -> None:
    records: list[TelemetryRecord] = []
    owners: list[ArchiveDeadline] = []

    def make_owner(
        process: Popen[bytes], command: list[str], deadline: float | None, *, raw: bool
    ) -> ArchiveDeadline:
        owner = ArchiveDeadline(process, command, deadline, raw=raw)
        owners.append(owner)
        return owner

    code = "import os,threading; os.write(2,b'error'); os.write(1,b'first\\n'); threading.Event().wait()"
    client = DockerClient(
        client_call=[sys.executable, "-c", code],
        default_timeout=0.3,
        telemetry_observer=records.append,
    )
    with (
        patch.object(client.image, "inspect", return_value=[]),
        patch("pywhales.components.image.cli_wrapper.ArchiveDeadline", make_owner),
    ):
        archive = client.image.save("sample")
        assert isinstance(archive, GeneratorType)
        try:
            assert next(archive) == b"first\n"
            client.client_config.default_timeout = 30
            owner = owners[0]
            assert owner.released.wait(3)
            assert owner.process.returncode is not None
            assert len(records) == 1

            with pytest.raises(DockerTimeout) as raised:
                next(archive)
        finally:
            archive.close()

    assert raised.value.stderr == "error"
    assert len(records) == 2
    completed = records[-1]
    assert isinstance(completed, CompletionRecord)
    assert (completed.outcome, completed.stdout_bytes, completed.stderr_bytes) == (
        "timeout",
        6,
        5,
    )
    assert not owner.thread.is_alive()
    assert all(
        pipe.closed
        for pipe in (owner.process.stdout, owner.process.stderr)
        if pipe is not None
    )
    assert list(archive) == [] and len(records) == 2


def test_stalled_archive_load_finishes_telemetry_only_when_producer_resumes() -> None:
    records: list[TelemetryRecord] = []
    owners: Queue[ArchiveDeadline] = Queue()
    entered = Event()
    resume = Event()

    def make_owner(
        process: Popen[bytes], command: list[str], deadline: float | None, *, raw: bool
    ) -> ArchiveDeadline:
        owner = ArchiveDeadline(process, command, deadline, raw=raw)
        owners.put(owner)
        return owner

    def chunks() -> Generator[bytes]:
        yield b"accepted"
        entered.set()
        assert resume.wait(3)
        yield b""
        raise AssertionError("producer advanced after deadline")

    client = DockerClient(
        client_call=[
            sys.executable,
            "-c",
            "import threading; threading.Event().wait()",
        ],
        default_timeout=0.3,
        telemetry_observer=records.append,
    )
    with (
        patch("pywhales.components.image.cli_wrapper.ArchiveDeadline", make_owner),
        ThreadPoolExecutor(max_workers=1) as executor,
    ):
        result = executor.submit(client.image.load, chunks())
        try:
            owner = owners.get(timeout=3)
            assert entered.wait(3) and owner.released.wait(3)
            assert owner.process.returncode is not None
            assert not result.done() and len(records) == 1
        finally:
            resume.set()

        with pytest.raises(DockerTimeout):
            result.result(timeout=3)

    assert len(records) == 2
    completed = records[-1]
    assert isinstance(completed, CompletionRecord)
    assert (completed.outcome, completed.stdin_bytes) == ("timeout", 8)
    assert not owner.thread.is_alive()
    assert owner.process.stdin is not None and owner.process.stdin.closed
