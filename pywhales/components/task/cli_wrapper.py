from __future__ import annotations

import builtins
import json
from datetime import datetime
from typing import override

from pydantic import JsonValue

import pywhales.components.service.cli_wrapper
from pywhales.client_config import (
    ClientConfig,
    DockerCLICaller,
    ReloadableObjectFromJson,
)
from pywhales.components.task.models import (
    AssignedGenericResources,
    ObjectVersion,
    TaskInspectResult,
    TaskSpec,
    TaskStatus,
)
from pywhales.enums import TaskState
from pywhales.utils import custom_parse_object_as, run


class Task(ReloadableObjectFromJson[TaskInspectResult]):
    def __init__(
        self, client_config: ClientConfig, reference: str, is_immutable_id: bool = False
    ) -> None:
        super().__init__(client_config, "id", reference, is_immutable_id)

    @override
    def _fetch_inspect_result_json(self, reference: str) -> dict[str, JsonValue]:
        json_str = run([*self.docker_cmd, "inspect", reference])
        return custom_parse_object_as(dict[str, JsonValue], {**json.loads(json_str)[0]})

    @override
    def _parse_json_object(
        self, json_object: dict[str, JsonValue]
    ) -> TaskInspectResult:
        return TaskInspectResult.model_validate(json_object)

    @property
    def id(self) -> str:
        return self._get_immutable_id()

    @property
    def version(self) -> ObjectVersion | None:
        return self._get_inspect_result().version

    @property
    def created_at(self) -> datetime | None:
        return self._get_inspect_result().created_at

    @property
    def updated_at(self) -> datetime | None:
        return self._get_inspect_result().updated_at

    @property
    def name(self) -> str | None:
        return self._get_inspect_result().name

    @property
    def labels(self) -> dict[str, str] | None:
        return self._get_inspect_result().labels

    @property
    def spec(self) -> TaskSpec | None:
        return self._get_inspect_result().spec

    @property
    def service_id(self) -> str | None:
        return self._get_inspect_result().service_id

    @property
    def slot(self) -> int | None:
        return self._get_inspect_result().slot

    @property
    def node_id(self) -> str | None:
        return self._get_inspect_result().node_id

    @property
    def assigned_generic_resources(self) -> list[AssignedGenericResources] | None:
        return self._get_inspect_result().assigned_generic_resources

    @property
    def status(self) -> TaskStatus | None:
        return self._get_inspect_result().status

    @property
    def desired_state(self) -> TaskState | str | None:
        return self._get_inspect_result().desired_state

    @override
    def __repr__(self) -> str:
        return f"pywhales.Task(id='{self.id[:12]}', name={self.name})"


class TaskCLI(DockerCLICaller):
    def list(self) -> builtins.list[Task]:
        """Returns all tasks in the swarm

        # Returns
            `List[pywhales.Task]`
        """
        service_cli = pywhales.components.service.cli_wrapper.ServiceCLI(
            self.client_config
        )
        return service_cli.ps(service_cli.list())

    def inspect(self, x: str | builtins.list[str]) -> Task | builtins.list[Task]:
        """Returns a `pywhales.Task` object from its ID."""
        if isinstance(x, str):
            return Task(self.client_config, x)
        else:
            return [Task(self.client_config, reference) for reference in x]

    def logs(self) -> None:
        """Not Yet implemented"""
        raise NotImplementedError
