from collections.abc import Iterable
from typing import assert_type

from pywhales import DockerClient
from pywhales.utils import ProcessStream


def _check_compose_build_contracts(client: DockerClient, stream: bool) -> None:
    assert_type(client.compose.build(), None)
    assert_type(client.compose.build(builder="ci", check=True), None)
    assert_type(
        client.compose.build(
            print=True, provenance=False, sbom="generator=example", push=False
        ),
        None,
    )
    assert_type(
        client.compose.build(
            stream_logs=True,
            builder="ci",
            check=True,
            print=True,
            provenance=True,
            sbom=False,
            push=True,
        ),
        Iterable[tuple[str, bytes]] | None,
    )
    assert_type(
        client.compose.build(
            stream_logs=stream,
            builder="ci",
            check=True,
            print=True,
            provenance="mode=max",
            sbom=False,
            push=True,
        ),
        Iterable[tuple[str, bytes]] | None,
    )
    assert_type(
        client.compose.build(
            "web", {}, True, None, False, False, False, None, False, print=True
        ),
        None,
    )


def _check_compose_up_contracts(client: DockerClient, stream: bool) -> None:
    assert_type(
        client.compose.up(
            watch=True, abort_on_container_failure=True, exit_code_from="web"
        ),
        None,
    )
    assert_type(
        client.compose.up(
            stream_logs=True,
            watch=True,
            abort_on_container_failure=True,
            exit_code_from="web",
        ),
        Iterable[tuple[str, bytes]] | None,
    )
    assert_type(
        client.compose.up(
            stream_logs=stream,
            watch=True,
            abort_on_container_failure=True,
            exit_code_from="web",
        ),
        Iterable[tuple[str, bytes]] | None,
    )
    assert_type(
        client.compose.up(
            "web",
            False,
            False,
            False,
            {},
            False,
            False,
            True,
            False,
            False,
            False,
            True,
            True,
            True,
            False,
            False,
            None,
            None,
            False,
            0,
            True,
            False,
            watch=True,
        ),
        None,
    )


def _check_compose_watch_contracts(client: DockerClient) -> None:
    assert_type(client.compose.watch(), ProcessStream | None)
    stream = client.compose.watch("web", no_up=True, quiet=True, prune=False)
    if stream is not None:
        with stream as entered:
            assert_type(entered, ProcessStream)
            assert_type(next(entered), tuple[str, bytes])
            assert_type(entered.close(), None)
