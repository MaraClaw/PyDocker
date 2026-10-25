from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import assert_type

from pydocker import DockerClient, Image
from pydocker.components.buildx.cli_wrapper import BakeResult
from pydocker.components.buildx.imagetools.models import Manifest


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
    assert_type(client.build(".", stream_logs=True), Iterator[str])
    assert_type(client.build(".", stream_logs=stream), Image | Iterator[str] | None)
    assert_type(client.buildx.bake(), BakeResult)
    assert_type(client.buildx.bake(stream_logs=True), Iterator[str])
    assert_type(client.buildx.bake(stream_logs=stream), BakeResult | Iterator[str])
    assert_type(client.buildx.prune(), None)
    assert_type(client.buildx.prune(stream_logs=True), Iterator[str])
    assert_type(client.buildx.prune(stream_logs=stream), Iterator[str] | None)
    assert_type(client.buildx.imagetools.create(), None)
    assert_type(client.buildx.imagetools.create(dry_run=True), Manifest)
    assert_type(client.buildx.imagetools.create(dry_run=stream), Manifest | None)
