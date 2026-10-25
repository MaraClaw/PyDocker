import json
from pathlib import Path

import pytest

from pydocker import docker
from pydocker.components.task.models import TaskInspectResult
from pydocker.test_utils import get_all_jsons


@pytest.mark.parametrize("json_file", get_all_jsons("tasks"))
def test_load_json(json_file: Path) -> None:
    json_as_txt = json_file.read_text()
    TaskInspectResult(**json.loads(json_as_txt))
    # we could do more checks here if needed


@pytest.mark.usefixtures("swarm_mode")
def test_list_tasks() -> None:
    service = docker.service.create("busybox", ["sleep", "infinity"])

    # Todo: use a context manager
    tasks = docker.task.list()
    repr(tasks)
    assert len(tasks) >= 1
    assert tasks[0].desired_state == "running"
    assert tasks[0].service_id == service.id
    docker.service.remove(service)
