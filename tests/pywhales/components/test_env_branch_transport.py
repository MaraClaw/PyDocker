"""Verify container assignments at the real child boundary, without Docker."""

import sys
from pathlib import Path
from typing import Literal, assert_never

import pytest
from pydantic import BaseModel

from pywhales import DockerClient
from pywhales.utils import ProcessStream

type Operation = Literal[
    "create", "run", "execute", "service", "compose_run", "compose_execute"
]
type Mode = Literal["captured", "streamed", "detached", "tty"]


class ChildRecord(BaseModel):
    argv: list[str]
    environment: dict[str, str]


@pytest.mark.parametrize(
    "operation,mode",
    [
        ("create", "captured"),
        ("run", "captured"),
        ("run", "streamed"),
        ("run", "detached"),
        ("run", "tty"),
        ("execute", "captured"),
        ("execute", "streamed"),
        ("execute", "detached"),
        ("execute", "tty"),
        ("service", "captured"),
        ("service", "detached"),
        ("compose_run", "captured"),
        ("compose_run", "streamed"),
        ("compose_run", "detached"),
        ("compose_run", "tty"),
        ("compose_execute", "captured"),
        ("compose_execute", "detached"),
        ("compose_execute", "tty"),
    ],
)
def test_assignments_reach_child_without_value_arguments(
    operation: Operation, mode: Mode, tmp_path: Path
) -> None:
    # Given: a fake CLI that independently records each child's argv and environment.
    driver = tmp_path / "driver.py"
    records = tmp_path / "records.jsonl"
    driver.write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[2:]\n"
        "with Path(sys.argv[1]).open('a') as output:\n"
        "    output.write(json.dumps({'argv': args, 'environment': dict(os.environ)}) + '\\n')\n"
        "if args[:2] == ['container', 'inspect']:\n"
        '    print(\'[{"Id":"container-id"}]\')\n'
        "else:\n"
        "    print('container-id')\n"
    )
    client = DockerClient(
        client_call=[sys.executable, str(driver), str(records)],
        environment={
            "PYWHALES_BRANCH_COLLISION": "client-value",
            "DOCKER_HOST": "client-endpoint",
        },
    )
    assignments = {
        "PYWHALES_BRANCH_SECRET": "unique-secret-transport-value",
        "PYWHALES_BRANCH_EMPTY": "",
        "PYWHALES_BRANCH_MULTILINE": "first=second\nthird",
        "PYWHALES_BRANCH_COLLISION": "container-value",
        "DOCKER_HOST": "container-endpoint",
    }
    stream = mode == "streamed"
    detach = mode == "detached"
    tty = mode == "tty"

    # When: invoke each public wrapper through every supported output branch.
    match operation:
        case "create":
            result = client.container.create("app", envs=assignments, pull="never")
        case "run":
            result = client.run(
                "app",
                envs=assignments,
                pull="never",
                stream=stream,
                detach=detach,
                tty=tty,
            )
        case "execute":
            result = client.execute(
                "container-id",
                ["cmd"],
                envs=assignments,
                stream=stream,
                detach=detach,
                tty=tty,
            )
        case "service":
            result = client.service.create(
                "app", ["cmd"], envs=assignments, detach=detach
            )
        case "compose_run":
            result = client.compose.run(
                "app",
                envs=assignments,
                stream=stream,
                detach=detach,
                tty=tty,
            )
        case "compose_execute":
            result = client.compose.execute(
                "app",
                ["cmd"],
                envs=assignments,
                detach=detach,
                tty=tty,
            )
        case unreachable:
            assert_never(unreachable)
    if isinstance(result, ProcessStream):
        with result:
            list(result)

    # Then: values arrive independently of argv, including empty and multiline values.
    launches = [
        ChildRecord.model_validate_json(line)
        for line in records.read_text().splitlines()
    ]
    launch = next(record for record in launches if "--env" in record.argv)
    options = [
        launch.argv[index + 1]
        for index, word in enumerate(launch.argv)
        if word == "--env"
    ]
    assert options == [
        "PYWHALES_BRANCH_SECRET",
        "PYWHALES_BRANCH_EMPTY",
        "PYWHALES_BRANCH_MULTILINE",
        "PYWHALES_BRANCH_COLLISION=container-value",
        "DOCKER_HOST=container-endpoint",
    ]
    for key in (
        "PYWHALES_BRANCH_SECRET",
        "PYWHALES_BRANCH_EMPTY",
        "PYWHALES_BRANCH_MULTILINE",
    ):
        assert launch.environment[key] == assignments[key]
        assert f"{key}={assignments[key]}" not in launch.argv
    assert assignments["PYWHALES_BRANCH_SECRET"] not in " ".join(launch.argv)
    assert launch.environment["PYWHALES_BRANCH_COLLISION"] == "client-value"
    assert launch.environment["DOCKER_HOST"] == "client-endpoint"
