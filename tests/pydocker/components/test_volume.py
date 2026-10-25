import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from pydocker import DockerClient, Volume
from pydocker.components.volume.models import VolumeInspectResult
from pydocker.exceptions import NoSuchVolume
from pydocker.test_utils import get_all_jsons, random_name


def test_batch_inspect_volume_names_and_typed_snapshot() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    snapshots = [
        {"Name": name, "Driver": "local", "Mountpoint": f"/data/{name}"}
        for name in ["second", "first", "second"]
    ]
    with (
        patch(
            "subprocess.run",
            return_value=CompletedProcess([], 0, json.dumps(snapshots).encode(), b""),
        ) as command,
        patch("pydocker.client_config.CACHE_VALIDITY_PERIOD", 3600),
    ):
        volumes = client.volume.inspect(["second", "first", "second"])
        assert [volume.name for volume in volumes] == ["second", "first", "second"]
        assert [volume.mountpoint for volume in volumes] == [
            Path("/data/second"),
            Path("/data/first"),
            Path("/data/second"),
        ]
        assert all(
            isinstance(volume._inspect_result, VolumeInspectResult)
            for volume in volumes
        )
        assert command.call_count == 1


@pytest.mark.parametrize("missing", [False, True], ids=["removed", "already-missing"])
def test_volume_cleanup_preserves_removal(missing: bool) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    volume = Volume(client.client_config, "resource", is_immutable_id=True)
    response = CompletedProcess(
        [], 1 if missing else 0, b"", b"No such volume: resource" if missing else b""
    )
    with patch("subprocess.run", return_value=response) as command, volume:
        pass
    assert command.call_args.args[0] == ["docker-test", "volume", "remove", "resource"]
    assert command.call_count == 1
    if missing:
        with (
            patch("subprocess.run", return_value=response),
            pytest.raises(NoSuchVolume),
        ):
            volume.remove()


@pytest.mark.parametrize("json_file", get_all_jsons("volumes"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    a = VolumeInspectResult(**json.loads(json_as_txt))
    if json_file.name == "0.json":
        assert a.created_at == datetime(
            year=2020,
            month=8,
            day=27,
            hour=9,
            minute=39,
            second=50,
            tzinfo=timezone(timedelta(hours=2)),
        )
        assert a.mountpoint == Path(
            "/var/lib/docker/volumes/dodo_letsencrypt_config/_data"
        )
        assert a.options is None


def test_simple_volume(ctr_client: DockerClient) -> None:
    some_volume = ctr_client.volume.create()
    assert some_volume.exists()
    assert some_volume.name in repr(ctr_client.volume.list())
    assert some_volume.driver is not None
    assert some_volume.driver in repr(ctr_client.volume.list())
    ctr_client.volume.remove(some_volume)


def test_multiple_volumes(ctr_client: DockerClient) -> None:
    volumes: list[Volume | str] = [ctr_client.volume.create() for _ in range(3)]

    for v in volumes:
        assert v in ctr_client.volume.list()

    ctr_client.volume.remove(volumes)

    for v in volumes:
        assert v not in ctr_client.volume.list()


def test_volume_drivers(ctr_client: DockerClient) -> None:
    some_volume = ctr_client.volume.create(
        driver="local",
        options={"type": "tmpfs", "device": "tmpfs", "o": "size=100m,uid=1000"},
    )
    ctr_client.run(
        "busybox",
        ["touch", "/dodo/dada"],
        volumes=[(some_volume, "/dodo")],
        remove=True,
    )
    ctr_client.volume.remove(some_volume)


def test_volume_labels(ctr_client: DockerClient) -> None:
    some_volume = ctr_client.volume.create(labels={"dodo": "dada"})

    assert some_volume.labels is not None
    assert some_volume.labels["dodo"] == "dada"
    ctr_client.volume.remove(some_volume)


def test_list(ctr_client: DockerClient) -> None:
    volumes = [ctr_client.volume.create() for _ in range(3)]

    all_volumes = ctr_client.volume.list()
    for v in volumes:
        assert v in all_volumes


def test_list_filters(ctr_client: DockerClient) -> None:
    select_label = random_name()
    with (
        ctr_client.volume.create(),
        ctr_client.volume.create(labels={select_label: ""}) as vol2,
    ):
        listed_volumes = ctr_client.volume.list(filters=[("label", select_label)])
        assert listed_volumes == [vol2]


def test_list_filters_old_signature(docker_client: DockerClient) -> None:
    """Check backward compatibility of the DockerClient.volume.list() API."""
    expected_warning = (
        r"Passing filters as a mapping is deprecated, replace with an iterable "
        r"of tuples instead, as so:\n"
        r"filters=\[\(.*\)\]"
    )
    select_label = random_name()
    with (
        docker_client.volume.create(),
        docker_client.volume.create(labels={select_label: ""}) as vol2,
    ):
        with pytest.warns(DeprecationWarning, match=expected_warning):
            listed_volumes = docker_client.volume.list(filters={"label": select_label})
        assert listed_volumes == [vol2]


def test_copy_to_volume(ctr_client: DockerClient, tmp_path: Path) -> None:
    some_volume = ctr_client.volume.create()
    ctr_client.run(
        "busybox",
        ["touch", "/volume/dodo.txt"],
        remove=True,
        volumes=[(some_volume, "/volume")],
    )

    ctr_client.volume.copy((some_volume, "dodo.txt"), tmp_path)
    assert (tmp_path / "dodo.txt").exists()


def test_copy_to_volume_subdirectory(ctr_client: DockerClient, tmp_path: Path) -> None:
    some_volume = ctr_client.volume.create()
    ctr_client.run(
        "busybox",
        [
            "sh",
            "-c",
            "mkdir -p /volume/subdir/subdir2 && touch /volume/subdir/subdir2/dodo.txt",
        ],
        remove=True,
        volumes=[(some_volume, "/volume")],
    )

    ctr_client.volume.copy((some_volume, "subdir/subdir2"), tmp_path)
    assert (tmp_path / "subdir2/dodo.txt").exists()


def test_copy_to_and_from_volume(ctr_client: DockerClient, tmp_path: Path) -> None:
    some_volume = ctr_client.volume.create()

    text_file = tmp_path / "some_dir" / "dodo.txt"
    text_file.parent.mkdir(parents=True)
    text_file.write_text("Hello\nWorld!")
    ctr_client.volume.copy(tmp_path, (some_volume, "subdir"))

    text_file.unlink()
    ctr_client.volume.copy((some_volume, "subdir/"), tmp_path)

    assert (tmp_path / "subdir/some_dir/dodo.txt").read_text() == "Hello\nWorld!"


def test_volume_cp_from_in_dir(ctr_client: DockerClient, tmp_path: Path) -> None:
    some_volume = ctr_client.volume.create()
    ctr_client.run(
        "busybox",
        ["touch", "/volume/dodo.txt"],
        remove=True,
        volumes=[(some_volume, "/volume")],
    )

    ctr_client.volume.copy((some_volume, "."), tmp_path)
    assert (tmp_path / "dodo.txt").exists()


def test_volume_cp_to_in_dir(ctr_client: DockerClient, tmp_path: Path) -> None:
    dodo = tmp_path / "dada" / "dodo.txt"
    dodo.parent.mkdir()
    dodo.touch()
    some_volume = ctr_client.volume.create()
    ctr_client.volume.copy(str(tmp_path) + "/.", (some_volume, ""))
    files = ctr_client.run(
        "busybox",
        ["ls", "/volume/dada/"],
        remove=True,
        volumes=[(some_volume, "/volume")],
    )

    assert files == "dodo.txt"


def test_clone(ctr_client: DockerClient) -> None:
    some_volume = ctr_client.volume.create()
    ctr_client.run(
        "busybox",
        [
            "sh",
            "-c",
            "mkdir -p /volume/subdir/subdir2 && touch /volume/subdir/subdir2/dodo.txt",
        ],
        remove=True,
        volumes=[(some_volume, "/volume")],
    )

    new_volume = ctr_client.volume.clone(some_volume)
    files = ctr_client.run(
        "busybox",
        [
            "sh",
            "-c",
            "ls /volume/subdir/subdir2",
        ],
        remove=True,
        volumes=[(new_volume, "/volume")],
    )
    assert files == "dodo.txt"


@pytest.mark.parametrize("method", ["inspect", "remove"])
def test_functions_no_such_volume(ctr_client: DockerClient, method: str) -> None:
    with pytest.raises(NoSuchVolume) as e:
        getattr(ctr_client.volume, method)("dodo")
    assert "no such volume" in str(e.value).lower()


def test_volume_does_not_exists(ctr_client: DockerClient) -> None:
    assert not ctr_client.volume.exists("dodo")


def test_volume_exists(ctr_client: DockerClient) -> None:
    with ctr_client.volume.create() as v:
        assert v.exists()
        assert ctr_client.volume.exists(v.name)


def test_prune(ctr_client: DockerClient) -> None:
    for volume in ctr_client.volume.list(filters={"name": "test-volume"}):
        volume.remove()
    volume = ctr_client.volume.create("test-volume")
    assert volume in ctr_client.volume.list()

    # volume not pruned because it does not have label "dne"
    ctr_client.volume.prune(filters={"label": "dne"}, all=True)
    assert volume in ctr_client.volume.list()

    # could only find "label" filter for `docker volume prune`

    # volume pruned
    ctr_client.volume.prune(all=True)
    assert volume not in ctr_client.volume.list()


def test_volume_remove_empty_list(ctr_client: DockerClient) -> None:
    with ctr_client.volume.create() as my_volume:
        assert my_volume in ctr_client.volume.list()
        all_volumes = set(ctr_client.volume.list())
        ctr_client.volume.remove([])
        assert all_volumes == set(ctr_client.volume.list())
