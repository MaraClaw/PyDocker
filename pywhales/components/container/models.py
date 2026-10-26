from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import pydantic

from pywhales.utils import DockerCamelModel


class ContainerHealthcheckResult(DockerCamelModel):
    start: datetime | None = None
    end: datetime | None = None
    exit_code: int | None = None
    output: str | None = None


class ContainerHealth(DockerCamelModel):
    status: str | None = None
    failing_streak: int | None = None
    log: list[ContainerHealthcheckResult] | None = None


class ContainerState(DockerCamelModel):
    status: str | None = None
    running: bool | None = None
    paused: bool | None = None
    restarting: bool | None = None
    oom_killed: bool | None = None
    dead: bool | None = None
    pid: int | None = None
    exit_code: int | None = None
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    health: ContainerHealth | None = None


class ContainerWeightDevice(DockerCamelModel):
    path: Path | None = None
    weight: int | None = None


class ContainerThrottleDevice(DockerCamelModel):
    path: Path | None = None
    rate: int | None = None


class ContainerDevice(DockerCamelModel):
    path_on_host: Path | None = None
    path_in_container: Path | None = None
    cgroup_permissions: str | None = None


class ContainerDeviceRequest(DockerCamelModel):
    driver: str | None = None
    count: int | None = None
    device_ids: Annotated[list[str] | None, pydantic.Field(alias="DeviceIDs")] = None
    capabilities: list[Any] | None = None
    options: dict[str, str] | None = None


class ContainerUlimit(DockerCamelModel):
    name: str | None = None
    soft: int | None = None
    hard: int | None = None


class ContainerLogConfig(DockerCamelModel):
    type: str | None = None
    config: Any | None = None


class ContainerRestartPolicy(DockerCamelModel):
    name: str | None = None
    maximum_retry_count: int | None = None


class PortBinding(DockerCamelModel):
    host_ip: str | None = None
    host_port: str | None = None


class ContainerMountBindOption(DockerCamelModel):
    propagation: str | None = None
    non_recursive: bool | None = None


class ContainerVolumeDriverConfig(DockerCamelModel):
    name: str | None = None
    options: dict[str, Any] | None = None


class ContainerVolumeOptions(DockerCamelModel):
    no_copy: bool | None = None
    labels: dict[str, str] | None = None


class ContainerTmpfsOptions(DockerCamelModel):
    size_bytes: int | None = None
    mode: int | None = None


class ContainerMount(DockerCamelModel):
    target: Path | None = None
    source: str | None = None
    type: str | None = None
    read_only: bool | None = None
    consistency: str | None = None
    bind_options: ContainerMountBindOption | None = None
    volume_options: ContainerVolumeOptions | None = None
    tmpfs_options: ContainerTmpfsOptions | None = None


class ContainerHostConfig(DockerCamelModel):
    cpu_shares: int | None = None
    memory: int | None = None
    cgroup_parent: Path | None = None
    blkio_weight: int | None = None
    blkio_weight_device: list[ContainerWeightDevice] | None = None
    blkio_device_read_bps: list[ContainerThrottleDevice] | None = None
    blkio_device_write_bps: list[ContainerThrottleDevice] | None = None
    blkio_device_read_iops: list[ContainerThrottleDevice] | None = None
    blkio_device_write_iops: list[ContainerThrottleDevice] | None = None
    cpu_period: int | None = None
    cpu_quota: int | None = None
    cpu_realtime_period: int | None = None
    cpu_realtime_runtime: int | None = None
    cpuset_cpus: str | None = None
    cpuset_mems: str | None = None
    devices: list[ContainerDevice] | None = None
    device_cgroup_rules: list[str] | None = None
    device_requests: list[ContainerDeviceRequest] | None = None
    kernel_memory: int | None = None
    kernel_memory_tcp: int | None = None
    memory_reservation: int | None = None
    memory_swap: int | None = None
    memory_swappiness: int | None = None
    nano_cpus: int | None = None
    oom_kill_disable: bool | None = None
    init: bool | None = None
    pids_limit: int | None = None
    ulimits: list[ContainerUlimit] | None = None
    cpu_count: int | None = None
    cpu_percent: int | None = None
    binds: list[str] | None = None
    container_id_file: Path | None = None
    log_config: ContainerLogConfig | None = None
    network_mode: str | None = None
    port_bindings: dict[str, list[PortBinding] | None] | None = None
    restart_policy: ContainerRestartPolicy | None = None
    auto_remove: bool | None = None
    volume_driver: str | None = None
    volumes_from: list[str] | None = None
    mounts: list[ContainerMount] | None = None
    capabilities: list[str] | None = None
    cap_add: list[str] | None = None
    cap_drop: list[str] | None = None
    cgroupns_mode: str | None = None
    dns: list[str] | None = None
    dns_options: list[str] | None = None
    dns_search: list[str] | None = None
    extra_hosts: list[str] | None = None
    group_add: list[str] | None = None
    ipc_mode: str | None = None
    cgroup: str | None = None
    links: list[str] | None = None
    oom_score_adj: int | None = None
    pid_mode: str | None = None
    privileged: bool | None = None
    publish_all_ports: bool | None = None
    readonly_rootfs: bool | None = None
    security_opt: list[str] | None = None
    storage_opt: Any = None
    tmpfs: dict[Path, str] | None = None
    uts_mode: str | None = None
    userns_mode: str | None = None
    shm_size: int | None = None
    sysctls: dict[str, Any] | None = None
    runtime: str | None = None
    console_size: tuple[int, int] | None = None
    isolation: str | None = None
    masked_paths: list[Path] | None = None
    readonly_paths: list[Path] | None = None


class ContainerHealthCheck(DockerCamelModel):
    test: list[str] | None = None
    interval: int | None = None
    timeout: int | None = None
    retries: int | None = None
    start_period: int | None = None


class ContainerConfig(DockerCamelModel):
    hostname: str | None = None
    domainname: str | None = None
    user: str | None = None
    attach_stdin: bool | None = None
    attach_stdout: bool | None = None
    attach_stderr: bool | None = None
    exposed_ports: dict[str, Any] | None = None
    tty: bool | None = None
    open_stdin: bool | None = None
    stdin_once: bool | None = None
    env: list[str] | None = None
    cmd: list[str] | None = None
    healthcheck: ContainerHealthCheck | None = None
    args_escaped: bool | None = None
    image: str | None = None
    volumes: dict[str, Any] | None = None
    working_dir: Path | None = None
    entrypoint: list[str] | str | None = None
    network_disabled: bool | None = None
    mac_address: str | None = None
    on_build: list[str] | None = None
    labels: dict[str, str] | None = None
    stop_signal: str | int | None = None
    stop_timeout: int | None = None
    shell: list[str] | None = None


class Mount(DockerCamelModel):
    type: str | None = None
    name: str | None = None
    source: str | None = None
    destination: str | None = None
    driver: str | None = None
    mode: str | None = None
    rw: bool | None = None
    propagation: str | None = None


class ContainerEndpointIPAMConfig(DockerCamelModel):
    ipv4_address: str | None = None
    ipv6_address: str | None = None
    link_local_ips: list[str] | None = None


class NetworkInspectResult(DockerCamelModel):
    ipam_config: ContainerEndpointIPAMConfig | None = None
    links: list[str] | None = None
    aliases: list[str] | None = None
    network_id: str | None = None
    endpoint_id: str | None = None
    gateway: str | None = None
    ip_address: str | None = None
    ip_prefix_length: int | None = None
    ipv6_gateway: str | None = None
    global_ipv6_address: str | None = None
    global_ipv6_prefix_length: int | None = None
    mac_address: str | None = None
    driver_options: dict[str, Any] | None = None


class ContainerNetworkAddress(DockerCamelModel):
    addr: str | None = None
    prefix_len: int | None = None


class NetworkSettings(DockerCamelModel):
    bridge: str | None = None
    sandbox_id: str | None = None
    hairpin_mode: bool | None = None
    link_local_ipv6_address: str | None = None
    link_local_ipv6_prefix_length: int | None = None
    ports: dict[str, Any] | None = None  # to rework
    sandbox_key: Path | None = None
    secondary_ip_addresses: list[ContainerNetworkAddress] | None = None
    secondary_ipv6_addresses: list[ContainerNetworkAddress] | None = None
    endpoint_id: str | None = None
    gateway: str | None = None
    global_ipv6_address: str | None = None
    global_ipv6_prefix_length: int | None = None
    ip_address: str | None = None
    ip_prefix_length: int | None = None
    ipv6_gateway: str | None = None
    mac_address: str | None = None
    networks: dict[str, NetworkInspectResult] | None = None


class ContainerGraphDriver(DockerCamelModel):
    name: str | None = None
    data: dict[str, Any] | None = None


class ContainerInspectResult(DockerCamelModel):
    id: str | None = None
    created: datetime | None = None
    path: str | None = None
    args: list[str] | None = None
    state: ContainerState | None = None
    image: str | None = None
    resolv_conf_path: str | None = None
    hostname_path: Path | None = None
    hosts_path: Path | None = None
    log_path: Path | None = None
    node: object = None
    name: str | None = None
    restart_count: int | None = None
    driver: str | None = None
    platform: str | None = None
    mount_label: str | None = None
    process_label: str | None = None
    app_armor_profile: str | None = None
    exec_ids: list[str] | None = None
    host_config: ContainerHostConfig | None = None
    graph_driver: ContainerGraphDriver | None = None
    size_rw: int | None = None
    size_root_fs: int | None = None
    mounts: list[Mount] | None = None
    config: ContainerConfig | None = None
    network_settings: NetworkSettings | None = None
