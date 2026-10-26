from collections import Counter
from collections.abc import Sequence
from typing import assert_never

from pywhales.client_config import ParsingError, ReloadableObjectFromJson
from pywhales.components.config.models import ConfigInspectResult
from pywhales.components.node.models import NodeInspectResult
from pywhales.components.secret.models import SecretInspectResult
from pywhales.components.service.models import ServiceInspectResult

type SwarmInspectResult = (
    ConfigInspectResult | NodeInspectResult | SecretInspectResult | ServiceInspectResult
)


def _inspect_name(result: SwarmInspectResult) -> str | None:
    match result:
        case NodeInspectResult(description=description):
            return description.hostname if description is not None else None
        case (
            ConfigInspectResult(spec=spec)
            | SecretInspectResult(spec=spec)
            | ServiceInspectResult(spec=spec)
        ):
            return spec.name if spec is not None else None
        case unreachable:
            assert_never(unreachable)


def parse_swarm_inspect_results[InspectResultT: SwarmInspectResult](
    parser: ReloadableObjectFromJson[InspectResultT],
    json_str: str,
    references: Sequence[str],
) -> list[tuple[str, InspectResultT]]:
    """Validate response slots, then resolve references independently of their order.

    Unique slot labels keep the shared parser's positional duplicate check from
    associating repeated request references with unrelated reordered responses.
    Identity consistency and multiplicity are checked after reference resolution.
    """
    results = parser._parse_inspect_results(
        json_str, [str(index) for index in range(len(references))]
    )
    snapshots: dict[str, InspectResultT] = {}
    identities: list[str] = []
    for result in results:
        identity = result.id
        if identity is None or identity == "":
            raise ParsingError("Missing Swarm inspect identity")
        if identity in snapshots and snapshots[identity] != result:
            raise ParsingError(f"Inconsistent inspect snapshot for {identity!r}")
        snapshots[identity] = result
        identities.append(identity)

    resolved: list[tuple[str, InspectResultT]] = []
    for reference in references:
        if reference in snapshots:
            resolved.append((reference, snapshots[reference]))
            continue
        matches = [
            (identity, result)
            for identity, result in snapshots.items()
            if reference and _inspect_name(result) == reference
        ]
        if not matches:
            matches = [
                (identity, result)
                for identity, result in snapshots.items()
                if reference and identity.startswith(reference)
            ]
        if len(matches) != 1:
            raise ParsingError(
                f"Expected one inspect identity for {reference!r}, received {len(matches)}"
            )
        resolved.append(matches[0])
    if Counter(identity for identity, _ in resolved) != Counter(identities):
        raise ParsingError("Inspect identities do not match requested multiplicities")
    return resolved
