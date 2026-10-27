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
