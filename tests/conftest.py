import logging
import subprocess
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import assert_never

import pytest

from pydocker import DockerClient

logger = logging.getLogger(__name__)

#
# -----------------------------------------------------------------------------
# Fixtures


@pytest.fixture(scope="session")
def docker_client(pytestconfig: pytest.Config) -> DockerClient:
    ctr_exe = pytestconfig.getoption("--docker-exe")
    assert isinstance(ctr_exe, str)
    client = DockerClient(client_call=[ctr_exe])
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
            "Unable to get Docker version with command '%s version': %s",
            ctr_exe,
            reason,
        )
        if pytestconfig.getoption("--no-runtime-skip"):
            pytest.fail("docker unavailable")
        else:
            pytest.skip("docker unavailable")

    return client


@pytest.fixture(scope="function")
def ctr_client(docker_client: DockerClient) -> DockerClient:
    return docker_client


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
    pydocker_group.addoption(
        "--docker-exe",
        metavar="EXE",
        default="docker",
        help="Path to the docker executable to use in the unit tests."
        "Defaults to docker.",
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
        if "docker_client" in item.fixturenames:
            item.add_marker("docker")
