"""Whole-response identity checks for context, plugin, and task inspection.

Validate ordered batches before callers construct or publish resource handles.
"""

import re
from collections.abc import Sequence
from typing import assert_never

from pywhales.client_config import ParsingError, ReloadableObjectFromJson
from pywhales.components.context.models import ContextInspectResult
from pywhales.components.plugin.models import PluginInspectResult
from pywhales.components.task.models import TaskInspectResult

type NamespaceInspectResult = (
    ContextInspectResult | PluginInspectResult | TaskInspectResult
)


def _reference_identity(result: NamespaceInspectResult, reference: str) -> str:
    """Validate a namespace-specific reference and return its resolved identity.

    Context names must match exactly. Plugin and task references are compared
    exactly only when they have the full immutable-ID shape; aliases and short
    IDs are resolved by the CLI.

    Args:
        result: Parsed context, plugin, or task snapshot.
        reference: Original CLI inspect reference.

    Returns:
        The nonempty name or immutable ID from the snapshot.

    Raises:
        ParsingError: If the identity is missing or fails an exact-match check.
    """
    match result:
        case ContextInspectResult(name=identity):
            matches = reference == identity
        case PluginInspectResult(id=identity):
            matches = (
                re.fullmatch(r"[0-9a-f]{64}", reference) is None
                or reference == identity
            )
        case TaskInspectResult(id=identity):
            matches = (
                re.fullmatch(r"[0-9a-z]{25}", reference) is None
                or reference == identity
            )
        case unreachable:
            assert_never(unreachable)
    if identity is None or identity == "":
        raise ParsingError("Missing inspect identity")
    if not matches:
        raise ParsingError("Inspect identity does not match requested reference")
    return identity


def parse_namespace_inspect_results[
    InspectResultT: NamespaceInspectResult,
    ReferenceT: str | None,
](
    parser: ReloadableObjectFromJson[InspectResultT, ReferenceT],
    json_str: str,
    references: Sequence[str],
) -> list[tuple[str, InspectResultT]]:
    """Parse and validate an entire namespace inspect response in input order.

    No resource caches are updated here. Parsing follows the originating
    client's safe or raw diagnostic policy.

    Args:
        parser: Resource parser supplying its model and identity field.
        json_str: Captured batch inspection JSON.
        references: Requested references in the same order as the response.

    Returns:
        Ordered pairs of resolved identities and validated snapshots.

    Raises:
        ParsingError: If JSON, result counts, identities, or duplicate snapshots
            are invalid or inconsistent.
    """
    results = parser._parse_inspect_results(json_str, references)
    snapshots: dict[str, InspectResultT] = {}
    resolved: list[tuple[str, InspectResultT]] = []
    for reference, result in zip(references, results, strict=True):
        identity = _reference_identity(result, reference)
        if identity in snapshots and snapshots[identity] != result:
            raise ParsingError("Inconsistent inspect snapshot")
        snapshots[identity] = result
        resolved.append((identity, result))
    return resolved
