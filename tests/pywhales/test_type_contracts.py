"""Static contracts for resource, process, image, and Buildx return shapes.

The ty check analyzes assert_type calls inside _check_* functions. Pytest does
not collect or execute these functions; they are not runtime Docker examples.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import assert_type

from pywhales import (
    Config,
    Container,
    DockerClient,
    Image,
    Network,
    Node,
    Secret,
    Service,
    Volume,
)
from pywhales.components.buildx.cli_wrapper import BakeResult
from pywhales.components.buildx.imagetools.models import Manifest
from pywhales.components.compose.models import ComposeEvent
from pywhales.components.image.streams import EmptyImageStream, ImageStream
from pywhales.components.system.models import DockerEvent
from pywhales.components.top import ComposeTopResult, ContainerTopResult
from pywhales.utils import MappedProcessStream, ProcessStream, stream_stdout_and_stderr


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
    assert_type(client.service.inspect("service"), Service)
    assert_type(client.service.inspect(["service"]), list[Service])
    assert_type(client.node.inspect("node"), Node)
    assert_type(client.node.inspect(["node"]), list[Node])
    assert_type(client.config.inspect("config"), Config)
    assert_type(client.config.inspect(["config"]), list[Config])
    assert_type(client.secret.inspect("secret"), Secret)
    assert_type(client.secret.inspect(["secret"]), list[Secret])
    assert_type(client.secret.inspect(iter(["secret"])), list[Secret])


def _check_process_contracts(client: DockerClient, raw: bool) -> None:
    assert_type(client.top("container"), ContainerTopResult)
    assert_type(
        client.container.top("container", ["-eo", "pid,args"]), ContainerTopResult
    )
    assert_type(client.top("container", raw=True), str)
    assert_type(client.top("container", raw=raw), ContainerTopResult | str)
    assert_type(client.compose.top(), ComposeTopResult)
    assert_type(client.compose.top(["service"], raw=True), str)
    assert_type(client.compose.top([], raw=raw), ComposeTopResult | str)
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
    assert_type(client.pull("busybox", stream_logs=True), ImageStream)
    assert_type(client.pull("busybox", False, True), ImageStream)
    assert_type(
        client.pull("busybox", stream_logs=stream),
        Image | ImageStream,
    )
    assert_type(
        client.pull(["busybox"], stream_logs=stream),
        list[Image] | ImageStream | EmptyImageStream,
    )
    assert_type(client.push("busybox"), None)
    assert_type(client.push("busybox", stream_logs=True), ImageStream)
    assert_type(client.push([], stream_logs=True), ImageStream | None)
    with client.pull("busybox", stream_logs=True) as logs:
        assert_type(next(logs), tuple[str, bytes])
        assert_type(logs.close(), None)
    with client.push("busybox", stream_logs=True) as logs:
        assert_type(next(logs), tuple[str, bytes])
        assert_type(logs.close(), None)
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
