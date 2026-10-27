"""Known Docker wire values and exact constrained command input types."""

from enum import StrEnum
from typing import Literal


class ContainerStatus(StrEnum):
    CREATED = "created"
    RESTARTING = "restarting"
    RUNNING = "running"
    REMOVING = "removing"
    PAUSED = "paused"
    EXITED = "exited"
    DEAD = "dead"


class HealthStatus(StrEnum):
    STARTING = "starting"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    NONE = "none"


class RestartPolicyName(StrEnum):
    NO = "no"
    ALWAYS = "always"
    ON_FAILURE = "on-failure"
    UNLESS_STOPPED = "unless-stopped"


class MountType(StrEnum):
    BIND = "bind"
    VOLUME = "volume"
    TMPFS = "tmpfs"
    NPIPE = "npipe"
    CLUSTER = "cluster"
    IMAGE = "image"


class BindPropagation(StrEnum):
    PRIVATE = "private"
    RPRIVATE = "rprivate"
    SHARED = "shared"
    RSHARED = "rshared"
    SLAVE = "slave"
    RSLAVE = "rslave"


class Isolation(StrEnum):
    DEFAULT = "default"
    PROCESS = "process"
    HYPERV = "hyperv"


class TaskState(StrEnum):
    NEW = "new"
    ALLOCATED = "allocated"
    PENDING = "pending"
    ASSIGNED = "assigned"
    ACCEPTED = "accepted"
    PREPARING = "preparing"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    COMPLETE = "complete"
    SHUTDOWN = "shutdown"
    FAILED = "failed"
    REJECTED = "rejected"
    REMOVE = "remove"
    ORPHANED = "orphaned"


class RestartCondition(StrEnum):
    NONE = "none"
    ON_FAILURE = "on-failure"
    ANY = "any"


class NodeRole(StrEnum):
    WORKER = "worker"
    MANAGER = "manager"


class NodeAvailability(StrEnum):
    ACTIVE = "active"
    PAUSE = "pause"
    DRAIN = "drain"


class NodeState(StrEnum):
    UNKNOWN = "unknown"
    DOWN = "down"
    READY = "ready"
    DISCONNECTED = "disconnected"


class NodeReachability(StrEnum):
    UNKNOWN = "unknown"
    UNREACHABLE = "unreachable"
    REACHABLE = "reachable"


class EndpointMode(StrEnum):
    VIP = "vip"
    DNSRR = "dnsrr"


class PublishMode(StrEnum):
    INGRESS = "ingress"
    HOST = "host"


class NetworkProtocol(StrEnum):
    TCP = "tcp"
    UDP = "udp"
    SCTP = "sctp"


class UpdateOrder(StrEnum):
    STOP_FIRST = "stop-first"
    START_FIRST = "start-first"


class FailureAction(StrEnum):
    PAUSE = "pause"
    CONTINUE = "continue"
    ROLLBACK = "rollback"


class ServiceUpdateState(StrEnum):
    UPDATING = "updating"
    PAUSED = "paused"
    COMPLETED = "completed"
    ROLLBACK_STARTED = "rollback_started"
    ROLLBACK_PAUSED = "rollback_paused"
    ROLLBACK_COMPLETED = "rollback_completed"


class ResourceScope(StrEnum):
    SWARM = "swarm"
    GLOBAL = "global"
    LOCAL = "local"


class SwarmState(StrEnum):
    INACTIVE = "inactive"
    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"
    LOCKED = "locked"


class ComposeDependencyCondition(StrEnum):
    SERVICE_STARTED = "service_started"
    SERVICE_HEALTHY = "service_healthy"
    SERVICE_COMPLETED_SUCCESSFULLY = "service_completed_successfully"


class ComposeProgress(StrEnum):
    AUTO = "auto"
    TTY = "tty"
    PLAIN = "plain"
    JSON = "json"
    QUIET = "quiet"


class ComposePullPolicy(StrEnum):
    ALWAYS = "always"
    MISSING = "missing"
    NEVER = "never"
    BUILD = "build"


class BuildxProgressMode(StrEnum):
    AUTO = "auto"
    NONE = "none"
    PLAIN = "plain"
    QUIET = "quiet"
    RAWJSON = "rawjson"
    TTY = "tty"


class BuildxCallMethod(StrEnum):
    BUILD = "build"
    CHECK = "check"
    OUTLINE = "outline"
    TARGETS = "targets"


class ClientType(StrEnum):
    DOCKER = "docker"
    NERDCTL = "nerdctl"
    UNKNOWN = "unknown"


class Orchestrator(StrEnum):
    SWARM = "swarm"
    KUBERNETES = "kubernetes"
    ALL = "all"


class ImageResolutionMode(StrEnum):
    ALWAYS = "always"
    CHANGED = "changed"
    NEVER = "never"


type ContainerStatusInput = (
    ContainerStatus
    | Literal[
        "created", "restarting", "running", "removing", "paused", "exited", "dead"
    ]
)
type HealthStatusInput = (
    HealthStatus | Literal["starting", "healthy", "unhealthy", "none"]
)
type IsolationInput = Isolation | Literal["default", "process", "hyperv"]
type ResourceScopeInput = ResourceScope | Literal["swarm", "global", "local"]
type RestartConditionInput = RestartCondition | Literal["none", "on-failure", "any"]
type ClientTypeInput = ClientType | Literal["docker", "nerdctl", "unknown"]
type ComposeProgressInput = (
    ComposeProgress | Literal["auto", "tty", "plain", "json", "quiet"]
)
type ComposeCreatePullPolicy = (
    ComposePullPolicy | Literal["always", "missing", "never", "build"]
)
type ComposeUpPullPolicy = Literal[
    "always",
    "missing",
    "never",
    ComposePullPolicy.ALWAYS,
    ComposePullPolicy.MISSING,
    ComposePullPolicy.NEVER,
]
type BuildxProgress = (
    BuildxProgressMode
    | Literal["auto", "none", "plain", "quiet", "rawjson", "tty", False]
)
type BuildxCall = BuildxCallMethod | Literal["build", "check", "outline", "targets"]
type BuildxBuildCall = Literal["build", BuildxCallMethod.BUILD]
type BuildxReportCall = Literal[
    "check",
    "outline",
    "targets",
    BuildxCallMethod.CHECK,
    BuildxCallMethod.OUTLINE,
    BuildxCallMethod.TARGETS,
]
