import pytest

from pywhales import DockerClient
from pywhales.admission import EndpointIdentity
from pywhales.profiles import (
    CONTROLS,
    CreateRequest,
    EndpointBinding,
    LogMode,
    LogPolicy,
    Mount,
    ResourceLimits,
    RuntimePolicy,
    SecurityProfile,
)


@pytest.fixture
def client() -> DockerClient:
    value = DockerClient()
    value.client_config._client_call_with_path = ["offline-docker"]
    return value


@pytest.fixture
def create_request() -> CreateRequest:
    return CreateRequest(
        endpoint=EndpointIdentity("test"),
        image="registry.example/team/app@sha256:" + "a" * 64,
        command=("/app",),
        user="1000:1000",
        capabilities=("NET_BIND_SERVICE",),
        privileged=False,
        read_only=True,
        security_options=("no-new-privileges=true",),
        mounts=(
            Mount(
                kind="volume",
                source="secret-reference",
                target="/run/secrets/token",
                read_only=True,
            ),
        ),
        networks=("isolated",),
        resources=ResourceLimits(cpus=0.5, memory_bytes=100_000_000, pids=32),
        logging=LogPolicy(
            driver="json-file",
            max_size_bytes=1_000_000,
            max_files=2,
            mode=LogMode.BLOCKING,
            loss_approved=False,
            buffer_bytes=None,
        ),
        runtime="runc",
        rootless=False,
        pull="never",
    )


@pytest.fixture
def profile(client: DockerClient, create_request: CreateRequest) -> SecurityProfile:
    return SecurityProfile(
        name="batch",
        version="v1",
        bindings=(EndpointBinding(create_request.endpoint, client.client_config),),
        registries=frozenset({"registry.example"}),
        require_digest=True,
        users=frozenset({create_request.user}),
        capabilities=frozenset(create_request.capabilities),
        allow_privileged=False,
        require_read_only=True,
        security_options=create_request.security_options,
        mounts=frozenset(create_request.mounts),
        networks=frozenset(create_request.networks),
        ceilings=ResourceLimits(cpus=1.0, memory_bytes=200_000_000, pids=64),
        logging=create_request.logging,
        runtimes=(
            RuntimePolicy(name="runc", rootless=False, supported_controls=CONTROLS),
        ),
    )
