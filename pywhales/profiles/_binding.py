"""Explicit caller-approved config identity, independent of reporting aliases."""

from copy import deepcopy
from dataclasses import dataclass, field, replace

from pywhales.admission import EndpointIdentity
from pywhales.client_config import ClientConfig
from pywhales.docker_client import DockerClient

from ._contracts import PolicyError, PolicyReason


@dataclass(frozen=True, slots=True, eq=False)
class EndpointBinding:
    """Operator approval of one exact config object and its current settings.

    Construct only after approving endpoint selection, ambient Docker environment,
    context files, executable and credentials externally. The snapshot detects
    Python config edits, not changes to files, environment or remote endpoints.
    This is an observational check, not a concurrency or daemon identity fence.
    """

    endpoint: EndpointIdentity
    config: ClientConfig = field(repr=False)
    _snapshot: ClientConfig = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_snapshot",
            deepcopy(
                self.config,
                {id(self.config.telemetry_observer): self.config.telemetry_observer},
            ),
        )

    def validate(self, client: DockerClient) -> None:
        """Reject another config or changed settings before any SDK preflight."""
        if (
            client.client_config is not self.config
            or client.container.client_config is not self.config
        ):
            raise PolicyError("endpoint.binding", PolicyReason.NOT_APPROVED)
        if self.config != self._snapshot:
            raise PolicyError("endpoint.binding", PolicyReason.CONFLICT)

    def _prepare(self, client: DockerClient) -> None:
        """Resolve once after full validation and retain only that cache transition."""
        self.validate(client)
        if self._snapshot._client_call_with_path is None:
            prefix = self.config.get_client_call_with_path()
            object.__setattr__(
                self,
                "_snapshot",
                replace(self._snapshot, _client_call_with_path=deepcopy(prefix)),
            )
