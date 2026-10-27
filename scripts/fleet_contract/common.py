"""Strict contract primitives and owned unresolved decisions."""

from typing import Annotated, Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    model_validator,
)
from pydantic_core import PydanticCustomError

type Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
type Token = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]{0,79}$")]
type Count = Annotated[int, Field(ge=0)]
type PositiveCount = Annotated[int, Field(gt=0)]
type Amount = Annotated[float, Field(ge=0, allow_inf_nan=False)]
type Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
type Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
type Digest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]
type SchemaVersion = Annotated[int, Field(ge=1, le=1)]
type Nonempty[T] = Annotated[tuple[T, ...], Field(min_length=1)]


class Contract(BaseModel):
    """Frozen JSON boundary with strict finite values and no extension keys."""

    model_config = ConfigDict(
        strict=True, extra="forbid", frozen=True, allow_inf_nan=False
    )


class Pending(Contract):
    """An unresolved value referring to an owned decision, never a default."""

    decision_id: Token


class Decision(Contract):
    """An unresolved input's owner, rationale and precise blocking effect."""

    id: Token
    field: Text
    owner: Text
    rationale: Text
    blocks: Literal["live_run", "qualification", "legacy_compatibility"]


JSON: Final = TypeAdapter(JsonValue)


def pending_fields(value: JsonValue, path: str = "") -> tuple[tuple[str, str], ...]:
    """Find unresolved markers in already parsed, schema-known JSON fields."""
    found: list[tuple[str, str]] = []
    match value:
        case dict():
            marker = value.get("decision_id")
            if isinstance(marker, str) and len(value) == 1:
                found.append((path, marker))
            else:
                for name, child in value.items():
                    found.extend(pending_fields(child, f"{path}.{name}".lstrip(".")))
        case list():
            for index, child in enumerate(value):
                found.extend(pending_fields(child, f"{path}.{index}"))
        case str() | int() | float() | bool() | None:
            return ()
    return tuple(found)


def check_decisions(model: Contract, decisions: tuple[Decision, ...]) -> None:
    """Require every unresolved location to have exactly one matching decision."""
    references = pending_fields(JSON.validate_json(model.model_dump_json()))
    actual = {(decision.field, decision.id) for decision in decisions}
    if (
        set(references) != actual
        or len(actual) != len(decisions)
        or len({decision.id for decision in decisions}) != len(decisions)
    ):
        raise PydanticCustomError("decision_reference", "decision_reference")


class Distribution(Contract):
    """Finite bounded distribution in the named unit; empirical data is explicit."""

    shape: Literal["constant", "uniform", "empirical"]
    unit: Literal["seconds", "bytes", "bytes_per_second", "operations_per_second"]
    minimum: Amount
    maximum: Amount
    samples: tuple[Amount, ...]

    @classmethod
    def invalid(cls) -> PydanticCustomError:
        """Return a fixed, input-independent bound error."""
        return PydanticCustomError("distribution_bounds", "distribution_bounds")

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        """Check bounds and the distribution's required empirical samples."""
        if self.minimum > self.maximum:
            raise self.invalid()
        match self.shape:
            case "constant":
                if self.minimum != self.maximum or self.samples:
                    raise self.invalid()
            case "uniform":
                if self.samples:
                    raise self.invalid()
            case "empirical":
                if not self.samples or any(
                    not self.minimum <= sample <= self.maximum
                    for sample in self.samples
                ):
                    raise self.invalid()
        return self
