"""Declared workload, identity and measurement requirements."""

from typing import Literal, Self

from pydantic import model_validator
from pydantic_core import PydanticCustomError

from .common import (
    Amount,
    Contract,
    Count,
    Digest,
    Distribution,
    Fraction,
    Nonempty,
    Positive,
    PositiveCount,
    Text,
    Token,
)

type Boundary = Literal["queue_wait", "selected_cli", "readiness", "recovery"]
type Action = Literal[
    "inspect", "create", "start", "stop", "remove", "pull", "build", "fault", "cleanup"
]
type Resource = Literal[
    "cpu_cores",
    "rss_bytes",
    "file_descriptors",
    "children",
    "disk_bytes",
    "disk_inodes",
]


class Owners(Contract):
    """Decision authority for every manifest group, not credentials."""

    maintainer: Text
    operator: Text
    workload: Text
    platform: Text
    topology: Text
    retention: Text
    measurements: Text
    budgets: Text
    authorization: Text


class Artifact(Contract):
    """Permitted credential-free artifact reference with access and retention."""

    destination: Token
    access: Literal["maintainers", "operators", "public_synthetic"]
    retention_seconds: Amount


class Identity(Contract):
    """Immutable workload identity and approved artifact destinations."""

    profile: Token
    revision: Digest
    workload_digest: Digest
    owners: Owners
    artifacts: Nonempty[Artifact]


class Image(Contract):
    """Content-addressed image and expected cache/size state."""

    digest: Digest
    size_bytes: PositiveCount
    cache: Literal["cold", "warm", "mixed"]


class Operation(Contract):
    """Action share and simultaneous submissions, independent of fleet residency."""

    action: Action
    fraction: Fraction
    concurrency: PositiveCount


class Workload(Contract):
    """Resident populations, churn, operation pressure and bounded termination."""

    resident_containers: Count
    service_replicas: Count
    inflight_operations: PositiveCount
    concurrent_submissions: PositiveCount
    endpoint_aliases: Nonempty[Token]
    arrivals: Distribution
    churn: Distribution
    operation_mix: Nonempty[Operation]
    images: Nonempty[Image]
    output_bytes: Distribution
    logs_bytes_per_second: Distribution
    duration_seconds: Positive
    termination: Literal["duration", "operation_count"]
    termination_operations: PositiveCount | None

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Reject impossible concurrency, units, mix and termination declarations."""
        if (
            self.concurrent_submissions > self.inflight_operations
            or abs(sum(op.fraction for op in self.operation_mix) - 1) > 1e-9
            or len({op.action for op in self.operation_mix}) != len(self.operation_mix)
            or any(
                op.concurrency > self.concurrent_submissions
                for op in self.operation_mix
            )
            or self.arrivals.unit != "operations_per_second"
            or self.churn.unit != "operations_per_second"
            or self.output_bytes.unit != "bytes"
            or self.logs_bytes_per_second.unit != "bytes_per_second"
        ):
            raise PydanticCustomError("workload_bounds", "workload_bounds")
        match self.termination:
            case "duration":
                if self.termination_operations is not None:
                    raise PydanticCustomError("termination_mode", "termination_mode")
            case "operation_count":
                if self.termination_operations is None:
                    raise PydanticCustomError("termination_mode", "termination_mode")
        return self


class Measurement(Contract):
    """Separate clock boundary, population and full outcome denominator."""

    boundary: Boundary
    unit: Literal["seconds"]
    population: Literal[
        "all_submissions", "launched_cli", "readiness_attempts", "faults"
    ]
    start_event: Text
    end_event: Text
    denominator: Literal["all_population_outcomes"]
    percentiles: Nonempty[Fraction]

    @model_validator(mode="after")
    def coherent(self) -> Self:
        """Keep local CLI, readiness, queue and recovery populations distinct."""
        populations = {
            "queue_wait": "all_submissions",
            "selected_cli": "launched_cli",
            "readiness": "readiness_attempts",
            "recovery": "faults",
        }
        if self.population != populations[self.boundary] or len(
            set(self.percentiles)
        ) != len(self.percentiles):
            raise PydanticCustomError(
                "measurement_population", "measurement_population"
            )
        return self


class Measurements(Contract):
    """Required latency boundaries and resource high-water observations."""

    latency: Nonempty[Measurement]
    resource_high_water: Nonempty[Resource]

    @model_validator(mode="after")
    def complete(self) -> Self:
        """Require all four distinct boundaries and unique resource metrics."""
        if (
            {item.boundary for item in self.latency}
            != {"queue_wait", "selected_cli", "readiness", "recovery"}
            or len(self.latency) != 4
            or len(set(self.resource_high_water)) != len(self.resource_high_water)
        ):
            raise PydanticCustomError(
                "measurement_boundaries", "measurement_boundaries"
            )
        return self
