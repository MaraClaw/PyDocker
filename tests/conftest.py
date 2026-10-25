import logging
import subprocess
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import Literal, assert_never

import pytest

from pydocker import DockerClient

logger = logging.getLogger(__name__)

#
# -----------------------------------------------------------------------------
# Fixtures


def _get_ctr_client(
    client_type: Literal["docker", "podman"], pytestconfig: pytest.Config
) -> DockerClient:
    ctr_exe = pytestconfig.getoption(f"--{client_type}-exe")
    assert isinstance(ctr_exe, str)
    client = DockerClient(client_call=[ctr_exe], client_type=client_type)
    try:
        # TODO: Implement 'DockerClient.version' and use that instead.
        subprocess.run(
            [ctr_exe, "version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ) as exc:
        match exc:
            case subprocess.CalledProcessError():
                reason = "\n" + exc.stderr.strip()
            case subprocess.TimeoutExpired():
                reason = f"timed out after {exc.timeout} seconds"
            case FileNotFoundError():
                reason = "executable not found"
            case unreachable:
                assert_never(unreachable)
        logger.warning(
            "Unable to get %s version with command '%s version': %s",
            client_type,
            ctr_exe,
            reason,
        )
        if pytestconfig.getoption("--no-runtime-skip"):
            pytest.fail(f"{client_type} unavailable")
        else:
            pytest.skip(f"{client_type} unavailable")

    return client


@pytest.fixture(scope="session")
def docker_client(pytestconfig: pytest.Config) -> DockerClient:
    return _get_ctr_client("docker", pytestconfig)


@pytest.fixture(scope="session")
def podman_client(
    pytestconfig: pytest.Config, request: pytest.FixtureRequest
) -> DockerClient:
    return _get_ctr_client("podman", pytestconfig)


# This is function-scoped so that all of a testcase's parameterisations run
# in a row, before running other testcases.
@pytest.fixture(scope="function")
def ctr_client(request: pytest.FixtureRequest) -> DockerClient:
    """Allows to parametrize a test with the container runtime as a string."""
    client = request.getfixturevalue(f"{request.param}_client")
    assert isinstance(client, DockerClient)
    return client


@pytest.fixture(scope="function")
def docker_registry(docker_client: DockerClient) -> Generator[str]:
    yield from _docker_registry(docker_client)


@pytest.fixture(scope="function")
def docker_registry_without_login(
    docker_client: DockerClient,
) -> Generator[str]:
    yield from _docker_registry(docker_client, login=False)


def _docker_registry(docker_client: DockerClient, login: bool = True) -> Generator[str]:
    encrypted_password = docker_client.run(
        "mhenry07/apache2-utils",
        ["htpasswd", "-Bbn", "my_user", "my_password"],
        remove=True,
    )
    with tempfile.TemporaryDirectory() as tmp_path:
        tmp_path = Path(tmp_path)
        htpasswd_file = tmp_path / "htpasswd"
        htpasswd_file.write_text(encrypted_password)
        registry = docker_client.container.create(
            "registry:2",
            remove=True,
            envs={
                "REGISTRY_AUTH": "htpasswd",
                "REGISTRY_AUTH_HTPASSWD_REALM": "Registry Realm",
                "REGISTRY_AUTH_HTPASSWD_PATH": "/tmp/htpasswd",
            },
            publish=[(5000, 5000)],
        )
        with registry:
            registry.copy_to(htpasswd_file, "/tmp/htpasswd")
            registry.start()
            time.sleep(1.5)
            if login:
                docker_client.login(
                    "localhost:5000", username="my_user", password="my_password"
                )
            yield "localhost:5000"


@pytest.fixture(scope="function")
def swarm_mode(docker_client: DockerClient) -> Generator[None]:
    docker_client.swarm.init(advertise_address="172.31.155.129")
    yield
    docker_client.swarm.leave(force=True)
    time.sleep(1)


#
# -----------------------------------------------------------------------------
# Pytest hooks


def pytest_addoption(parser: pytest.Parser) -> None:
    """Pytest hook for adding CLI options."""

    pydocker_group = parser.getgroup("pydocker")
    for name in ("docker", "podman"):
        pydocker_group.addoption(
            f"--{name}-exe",
            metavar="EXE",
            default=name,
            help=f"Path to the {name} executable to use in the unit tests."
            f"Defaults to {name}.",
        )
    pydocker_group.addoption(
        "--no-runtime-skip",
        action="store_true",
        help="Do not skip tests corresponding to a container runtime that is not available",
    )


def pytest_configure(config: pytest.Config) -> None:
    # Filter warnings
    config.addinivalue_line(
        "filterwarnings", "error::pydantic.PydanticDeprecatedSince20"
    )


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    # Apply marks to testcases.
    for item in items:
        assert isinstance(item, pytest.Function)
        for runtime in ["docker", "podman"]:
            if f"{runtime}_client" in item.fixturenames:
                item.add_marker(runtime)
            if (
                hasattr(item, "callspec")
                and "ctr_client" in item.callspec.params
                and item.callspec.params["ctr_client"] == runtime
            ):
                item.add_marker(runtime)
