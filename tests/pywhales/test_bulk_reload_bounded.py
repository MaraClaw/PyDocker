import sys
from collections.abc import Callable, Sequence
from threading import Event
from unittest.mock import patch

import pytest

from pywhales import (
    Context,
    InspectionPolicy,
    ObservationCancelled,
    ObservationLimitError,
    observation,
)
from pywhales._observation import get_observation
from pywhales.client_config import ClientConfig, bulk_reload
from pywhales.inventory import InventoryResource


def _inspect_contexts(
    resources: Sequence[InventoryResource],
) -> list[InventoryResource]:
    result: list[InventoryResource] = []
    for resource in resources:
        assert isinstance(resource, Context)
        staged = Context(
            resource.client_config, resource._get_immutable_id(), is_immutable_id=True
        )
        staged._set_inspect_result(
            staged._parse_inspect_result({"Name": resource._get_immutable_id()})
        )
        result.append(staged)
    return result


def test_explicit_policy_stages_chunks_and_publishes_distinct_handles() -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    first = Context(config, "first", is_immutable_id=True)
    second = Context(config, "second", is_immutable_id=True)
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=10000, max_response_bytes=10000
    )

    with patch(
        "pywhales._inventory_staging.inspect_group", side_effect=_inspect_contexts
    ) as inspect:
        bulk_reload([first, second, first], policy=policy)

    assert inspect.call_count == 2
    assert first._inspect_result is not None
    assert second._inspect_result is not None


def test_ambient_and_explicit_limits_intersect() -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    resources = [Context(config, name, is_immutable_id=True) for name in ("a", "b")]
    outer = InspectionPolicy(
        max_references=1, max_argv_bytes=10000, max_response_bytes=10000
    )
    inner = InspectionPolicy(
        max_references=5, max_argv_bytes=20000, max_response_bytes=20000
    )
    observed: list[InspectionPolicy] = []

    def inspect(resources: Sequence[InventoryResource]) -> list[InventoryResource]:
        state = get_observation()
        assert state is not None
        observed.append(state.policy)
        return _inspect_contexts(resources)

    with (
        observation(outer),
        patch("pywhales._inventory_staging.inspect_group", side_effect=inspect),
    ):
        bulk_reload(resources, policy=inner)

    assert observed == [outer, outer]
    assert all(resource._inspect_result is not None for resource in resources)


def test_ambient_policy_alone_selects_bounded_staging() -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    resources = [Context(config, name, is_immutable_id=True) for name in ("a", "b")]
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=10000, max_response_bytes=10000
    )

    with (
        observation(policy),
        patch(
            "pywhales._inventory_staging.inspect_group", side_effect=_inspect_contexts
        ) as inspect,
    ):
        bulk_reload(resources)

    assert inspect.call_count == 2
    assert all(resource._inspect_result is not None for resource in resources)


def test_later_group_limit_failure_precedes_all_inspection_and_publication() -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    first = Context(config, "first", is_immutable_id=True)
    second = Context(
        ClientConfig(_client_call_with_path=["docker-test"]),
        "x" * 1000,
        is_immutable_id=True,
    )
    policy = InspectionPolicy(
        max_references=5, max_argv_bytes=100, max_response_bytes=10000
    )
    before = [
        (resource._inspect_result, resource._last_refreshed_time)
        for resource in (first, second)
    ]

    with (
        patch("pywhales._inventory_staging.inspect_group") as inspect,
        pytest.raises(ObservationLimitError),
    ):
        bulk_reload([first, second], policy=policy)

    inspect.assert_not_called()
    assert [
        (resource._inspect_result, resource._last_refreshed_time)
        for resource in (first, second)
    ] == before


def test_cancellation_during_preparation_preserves_all_caches() -> None:
    config = ClientConfig(_client_call_with_path=["docker-test"])
    resources = [Context(config, name, is_immutable_id=True) for name in ("a", "b")]
    policy = InspectionPolicy(
        max_references=5, max_argv_bytes=10000, max_response_bytes=10000
    )
    cancel = Event()
    before = [
        (resource._inspect_result, resource._last_refreshed_time)
        for resource in resources
    ]
    from pywhales._bulk_reload import _prepare_publication

    def prepare(
        original: InventoryResource, staged: InventoryResource
    ) -> Callable[[], None]:
        callback = _prepare_publication(original, staged)
        cancel.set()
        return callback

    with (
        pytest.raises(ObservationCancelled),
        observation(policy, cancel=cancel),
        patch(
            "pywhales._inventory_staging.inspect_group", side_effect=_inspect_contexts
        ),
        patch("pywhales._bulk_reload._prepare_publication", side_effect=prepare),
    ):
        bulk_reload(resources, policy=policy)

    assert [
        (resource._inspect_result, resource._last_refreshed_time)
        for resource in resources
    ] == before


def test_bounded_bulk_reload_runs_inspections_through_local_cli() -> None:
    code = 'import json,sys; print(json.dumps([{"Name": value} for value in sys.argv[3:]]))'
    config = ClientConfig(_client_call_with_path=[sys.executable, "-c", code])
    resources = [Context(config, name, is_immutable_id=True) for name in ("a", "b")]
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=10000, max_response_bytes=10000
    )

    bulk_reload(resources, policy=policy)

    assert [
        resource._inspect_result.name
        for resource in resources
        if resource._inspect_result
    ] == ["a", "b"]


def test_response_limit_preserves_caches_after_earlier_chunk_succeeds() -> None:
    code = 'import json,sys; print(json.dumps([{"Name": value} for value in sys.argv[3:]]))'
    config = ClientConfig(_client_call_with_path=[sys.executable, "-c", code])
    resources = [
        Context(config, name, is_immutable_id=True) for name in ("a", "b" * 200)
    ]
    policy = InspectionPolicy(
        max_references=1, max_argv_bytes=10000, max_response_bytes=100
    )
    before = [
        (resource._inspect_result, resource._last_refreshed_time)
        for resource in resources
    ]

    with pytest.raises(ObservationLimitError):
        bulk_reload(resources, policy=policy)

    assert [
        (resource._inspect_result, resource._last_refreshed_time)
        for resource in resources
    ] == before
