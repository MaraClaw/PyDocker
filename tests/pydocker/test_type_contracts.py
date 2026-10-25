from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import assert_type

from pydocker import Container, DockerClient, Image, Network, Volume
from pydocker.components.buildx.cli_wrapper import BakeResult
from pydocker.components.buildx.imagetools.models import Manifest
from pydocker.components.compose.models import ComposeEvent
from pydocker.components.system.models import DockerEvent
from pydocker.utils import MappedProcessStream, ProcessStream, stream_stdout_and_stderr


def _check_resource_inspection_contracts(client: DockerClient) -> None:
    assert_type(client.container.inspect("container"), Container)
    assert_type(client.container.inspect(["container"]), list[Container])
    assert_type(client.container.inspect(iter(["container"])), list[Container])
    assert_type(client.image.inspect("image"), Image)
    assert_type(client.image.inspect(["image"]), list[Image])
    assert_type(client.image.inspect(iter(["image"])), list[Image])
    assert_type(client.network.inspect("network"), Network)
    assert_type(client.network.inspect(["network"]), list[Network])
    assert_type(client.network.inspect(iter(["network"])), list[Network])
    assert_type(client.volume.inspect("volume"), Volume)
    assert_type(client.volume.inspect(["volume"]), list[Volume])
    assert_type(client.volume.inspect(iter(["volume"])), list[Volume])


def _check_process_contracts(client: DockerClient, raw: bool) -> None:
    assert_type(client.system.events(), MappedProcessStream[DockerEvent])
    assert_type(client.compose.events(), MappedProcessStream[ComposeEvent])
    with stream_stdout_and_stderr(["command"]) as stream:
        assert_type(stream, ProcessStream)
        assert_type(next(stream), tuple[str, bytes])
        assert_type(stream.close(), None)
    with client.build(".", stream_logs=True) as logs:
        assert_type(logs, MappedProcessStream[str])
        assert_type(next(logs), str)
        assert_type(logs.close(), None)


def _check_image_return_contracts(
    client: DockerClient, stream: bool, output: Path | None
) -> None:
    assert_type(client.pull("busybox"), Image)
    assert_type(client.pull(["busybox"]), list[Image])
    assert_type(client.pull("busybox", stream_logs=True), Iterable[tuple[str, bytes]])
    assert_type(client.pull("busybox", False, True), Iterable[tuple[str, bytes]])
    assert_type(
        client.pull("busybox", stream_logs=stream),
        Image | Iterable[tuple[str, bytes]],
    )
    assert_type(
        client.pull(["busybox"], stream_logs=stream),
        list[Image] | Iterable[tuple[str, bytes]],
    )
    assert_type(client.push("busybox"), None)
    assert_type(client.push("busybox", stream_logs=True), Iterable[tuple[str, bytes]])
    assert_type(client.push([], stream_logs=True), Iterable[tuple[str, bytes]] | None)
    assert_type(client.image.save("busybox"), Iterator[bytes])
    assert_type(client.image.save("busybox", output=Path("image.tar")), None)
    assert_type(client.image.save("busybox", output=output), Iterator[bytes] | None)
    assert_type(client.image.load(iter([b"archive"])), list[str])


def _check_buildx_return_contracts(client: DockerClient, stream: bool) -> None:
    assert_type(client.build("."), Image | None)
    assert_type(client.build(".", load=True), Image | None)
    assert_type(client.build(".", stream_logs=True), MappedProcessStream[str])
    assert_type(
        client.build(".", stream_logs=stream), Image | MappedProcessStream[str] | None
    )
    assert_type(client.buildx.bake(), BakeResult)
    assert_type(client.buildx.bake(stream_logs=True), MappedProcessStream[str])
    assert_type(
        client.buildx.bake(stream_logs=stream), BakeResult | MappedProcessStream[str]
    )
    assert_type(client.buildx.prune(), None)
    assert_type(client.buildx.prune(stream_logs=True), MappedProcessStream[str])
    assert_type(
        client.buildx.prune(stream_logs=stream), MappedProcessStream[str] | None
    )
    assert_type(client.buildx.imagetools.create(), None)
    assert_type(client.buildx.imagetools.create(dry_run=True), Manifest)
    assert_type(client.buildx.imagetools.create(dry_run=stream), Manifest | None)
