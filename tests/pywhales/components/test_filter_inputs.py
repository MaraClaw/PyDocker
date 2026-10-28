from types import MappingProxyType
from unittest.mock import PropertyMock, patch

import pytest

from pywhales import DockerClient

OPERATIONS = [
    ("container", "list"),
    ("container", "prune"),
    ("image", "list"),
    ("image", "prune"),
    ("network", "list"),
    ("network", "prune"),
    ("volume", "list"),
    ("volume", "prune"),
    ("service", "list"),
    ("secret", "list"),
]


@pytest.mark.parametrize(
    ("component", "method"),
    OPERATIONS,
)
@pytest.mark.parametrize(
    "filters", [{}, {"id": "x"}, {"label": "value"}, MappingProxyType({})]
)
def test_mapping_filters_are_rejected_before_cli_lookup(
    component: str, method: str, filters: dict[str, str] | MappingProxyType[str, str]
) -> None:
    client = DockerClient()
    operation = getattr(getattr(client, component), method)

    with (
        patch(
            "pywhales.client_config.DockerCLICaller.docker_cmd",
            new_callable=PropertyMock,
            side_effect=AssertionError("CLI lookup"),
        ),
        pytest.raises(TypeError),
    ):
        operation(filters=filters)


@pytest.mark.parametrize(("component", "method"), OPERATIONS)
@pytest.mark.parametrize("values", [[], [("label", "first"), ("label", "second")]])
def test_filter_generators_preserve_order_and_duplicates(
    component: str, method: str, values: list[tuple[str, str]]
) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    operation = getattr(getattr(client, component), method)

    with patch(
        f"pywhales.components.{component}.cli_wrapper.run", return_value=""
    ) as run:
        operation(filters=(pair for pair in values))

    command = list(run.call_args.args[0])
    actual = [
        command[index + 1] for index, arg in enumerate(command) if arg == "--filter"
    ]
    assert actual == [f"{key}={value}" for key, value in values]


@pytest.mark.parametrize(("component", "method"), OPERATIONS)
@pytest.mark.parametrize("entry", [("label",), ("label", "value", "extra")])
def test_malformed_filter_pairs_fail_before_launch(
    component: str, method: str, entry: tuple[str, ...]
) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    operation = getattr(getattr(client, component), method)

    with (
        patch(f"pywhales.components.{component}.cli_wrapper.run") as run,
        pytest.raises(ValueError),
    ):
        operation(filters=[entry])

    run.assert_not_called()


@pytest.mark.parametrize("alias", ["ps", "images"])
def test_root_list_alias_forwards_filters(alias: str) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    component = "container" if alias == "ps" else "image"

    with patch(
        f"pywhales.components.{component}.cli_wrapper.run", return_value=""
    ) as run:
        result = getattr(client, alias)(filters=[("label", "one"), ("label", "two")])

    assert result == []
    assert list(run.call_args.args[0])[-4:] == [
        "--filter",
        "label=one",
        "--filter",
        "label=two",
    ]


def test_container_prune_stream_forwards_filters_and_returns_stream() -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    records = iter([("stdout", b"deleted\n")])

    with patch(
        "pywhales.components.container.cli_wrapper.stream_stdout_and_stderr",
        return_value=records,
    ) as stream:
        result = client.container.prune(filters=[("label", "one")], stream_logs=True)

    assert result is records
    assert list(stream.call_args.args[0])[-2:] == ["--filter", "label=one"]


@pytest.mark.parametrize(
    ("component", "method", "output"),
    [
        ("config", "list", ""),
        ("compose", "ls", "[]"),
        ("system", "prune", ""),
        ("buildx", "prune", ""),
    ],
)
def test_other_filter_mapping_apis_remain_supported(
    component: str, method: str, output: str
) -> None:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    operation = getattr(getattr(client, component), method)

    with patch(
        f"pywhales.components.{component}.cli_wrapper.run", return_value=output
    ) as run:
        operation(filters={"label": "one"})

    assert list(run.call_args.args[0])[-2:] == ["--filter", "label=one"]
