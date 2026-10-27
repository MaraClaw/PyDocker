"""Validate, normalize and export contributor contracts without external contact.

Run from the checkout with ``uv run --locked python -m scripts.fleet_contract``.
Only the explicitly named UTF-8 input file is read; no subprocess or network
client is imported. Successful normalized output is not a credential sanitizer.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final, NoReturn, override

from pydantic import BaseModel, ValidationError

from .manifest import Manifest
from .report import Evidence, classify
from .results import RunResult
from .support import SupportMatrix

MODELS: Final[dict[str, type[BaseModel]]] = {
    "manifest": Manifest,
    "support": SupportMatrix,
    "result": RunResult,
    "evidence": Evidence,
}


class SafeParser(argparse.ArgumentParser):
    """Reject argument errors without echoing supplied values or file paths."""

    @override
    def error(self, message: str) -> NoReturn:
        """Emit only a fixed argument error code."""
        print('{"errors":[{"code":"arguments","path":"$"}]}', file=sys.stderr)
        raise SystemExit(2)


def safe_errors(error: ValidationError, model: type[BaseModel]) -> str:
    """Expose only fixed error codes and vocabulary from the model's schema."""
    schema = model.model_json_schema()
    names = set(schema.get("properties", {}))
    for definition in schema.get("$defs", {}).values():
        names.update(definition.get("properties", {}))
    errors = []
    for item in error.errors(
        include_input=False, include_context=False, include_url=False
    ):
        path = [
            str(part) if isinstance(part, int) else (part if part in names else "field")
            for part in item["loc"]
        ]
        code = "missing" if item["type"] == "missing" else "invalid"
        errors.append({"code": code, "path": ".".join(("$", *path))})
    return json.dumps({"errors": errors}, sort_keys=True)


def load[T: BaseModel](model: type[T], path: Path) -> T | None:
    """Read one explicit file and report sanitized boundary errors."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError, UnicodeError:
        print('{"errors":[{"code":"input_unreadable","path":"$"}]}', file=sys.stderr)
        return None
    try:
        return model.model_validate_json(raw)
    except ValidationError as error:
        print(safe_errors(error, model), file=sys.stderr)
        return None


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the offline CLI, returning two for safe boundary errors."""
    parser = SafeParser(
        description="Offline fleet contracts; never authorizes or qualifies a run."
    )
    parser.add_argument("command", choices=("validate", "schema", "report"))
    parser.add_argument("kind", choices=tuple(MODELS), nargs="?", default="manifest")
    parser.add_argument("--input", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--support", type=Path)
    options = parser.parse_args(arguments)
    model = MODELS[options.kind]
    if options.command == "schema":
        print(json.dumps(model.model_json_schema(), sort_keys=True, indent=2))
        return 0
    if options.input is None:
        parser.error("arguments")
    if options.command == "report":
        if options.manifest is None or options.support is None:
            parser.error("arguments")
        manifest = load(Manifest, options.manifest)
        support = load(SupportMatrix, options.support)
        result = load(RunResult, options.input)
        if manifest is None or support is None or result is None:
            return 2
        print(
            classify(
                Evidence(manifest=manifest, support=support, result=result)
            ).model_dump_json(indent=2)
        )
    else:
        parsed = load(model, options.input)
        if parsed is None:
            return 2
        print(parsed.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
