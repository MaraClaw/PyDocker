from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import contextmanager
from types import MappingProxyType
from unittest.mock import MagicMock, PropertyMock, patch

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


PAIR_OPERATIONS = [
    pytest.param(
        "config", "list", "pywhales.components.config.cli_wrapper.run", "", id="config"
    ),
    pytest.param(
        "system",
        "events",
        "pywhales.components.system.cli_wrapper.stream_stdout_and_stderr",
        None,
        id="system-events",
    ),
    pytest.param(
        "system",
        "prune",
        "pywhales.components.system.cli_wrapper.run",
        "",
        id="system-prune",
    ),
    pytest.param(
        "buildx", "prune", "pywhales.components.buildx.cli_wrapper.run", "", id="buildx"
    ),
    pytest.param(
        "compose",
        "ls",
        "pywhales.components.compose.cli_wrapper.run",
        "[]",
        id="compose",
    ),
]
PAIRS = [
    ("label", "first"),
    ("name", "middle"),
    ("label", "second"),
    ("label", "first"),
]


class OneShot:
    """Iterable that fails when it is iterated a second time."""

    def __init__(self, values: Iterable[tuple[str, str]]) -> None:
        self.values = list(values)
        self.iterations = 0

    def __iter__(self) -> Iterator[tuple[str, str]]:
        self.iterations += 1
        assert self.iterations == 1, "filters were iterated more than once"
        return iter(self.values)


@contextmanager
def forbid_cli_lookup() -> Generator[None]:
    with (
        patch(
            "pywhales.client_config.DockerCLICaller.docker_cmd",
            new_callable=PropertyMock,
            side_effect=AssertionError("CLI lookup"),
        ),
        patch(
            "pywhales.client_config.DockerCLICaller.docker_compose_cmd",
            new_callable=PropertyMock,
            side_effect=AssertionError("CLI lookup"),
        ),
        patch(
            "pywhales.client_config.ClientConfig.get_client_call_with_path",
            side_effect=AssertionError("CLI lookup"),
        ),
    ):
        yield


def pair_operation(component: str, method: str) -> Callable[..., object]:
    client = DockerClient()
    client.client_config._client_call_with_path = ["docker-test"]
    return getattr(getattr(client, component), method)


def launched_filters(launch: MagicMock) -> list[str]:
    command = list(launch.call_args.args[0])
    return [command[i + 1] for i, arg in enumerate(command) if arg == "--filter"]


@pytest.mark.parametrize(("component", "method", "target", "result"), PAIR_OPERATIONS)
@pytest.mark.parametrize(
    "wrap",
    [list, tuple, iter, OneShot, lambda values: (pair for pair in values)],
    ids=["list", "tuple", "iterator", "one-shot", "generator"],
)
def test_pair_filters_keep_order_and_duplicates(
    component: str,
    method: str,
    target: str,
    result: str | None,
    wrap: Callable[[list[tuple[str, str]]], Iterable[tuple[str, str]]],
) -> None:
    operation = pair_operation(component, method)
    source = wrap(PAIRS)

    with patch(target, return_value=result or MagicMock()) as launch:
        operation(filters=source)

    assert launched_filters(launch) == [f"{key}={value}" for key, value in PAIRS]
    if isinstance(source, OneShot):
        assert source.iterations == 1


@pytest.mark.parametrize(("component", "method", "target", "result"), PAIR_OPERATIONS)
def test_empty_pair_filters_add_no_filter_option(
    component: str, method: str, target: str, result: str | None
) -> None:
    operation = pair_operation(component, method)

    with patch(target, return_value=result or MagicMock()) as launch:
        operation(filters=iter(()))

    assert "--filter" not in list(launch.call_args.args[0])


@pytest.mark.parametrize(("component", "method", "target", "result"), PAIR_OPERATIONS)
@pytest.mark.parametrize(
    "mapping", [{"label": "one", "name": "two"}, MappingProxyType({"label": "one"})]
)
def test_mapping_and_pair_filters_build_the_same_command(
    component: str,
    method: str,
    target: str,
    result: str | None,
    mapping: dict[str, str] | MappingProxyType[str, str],
) -> None:
    operation = pair_operation(component, method)

    with patch(target, return_value=result or MagicMock()) as launch:
        operation(filters=mapping)
        from_mapping = list(launch.call_args.args[0])
        operation(filters=list(mapping.items()))
        from_pairs = list(launch.call_args.args[0])

    assert from_mapping == from_pairs
    assert launched_filters(launch) == [
        f"{key}={value}" for key, value in mapping.items()
    ]


INVALID_FILTERS = [
    pytest.param("label=value", id="str"),
    pytest.param(b"label=value", id="bytes"),
    pytest.param(bytearray(b"label=value"), id="bytearray"),
    pytest.param(["ab"], id="two-char-string-entry"),
    pytest.param([b"ab"], id="two-byte-entry"),
    pytest.param(["label=value"], id="string-entry"),
    pytest.param([("label",)], id="short-pair"),
    pytest.param([("label", "value", "extra")], id="long-pair"),
    pytest.param([()], id="empty-pair"),
    pytest.param([["label", "value"]], id="list-pair"),
    pytest.param([{"label": "value"}], id="mapping-entry"),
    pytest.param([None], id="none-entry"),
    pytest.param([(1, "value")], id="non-string-key"),
    pytest.param([("label", 1)], id="non-string-value"),
    pytest.param([("label", None)], id="none-value"),
    pytest.param([("label", b"value")], id="bytes-value"),
    pytest.param([("label", "ok"), ("label",)], id="bad-after-good"),
    pytest.param(7, id="not-iterable"),
]


@pytest.mark.parametrize(("component", "method", "target", "result"), PAIR_OPERATIONS)
@pytest.mark.parametrize("filters", INVALID_FILTERS)
def test_invalid_pair_filters_fail_before_cli_lookup(
    component: str, method: str, target: str, result: str | None, filters: object
) -> None:
    client = DockerClient()
    operation = getattr(getattr(client, component), method)

    with (
        forbid_cli_lookup(),
        patch(target) as launch,
        pytest.raises(TypeError),
    ):
        operation(filters=filters)

    launch.assert_not_called()


@pytest.mark.parametrize(("component", "method", "target", "result"), PAIR_OPERATIONS)
def test_generator_with_invalid_entry_fails_before_cli_lookup(
    component: str, method: str, target: str, result: str | None
) -> None:
    client = DockerClient()
    operation = getattr(getattr(client, component), method)
    filters = (pair for pair in [("label", "ok"), "ab"])

    with (
        forbid_cli_lookup(),
        patch(target) as launch,
        pytest.raises(TypeError),
    ):
        operation(filters=filters)

    launch.assert_not_called()
    assert next(filters, None) is None


def test_events_rejects_filters_before_timeout_or_stream_launch() -> None:
    with (
        forbid_cli_lookup(),
        patch(
            "pywhales.components.system.cli_wrapper.stream_stdout_and_stderr"
        ) as stream,
        pytest.raises(TypeError),
    ):
        pair_operation("system", "events")(filters="event=start", timeout=5.0)

    stream.assert_not_called()


@pytest.mark.parametrize("stream_logs", [False, True])
def test_buildx_prune_passes_repeated_keys_through(stream_logs: bool) -> None:
    operation = pair_operation("buildx", "prune")
    pairs = [("until", "24h"), ("until", "48h")]
    target = (
        "pywhales.components.buildx.cli_wrapper.stream_buildx_logs"
        if stream_logs
        else "pywhales.components.buildx.cli_wrapper.run"
    )

    with patch(target) as launch:
        operation(filters=pairs, stream_logs=stream_logs)

    assert launched_filters(launch) == ["until=24h", "until=48h"]


def test_buildx_prune_stream_rejects_invalid_filters_before_launch() -> None:
    with (
        forbid_cli_lookup(),
        patch("pywhales.components.buildx.cli_wrapper.stream_buildx_logs") as stream,
        pytest.raises(TypeError),
    ):
        pair_operation("buildx", "prune")(filters="until=24h", stream_logs=True)

    stream.assert_not_called()


def test_system_prune_forwards_negated_label_pairs() -> None:
    operation = pair_operation("system", "prune")

    with patch("pywhales.components.system.cli_wrapper.run", return_value="") as run:
        operation(
            all=True,
            filters=[("label!", "keep"), ("label!", "protect"), ("until", "1h")],
        )

    assert launched_filters(run) == ["label!=keep", "label!=protect", "until=1h"]
