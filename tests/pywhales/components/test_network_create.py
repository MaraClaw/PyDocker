"""Network creation exercised through a local Python CLI child, never Docker."""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from pywhales import DockerClient
from pywhales.components.network.models import NetworkIPAMPool
from pywhales.exceptions import DockerException


@pytest.fixture
def fake_cli(tmp_path: Path) -> tuple[DockerClient, Path]:
    script = tmp_path / "cli.py"
    log = tmp_path / "calls.jsonl"
    script.write_text(
        "import json, sys\n"
        "with open(sys.argv[1], 'a') as output:\n"
        "    output.write(json.dumps(sys.argv[2:]) + '\\n')\n"
        "if 'invalid-cidr' in sys.argv:\n"
        "    print('invalid-cidr rejected by CLI', file=sys.stderr)\n"
        "    sys.exit(23)\n"
        "if 'create' in sys.argv:\n"
        "    print('network-id')\n"
    )
    return DockerClient(client_call=[sys.executable, str(script), str(log)]), log


def calls(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_legacy_positional_argv_and_cleanup(
    fake_cli: tuple[DockerClient, Path],
) -> None:
    client, log = fake_cli
    with client.network.create(
        "legacy", True, "bridge", "192.0.2.1", "192.0.2.0/24", {"a": "b"}, ["x=y"]
    ) as network:
        assert network.id == "network-id"
        assert network.client_config is client.client_config
    assert calls(log) == [
        [
            "network",
            "create",
            "--attachable",
            "--driver",
            "bridge",
            "--gateway",
            "192.0.2.1",
            "--subnet",
            "192.0.2.0/24",
            "--label",
            "a=b",
            "--opt",
            "x=y",
            "legacy",
        ],
        ["network", "remove", "network-id"],
    ]


@pytest.mark.parametrize("value", ["bad,value", '"bad"', "bad\nvalue"])
def test_legacy_singular_values_reach_cli_unchanged(
    fake_cli: tuple[DockerClient, Path], value: str
) -> None:
    client, log = fake_cli
    client.network.create("legacy", False, None, value, value, {}, ())
    assert calls(log) == [
        ["network", "create", "--gateway", value, "--subnet", value, "legacy"]
    ]


def test_ordered_pools_and_explicit_values(fake_cli: tuple[DockerClient, Path]) -> None:
    client, log = fake_cli
    pool = NetworkIPAMPool(
        subnet="192.0.2.0/24",
        gateway="192.0.2.1",
        auxiliary_addresses={"node": "192.0.2.3"},
    )
    client.network.create(
        "custom",
        internal=True,
        enable_ipv6=False,
        ipam_driver="custom",
        ipam_options={"zero": 0, "token": "a=b"},
        ipam_pools=[
            pool,
            NetworkIPAMPool(subnet="198.51.100.0/24", ip_range="198.51.100.128/25"),
        ],
    )
    assert calls(log) == [
        [
            "network",
            "create",
            "--internal",
            "--ipv6=false",
            "--ipam-driver",
            "custom",
            "--ipam-opt",
            "zero=0",
            "--ipam-opt",
            "token=a=b",
            "--subnet",
            "192.0.2.0/24",
            "--gateway",
            "192.0.2.1",
            "--aux-address",
            "node=192.0.2.3",
            "--subnet",
            "198.51.100.0/24",
            "--ip-range",
            "198.51.100.128/25",
            "custom",
        ]
    ]


def test_singular_conflict_precedes_lookup() -> None:
    client = DockerClient(client_call=["missing-pywhales-test-executable"])
    with pytest.raises(ValueError):
        client.network.create("name", subnet="192.0.2.0/24", ipam_pools=[])


def test_wrong_pool_reassociation_is_rejected_before_launch(
    fake_cli: tuple[DockerClient, Path],
) -> None:
    client, log = fake_cli
    with pytest.raises(ValueError):
        client.network.create(
            "name",
            ipam_pools=[
                NetworkIPAMPool(subnet="192.0.2.0/24", gateway="198.51.100.1"),
                NetworkIPAMPool(subnet="198.51.100.0/24"),
            ],
        )
    assert not log.exists()


@pytest.mark.parametrize("raw", [False, True])
def test_cli_rejection_is_not_retried_and_diagnostics_follow_config(
    fake_cli: tuple[DockerClient, Path], raw: bool
) -> None:
    client, log = fake_cli
    client.client_config.raw_diagnostics = raw
    with pytest.raises(DockerException) as raised:
        client.network.create(
            "secret-name", ipam_pools=[NetworkIPAMPool(subnet="invalid-cidr")]
        )
    assert raised.value.return_code == 23
    assert raised.value.stderr == "invalid-cidr rejected by CLI\n"
    assert ("secret-name" in str(raised.value)) is raw
    assert calls(log) == [
        ["network", "create", "--subnet", "invalid-cidr", "secret-name"]
    ]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("internal", 0),
        ("enable_ipv6", 0),
        ("ipam_driver", 5),
        ("ipam_options", []),
        ("ipam_options", {"": "a"}),
        ("ipam_options", {"a=b": "c"}),
        ("ipam_options", {"a": True}),
        ("ipam_pools", "not-a-sequence-of-pools"),
        ("ipam_pools", [{}]),
    ],
)
def test_malformed_options_fail_before_lookup(key: str, value: object) -> None:
    client = DockerClient(client_call=["missing-pywhales-test-executable"])
    kwargs = json.loads(json.dumps({key: value}))
    with pytest.raises((TypeError, ValueError)):
        client.network.create("name", **kwargs)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"subnet": 5},
        {"subnet": "x", "gateway": 5},
        {"subnet": "x", "auxiliary_addresses": {"name": 5}},
        {"subnet": "x", "extra": "field"},
    ],
)
def test_pool_structure_is_strict(data: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        NetworkIPAMPool.model_validate(data)


@pytest.mark.parametrize("value", ["x,y", '"x"', "x\ny"])
def test_csv_expansion_is_rejected(value: str) -> None:
    client = DockerClient(client_call=["missing-pywhales-test-executable"])
    with pytest.raises(ValueError):
        client.network.create("name", ipam_pools=[NetworkIPAMPool(subnet=value)])


def test_auxiliary_names_must_be_globally_unique() -> None:
    client = DockerClient(client_call=["missing-pywhales-test-executable"])
    with pytest.raises(ValueError):
        client.network.create(
            "name",
            ipam_pools=[
                NetworkIPAMPool(
                    subnet="192.0.2.0/24", auxiliary_addresses={"same": "192.0.2.3"}
                ),
                NetworkIPAMPool(
                    subnet="198.51.100.0/24",
                    auxiliary_addresses={"same": "198.51.100.3"},
                ),
            ],
        )


def test_mutated_auxiliary_mapping_is_revalidated() -> None:
    client = DockerClient(client_call=["missing-pywhales-test-executable"])
    pool = NetworkIPAMPool(
        subnet="192.0.2.0/24", auxiliary_addresses={"valid": "192.0.2.3"}
    )
    addresses = pool.auxiliary_addresses
    assert isinstance(addresses, dict)
    addresses["bad=key"] = "192.0.2.4"
    with pytest.raises(ValueError):
        client.network.create("name", ipam_pools=[pool])


def test_cli_conflicts_are_forwarded(fake_cli: tuple[DockerClient, Path]) -> None:
    client, log = fake_cli
    client.network.create(
        "name",
        ipam_pools=[
            NetworkIPAMPool(subnet="192.0.2.0/24", gateway="198.51.100.1"),
            NetworkIPAMPool(subnet="198.51.100.0/24", gateway="198.51.100.2"),
        ],
    )
    assert len(calls(log)) == 1


def test_non_cli_cidr_syntax_is_forwarded(fake_cli: tuple[DockerClient, Path]) -> None:
    client, log = fake_cli
    client.network.create(
        "name",
        ipam_pools=[
            NetworkIPAMPool(subnet="192.0.2.0/255.255.255.0", gateway="198.51.100.1"),
            NetworkIPAMPool(subnet="198.51.100.0/24"),
        ],
    )
    assert len(calls(log)) == 1


@pytest.mark.parametrize("kind", ["range", "aux"])
def test_wrong_pool_range_or_aux_is_rejected(kind: str) -> None:
    client = DockerClient(client_call=["missing-pywhales-test-executable"])
    pool = NetworkIPAMPool(
        subnet="192.0.2.0/24",
        ip_range="198.51.100.1/25" if kind == "range" else None,
        auxiliary_addresses={"node": "198.51.100.1"} if kind == "aux" else {},
    )
    with pytest.raises(ValueError):
        client.network.create(
            "name", ipam_pools=[pool, NetworkIPAMPool(subnet="198.51.100.0/24")]
        )


@pytest.mark.parametrize("kind", ["gateway", "aux"])
def test_wrong_pool_cidr_gateway_or_aux_is_rejected(kind: str) -> None:
    client = DockerClient(client_call=["missing-pywhales-test-executable"])
    pool = NetworkIPAMPool(
        subnet="192.0.2.0/24",
        gateway="198.51.100.1/0" if kind == "gateway" else None,
        auxiliary_addresses={"node": "198.51.100.1/0"} if kind == "aux" else {},
    )
    with pytest.raises(ValueError):
        client.network.create(
            "name", ipam_pools=[pool, NetworkIPAMPool(subnet="198.51.100.0/24")]
        )


@pytest.mark.parametrize("kind", ["gateway", "range", "aux"])
def test_wrong_pool_mapped_address_rejected_before_launch(
    fake_cli: tuple[DockerClient, Path], kind: str
) -> None:
    client, log = fake_cli
    pool = NetworkIPAMPool(
        subnet="192.0.2.0/24",
        gateway="::ffff:198.51.100.1" if kind == "gateway" else None,
        ip_range="::ffff:198.51.100.1/120" if kind == "range" else None,
        auxiliary_addresses={"node": "::ffff:198.51.100.1"} if kind == "aux" else {},
    )
    with pytest.raises(ValueError):
        client.network.create(
            "name", ipam_pools=[pool, NetworkIPAMPool(subnet="198.51.100.0/24")]
        )
    assert not log.exists()


@pytest.mark.parametrize(
    ("kind", "option", "value"),
    [
        ("gateway", "--gateway", "::ffff:192.0.2.1"),
        ("range", "--ip-range", "::ffff:192.0.2.1/120"),
        ("aux", "--aux-address", "node=::ffff:192.0.2.1"),
    ],
)
def test_correct_pool_mapped_address_preserves_argv(
    fake_cli: tuple[DockerClient, Path], kind: str, option: str, value: str
) -> None:
    client, log = fake_cli
    address = value.removeprefix("node=")
    pool = NetworkIPAMPool(
        subnet="192.0.2.0/24",
        gateway=address if kind == "gateway" else None,
        ip_range=address if kind == "range" else None,
        auxiliary_addresses={"node": address} if kind == "aux" else {},
    )
    client.network.create("name", ipam_pools=[pool])
    assert calls(log) == [
        ["network", "create", "--subnet", "192.0.2.0/24", option, value, "name"]
    ]


def test_wrong_pool_mapped_subnet_rejected_before_launch(
    fake_cli: tuple[DockerClient, Path],
) -> None:
    client, log = fake_cli
    with pytest.raises(ValueError):
        client.network.create(
            "name",
            ipam_pools=[
                NetworkIPAMPool(subnet="192.0.2.0/24", gateway="198.51.100.1"),
                NetworkIPAMPool(subnet="::ffff:198.51.100.0/120"),
            ],
        )
    assert not log.exists()


@pytest.mark.parametrize("prefix", [96, 120])
def test_correct_mapped_subnet_preserves_argv(
    fake_cli: tuple[DockerClient, Path], prefix: int
) -> None:
    client, log = fake_cli
    subnet = f"::ffff:192.0.2.0/{prefix}"
    client.network.create(
        "name", ipam_pools=[NetworkIPAMPool(subnet=subnet, gateway="192.0.2.1")]
    )
    assert calls(log) == [
        ["network", "create", "--subnet", subnet, "--gateway", "192.0.2.1", "name"]
    ]


def test_mapped_network_overlap_is_forwarded_to_cli(
    fake_cli: tuple[DockerClient, Path],
) -> None:
    client, log = fake_cli
    client.network.create(
        "name",
        ipam_pools=[
            NetworkIPAMPool(subnet="192.0.2.0/24", gateway="198.51.100.1"),
            NetworkIPAMPool(subnet="::ffff:198.51.100.0/96"),
        ],
    )
    assert len(calls(log)) == 1


def test_mapped_subnet_below_96_uses_masked_network_identity(
    fake_cli: tuple[DockerClient, Path],
) -> None:
    client, log = fake_cli
    with pytest.raises(ValueError):
        client.network.create(
            "name",
            ipam_pools=[
                NetworkIPAMPool(subnet="::ffff:0:0/95", gateway="::ffff:198.51.100.1"),
                NetworkIPAMPool(subnet="198.51.100.0/24"),
            ],
        )
    assert not log.exists()


def test_cidr_association_uses_written_ip(fake_cli: tuple[DockerClient, Path]) -> None:
    client, log = fake_cli
    client.network.create(
        "name",
        ipam_pools=[
            NetworkIPAMPool(subnet="192.0.2.0/24", ip_range="192.0.2.10/16"),
            NetworkIPAMPool(subnet="198.51.100.0/24"),
        ],
    )
    assert calls(log)[0][5] == "192.0.2.10/16"


def test_invalid_aux_address_value_with_equals_reaches_cli(
    fake_cli: tuple[DockerClient, Path],
) -> None:
    client, log = fake_cli
    client.network.create(
        "name",
        ipam_pools=[
            NetworkIPAMPool(subnet="192.0.2.0/24", auxiliary_addresses={"node": "x=y"})
        ],
    )
    assert calls(log)[0][-3:] == ["--aux-address", "node=x=y", "name"]


def test_explicit_false_and_true_are_distinct_from_none(
    fake_cli: tuple[DockerClient, Path],
) -> None:
    client, log = fake_cli
    client.network.create("name", internal=False, enable_ipv6=True)
    assert calls(log) == [["network", "create", "--internal=false", "--ipv6", "name"]]
