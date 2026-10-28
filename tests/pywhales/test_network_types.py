"""Static contracts for network creation; checked by ty, not pytest."""

from collections.abc import Mapping, Sequence
from typing import assert_type

from pywhales import DockerClient, Network
from pywhales.components.network.models import NetworkIPAMPool


def _check_network_create(
    client: DockerClient,
    pools: Sequence[NetworkIPAMPool],
    options: Mapping[str, str | int],
) -> None:
    pool = NetworkIPAMPool(
        subnet="192.0.2.0/24", auxiliary_addresses={"node": "192.0.2.1"}
    )
    assert_type(pool.subnet, str)
    assert_type(pool.auxiliary_addresses, Mapping[str, str])
    assert_type(
        client.network.create(
            "name",
            True,
            "bridge",
            None,
            None,
            {},
            (),
            internal=False,
            enable_ipv6=True,
            ipam_driver="default",
            ipam_options=options,
            ipam_pools=pools,
        ),
        Network,
    )
