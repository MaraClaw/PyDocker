from typing import Literal, assert_type

from pywhales import DockerClient, Image, docker
from pywhales.components.buildx.cli_wrapper import (
    BakeResult,
    BuildxCall,
    BuildxReportCall,
)
from pywhales.utils import MappedProcessStream


def _check_build_defaults(client: DockerClient, stream: bool) -> None:
    assert_type(client.buildx.build("."), Image | None)
    assert_type(client.build("."), Image | None)
    assert_type(docker.build("."), Image | None)
    assert_type(client.image.build("."), Image | None)
    assert_type(client.build(".", call=None), Image | None)
    assert_type(client.build(".", call="build"), Image | None)
    assert_type(client.build(".", stream_logs=True), MappedProcessStream[str])
    assert_type(
        client.build(".", stream_logs=stream), Image | MappedProcessStream[str] | None
    )


def _check_build_reports(
    client: DockerClient, report: BuildxReportCall, stream: bool
) -> None:
    assert_type(client.build(".", call="check"), str)
    assert_type(client.buildx.build(".", call="outline"), str)
    assert_type(docker.build(".", call="targets"), str)
    assert_type(client.build(".", call=report), str)
    assert_type(
        client.build(".", call=report, stream_logs=True), MappedProcessStream[str]
    )
    assert_type(
        client.build(".", call=report, stream_logs=stream),
        str | MappedProcessStream[str],
    )
    assert_type(
        client.build(
            ".", call="check", no_cache_filter="deps", annotations=["key=value"]
        ),
        str,
    )


def _check_build_dynamic(
    client: DockerClient, call: BuildxCall | None, stream: bool
) -> None:
    assert_type(client.build(".", call=call), Image | str | None)
    assert_type(
        client.build(".", call=call, stream_logs=True), MappedProcessStream[str]
    )
    assert_type(
        client.build(".", call=call, stream_logs=stream),
        Image | str | MappedProcessStream[str] | None,
    )


def _check_bake_defaults(client: DockerClient, stream: bool, config: bool) -> None:
    assert_type(client.buildx.bake(), BakeResult)
    assert_type(client.buildx.bake(print=True), BakeResult)
    assert_type(client.buildx.bake(call="build"), BakeResult)
    assert_type(client.buildx.bake(return_config=False), None)
    assert_type(client.buildx.bake(return_config=config), BakeResult | None)
    assert_type(client.buildx.bake(stream_logs=True), MappedProcessStream[str])
    assert_type(
        client.buildx.bake(stream_logs=stream), BakeResult | MappedProcessStream[str]
    )
    assert_type(
        client.buildx.bake(stream_logs=stream, return_config=False),
        MappedProcessStream[str] | None,
    )
    assert_type(
        client.buildx.bake(stream_logs=stream, return_config=config),
        BakeResult | MappedProcessStream[str] | None,
    )


def _check_bake_reports(
    client: DockerClient, report: BuildxReportCall, stream: bool
) -> None:
    assert_type(client.buildx.bake(call="check"), str)
    assert_type(client.buildx.bake(call="outline", return_config=False), str)
    assert_type(client.buildx.bake(call="targets", jobs=0), str)
    assert_type(client.buildx.bake(call=report, jobs="defer-error,parallel=2"), str)
    assert_type(
        client.buildx.bake(call=report, stream_logs=True), MappedProcessStream[str]
    )
    assert_type(
        client.buildx.bake(call=report, stream_logs=stream),
        str | MappedProcessStream[str],
    )


def _check_bake_dynamic(
    client: DockerClient, call: BuildxCall | None, stream: bool, config: bool
) -> None:
    assert_type(client.buildx.bake(call=call), BakeResult | str)
    assert_type(client.buildx.bake(call=call, return_config=False), str | None)
    assert_type(
        client.buildx.bake(call=call, return_config=config), BakeResult | str | None
    )
    assert_type(
        client.buildx.bake(call=call, stream_logs=True), MappedProcessStream[str]
    )
    assert_type(
        client.buildx.bake(call=call, stream_logs=stream),
        BakeResult | str | MappedProcessStream[str],
    )
    assert_type(
        client.buildx.bake(call=call, stream_logs=stream, return_config=config),
        BakeResult | str | MappedProcessStream[str] | None,
    )


def _check_normal_dynamic(client: DockerClient, call: Literal["build"] | None) -> None:
    assert_type(client.build(".", call=call), Image | None)
    assert_type(client.buildx.bake(call=call), BakeResult)


def _check_positional_streams(client: DockerClient, stream: bool) -> None:
    assert_type(
        client.buildx.bake(
            (), None, (), False, True, False, "auto", False, False, {}, {}, None, stream
        ),
        BakeResult | MappedProcessStream[str],
    )
    assert_type(
        client.build(
            ".",
            {},
            (),
            None,
            {},
            {},
            None,
            True,
            None,
            None,
            None,
            {},
            False,
            None,
            None,
            {},
            None,
            "auto",
            None,
            False,
            False,
            None,
            (),
            None,
            (),
            None,
            stream,
        ),
        Image | MappedProcessStream[str] | None,
    )
