from collections.abc import Iterable
from typing import assert_type

from pywhales import Container, DockerClient, Image, docker
from pywhales.components.buildx.cli_wrapper import (
    BakeResult,
    BuildxCall,
    BuildxReportCall,
)
from pywhales.components.compose.cli_wrapper import ComposeRunArgs
from pywhales.components.container.cli_wrapper import ExecuteArgs, RunArgs
from pywhales.utils import MappedProcessStream, ProcessStream


def _check_container_deadlines(
    client: DockerClient, container: Container, timeout: float | None, flag: bool
) -> None:
    run_args: RunArgs = {"timeout": timeout}
    execute_args: ExecuteArgs = {"timeout": timeout}
    assert_type(client.run("app", **run_args), str)
    assert_type(client.container.run("app", detach=True, timeout=timeout), Container)
    assert_type(
        docker.run("app", stream=True, timeout=timeout), Iterable[tuple[str, bytes]]
    )
    assert_type(
        client.run("app", detach=flag, stream=flag, timeout=timeout),
        Container | str | Iterable[tuple[str, bytes]],
    )
    assert_type(client.execute("app", ["cmd"], **execute_args), str)
    assert_type(
        client.container.execute("app", ["cmd"], detach=True, timeout=timeout), None
    )
    assert_type(
        docker.execute("app", ["cmd"], stream=True, timeout=timeout), ProcessStream
    )
    assert_type(
        client.execute("app", ["cmd"], detach=flag, stream=flag, timeout=timeout),
        str | ProcessStream | None,
    )
    assert_type(container.execute(["cmd"], **execute_args), str)
    assert_type(container.execute(["cmd"], detach=True, timeout=timeout), None)
    assert_type(container.execute(["cmd"], stream=True, timeout=timeout), ProcessStream)
    assert_type(
        container.execute(["cmd"], detach=flag, stream=flag, timeout=timeout),
        str | ProcessStream | None,
    )
    assert_type(
        client.execute(
            "app",
            ["cmd"],
            False,
            {},
            (),
            False,
            False,
            False,
            None,
            None,
            flag,
            None,
            timeout=timeout,
        ),
        str | ProcessStream | None,
    )
    assert_type(
        container.execute(
            ["cmd"],
            False,
            {},
            (),
            False,
            False,
            False,
            None,
            None,
            flag,
            None,
            timeout=timeout,
        ),
        str | ProcessStream | None,
    )


def _check_compose_deadlines(
    client: DockerClient, timeout: float | None, flag: bool
) -> None:
    run_args: ComposeRunArgs = {"timeout": timeout}
    assert_type(client.compose.run("app", **run_args), str)
    assert_type(client.compose.run("app", detach=True, timeout=timeout), Container)
    assert_type(
        client.compose.run("app", stream=True, timeout=timeout),
        Iterable[tuple[str, bytes]],
    )
    assert_type(
        client.compose.run("app", detach=flag, stream=flag, timeout=timeout),
        Container | str | Iterable[tuple[str, bytes]],
    )
    assert_type(
        client.compose.run(
            "app",
            (),
            False,
            False,
            None,
            {},
            {},
            None,
            False,
            flag,
            True,
            (),
            False,
            False,
            False,
            None,
            (),
            None,
            timeout=timeout,
        ),
        Container | str | Iterable[tuple[str, bytes]],
    )


def _check_build_deadlines(
    client: DockerClient,
    timeout: float | None,
    call: BuildxCall | None,
    report: BuildxReportCall,
    flag: bool,
) -> None:
    assert_type(client.buildx.build(".", timeout=timeout), Image | None)
    assert_type(client.build(".", timeout=timeout), Image | None)
    assert_type(docker.build(".", timeout=timeout), Image | None)
    assert_type(client.build(".", call=report, timeout=timeout), str)
    assert_type(client.build(".", call=call, timeout=timeout), Image | str | None)
    assert_type(
        client.build(".", stream_logs=True, timeout=timeout), MappedProcessStream[str]
    )
    assert_type(
        client.build(".", stream_logs=flag, timeout=timeout),
        Image | MappedProcessStream[str] | None,
    )
    assert_type(
        client.build(".", call=report, stream_logs=flag, timeout=timeout),
        str | MappedProcessStream[str],
    )
    assert_type(
        client.build(".", call=call, stream_logs=flag, timeout=timeout),
        Image | str | MappedProcessStream[str] | None,
    )


def _check_bake_deadlines(
    client: DockerClient,
    timeout: float | None,
    call: BuildxCall | None,
    report: BuildxReportCall,
    flag: bool,
) -> None:
    assert_type(client.buildx.bake(timeout=timeout), BakeResult)
    assert_type(client.buildx.bake(print=True, timeout=timeout), BakeResult)
    assert_type(client.buildx.bake(return_config=False, timeout=timeout), None)
    assert_type(
        client.buildx.bake(return_config=flag, timeout=timeout), BakeResult | None
    )
    assert_type(client.buildx.bake(call=report, timeout=timeout), str)
    assert_type(client.buildx.bake(call=call, timeout=timeout), BakeResult | str)
    assert_type(
        client.buildx.bake(call=call, return_config=False, timeout=timeout), str | None
    )
    assert_type(
        client.buildx.bake(stream_logs=True, timeout=timeout), MappedProcessStream[str]
    )
    assert_type(
        client.buildx.bake(stream_logs=flag, timeout=timeout),
        BakeResult | MappedProcessStream[str],
    )
    assert_type(
        client.buildx.bake(call=report, stream_logs=flag, timeout=timeout),
        str | MappedProcessStream[str],
    )
    assert_type(
        client.buildx.bake(
            call=call, stream_logs=flag, return_config=flag, timeout=timeout
        ),
        BakeResult | str | MappedProcessStream[str] | None,
    )
