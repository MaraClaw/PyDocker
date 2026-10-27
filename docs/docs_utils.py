"""Documentation formatting and opt-in live Docker example providers.

Formatting and default example insertion need no Docker runtime. Calling live
providers requires the CLI and daemon; builders also require Buildx, and Swarm
examples need manager access or permission to initialize an inactive daemon.
Providers can create and remove resources or install a plugin. Use a disposable
daemon; cleanup preserves borrowed state rather than restoring arbitrary changes.
"""

import tempfile
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Protocol, override
from uuid import uuid4

from pywhales import Builder, Container, Image, Volume, docker
from pywhales.exceptions import NoSuchImage
from pywhales.utils import run


class DisplayValue(Protocol):
    """String-renderable value for a notebook-style documentation example."""

    @override
    def __str__(self) -> str: ...


def _example_names(command: Sequence[str]) -> list[str]:
    """Run a Docker inventory command and return its output lines."""
    return run([*docker.docker_cmd, *command]).splitlines()


def _example_inventory[Resource](
    command: Sequence[str], reference: Callable[[str], Resource]
) -> Mapping[str, Resource]:
    """Map tab-separated inventory names to resource handles.

    Args:
        command: Docker subcommand producing one name and identifier per line.
        reference: Construct a handle from each identifier without acquiring it.

    Returns:
        Handles keyed by the names reported by the CLI.
    """
    return {
        name: reference(identifier)
        for name, identifier in (
            line.split("\t", 1) for line in _example_names(command)
        )
    }


@contextmanager
def _created_example[Resource](
    create: Callable[[str], Resource],
    inventory: Callable[[], Mapping[str, Resource]],
    release: Callable[[Resource], None],
) -> Generator[Resource]:
    """Borrow or acquire a uniquely named resource on context entry.

    Borrowed resources are left untouched. On exit, release the acquired handle;
    if creation raised before returning it, recheck the generated name and release
    a matching partial acquisition. Inventory and cleanup failures propagate.

    Args:
        create: Acquire a resource using the generated name.
        inventory: Return current names and handles before acquisition and,
            when needed, after a failed acquisition.
        release: Remove a resource owned by this context.

    Yields:
        The borrowed or newly acquired resource, valid within the context.
    """
    name = f"pywhales-docs-{uuid4().hex}"
    borrowed = inventory().get(name)
    if borrowed is not None:
        yield borrowed
        return
    acquired: list[Resource] = []
    try:
        resource = create(name)
        acquired.append(resource)
        yield resource
    finally:
        if acquired:
            release(acquired[0])
        else:
            candidate = inventory().get(name)
            if candidate is not None:
                release(candidate)


@contextmanager
def _example_image(name: str) -> Generator[Image]:
    """Borrow an image or pull it on context entry and clean up its tag.

    Existing images are not removed. After an attempted pull, remove the image
    forcibly without pruning if inventory contains its ``name:latest`` tag,
    including when metadata loading failed after the pull.

    Args:
        name: Untagged image repository used by the example.

    Yields:
        The inspected or pulled image handle for use inside the context.
    """
    try:
        image = docker.image.inspect(name)
    except NoSuchImage:
        try:
            yield docker.image.pull(name)
        finally:
            if f"{name}:latest" in _example_names(
                ["image", "ls", "--format", "{{.Repository}}:{{.Tag}}"]
            ):
                docker.image.remove(name, force=True, prune=False)
    else:
        yield image


@contextmanager
def _example_swarm() -> Generator[None]:
    """Initialize an inactive Swarm on entry and leave only that Swarm on exit.

    Existing membership and non-inactive states are preserved. A failed init
    registers no leave operation; successful initialization registers force-leave
    cleanup even if the example fails. Requires a disposable Docker daemon.

    Yields:
        Control while Swarm-dependent example operations run.
    """
    with ExitStack() as cleanup:
        swarm = docker.system.info().swarm
        if swarm is not None and swarm.local_node_state == "inactive":
            docker.swarm.init()
            cleanup.callback(docker.swarm.leave, force=True)
        yield


def write_code(index: int, attribute_access: str, value: DisplayValue) -> str:
    """Format a notebook-style expression with its runtime type and value.

    Args:
        index: Displayed input-cell number.
        attribute_access: Expression text displayed without evaluating it here.
        value: Already evaluated value rendered through its string conversion.

    Returns:
        A cell header and type/value output ending in a newline.
    """
    string = f"""In [{index}]: super_print({attribute_access})
type = {type(value)}, value = {value}
"""
    return string


def generate_code_demo_networks() -> str:
    """Render live network properties using a temporary attached container.

    Requires Docker. Release the acquired container before its network and any
    image pulled for the example; an existing Ubuntu image is preserved.

    Returns:
        Notebook-style property output captured before cleanup.
    """
    result = []

    with (
        _example_image("ubuntu"),
        docker.network.create(f"pywhales-docs-{uuid4().hex}") as network,
        _created_example(
            lambda name: docker.run(
                "ubuntu",
                ["sleep", "infinity"],
                name=name,
                detach=True,
                networks=[network],
                pull="never",
            ),
            lambda: _example_inventory(
                [
                    "container",
                    "list",
                    "--all",
                    "--no-trunc",
                    "--format",
                    "{{.Names}}\t{{.ID}}",
                ],
                lambda identifier: Container(
                    docker.client_config, identifier, is_immutable_id=True
                ),
            ),
            lambda container: container.remove(force=True),
        ),
    ):
        to_evaluate = [
            "network.name",
            "network.id",
            "network.created",
            "network.scope",
            "network.driver",
            "network.enable_ipv6",
            "network.ipam.driver",
            "network.ipam.config",
            "network.ipam.options",
            "network.internal",
            "network.attachable",
            "network.ingress",
            "network.containers",
            "network.options",
            "network.labels",
            "network.config_from",
            "network.config_only",
        ]

        for i, attribute_access in enumerate(to_evaluate):
            value = eval(attribute_access)
            result.append(write_code(i + 5, attribute_access, value))

    return "\n".join(result)


@_example_swarm()
def generate_code_demo_nodes() -> str:
    """Render properties of the first live Swarm node.

    Requires manager access. Initialize and later force-leave Swarm only when
    the daemon starts inactive; preserve existing membership. Print the node ID
    as progress output without removing the node.

    Returns:
        Notebook-style node property output.
    """
    result = []

    node = docker.node.list()[0]
    print(node.id)

    to_evaluate = [
        "node.id",
        "node.version.index",
        "node.created_at",
        "node.updated_at",
        "node.spec.name",
        "node.spec.labels",
        "node.spec.role",
        "node.spec.availability",
        "node.description.hostname",
        "node.description.platform.architecture",
        "node.description.platform.os",
        "node.description.resources.nano_cpus",
        "node.description.resources.memory_bytes",
        "node.description.resources.generic_resources",
        "node.description.engine.engine_version",
        "node.description.engine.labels",
        "node.description.engine.plugins",
        "node.description.tls_info.trust_root",
        "node.description.tls_info.cert_issuer_subject",
        "node.description.tls_info.cert_issuer_public_key",
        "node.status.state",
        "node.status.message",
        "node.status.addr",
        "node.manager_status.leader",
        "node.manager_status.reachability",
        "node.manager_status.addr",
    ]

    for i, attribute_access in enumerate(to_evaluate):
        value = eval(attribute_access)
        result.append(write_code(i + 4, attribute_access, value))

    return "\n".join(result)


@_example_swarm()
def generate_code_demo_services() -> str:
    """Render a temporary live Swarm service and release acquired resources.

    Requires Docker and manager access or an inactive disposable daemon. Remove
    the service before a newly pulled BusyBox image, then leave any Swarm created
    by the example. Existing images and Swarm membership are preserved.

    Returns:
        Notebook-style service property output; the service ID is also printed.
    """
    result = []

    with (
        _example_image("busybox"),
        docker.service.create("busybox", ["ping", "www.google.com"]) as service,
    ):
        print(service.id)

        to_evaluate = [
            "service.id",
            "service.version",
            "service.created_at",
            "service.updated_at",
            "service.spec.name",
            "service.spec.labels",
            "service.spec.mode",
            "service.spec.update_config",
            "service.spec.rollback_config",
            "service.spec.task_template.container_spec.image",
            "service.spec.task_template.container_spec.labels",
            "service.spec.task_template.container_spec.privileges",
            "service.spec.task_template.container_spec.stop_grace_period",
            "service.spec.task_template.container_spec.isolation",
            "service.spec.task_template.container_spec.env",
            "service.spec.task_template.resources.limits",
            "service.spec.task_template.resources.reservations",
            "service.previous_spec",
            "service.endpoint.spec",
            "service.endpoint.ports",
            "service.endpoint.virtual_ips",
            "service.update_status",
        ]

        for i, attribute_access in enumerate(to_evaluate):
            value = eval(attribute_access)
            result.append(write_code(i + 4, attribute_access, value))

    return "\n".join(result)


@_example_swarm()
def generate_code_demo_configs() -> str:
    """Render a uniquely named live Swarm config from temporary file content.

    Requires Docker and manager access or an inactive disposable daemon. Register
    config removal after creation returns, and leave any Swarm initialized by
    this call when rendering exits. The config ID is also printed.

    Returns:
        Notebook-style config property output captured before cleanup.
    """
    result = []

    with tempfile.TemporaryDirectory() as tmp_dir, ExitStack() as cleanup:
        config_file = Path(tmp_dir) / "file.conf"
        config_file.write_text("Hello world!")
        config = docker.config.create(
            f"pywhales-docs-{uuid4().hex}", config_file, labels={"hello": "world"}
        )
        cleanup.callback(config.remove)
        print(config.id)

        to_evaluate = [
            "config.id",
            "config.version.index",
            "config.created_at",
            "config.updated_at",
            "config.spec.name",
            "config.spec.labels",
            "config.spec.data",
            "config.spec.templating",
        ]
        for i, attribute_access in enumerate(to_evaluate):
            value = eval(attribute_access)
            result.append(write_code(i + 4, attribute_access, value))

    return "\n".join(result)


def generate_code_demo_volumes() -> str:
    """Render live volume properties and release the example's acquisition.

    Requires Docker. Preserve a borrowed generated-name match, and recover a
    partial acquisition by its name if creation fails before returning a handle.

    Returns:
        Notebook-style volume property output captured before cleanup.
    """
    result = []

    with _created_example(
        lambda name: docker.volume.create(name),
        lambda: _example_inventory(
            ["volume", "ls", "--format", "{{.Name}}\t{{.Name}}"],
            lambda identifier: Volume(
                docker.client_config, identifier, is_immutable_id=True
            ),
        ),
        lambda volume: volume.remove(),
    ) as volume:
        to_evaluate = [
            "volume.name",
            "volume.driver",
            "volume.mountpoint",
            "volume.created_at",
            "volume.status",
            "volume.labels",
            "volume.scope",
            "volume.options",
        ]

        for i, attribute_access in enumerate(to_evaluate):
            value = eval(attribute_access, {}, {"volume": volume})
            result.append(write_code(i + 4, attribute_access, value))

    return "\n".join(result)


def generate_code_demo_builders() -> str:
    """Render live builder properties and release the example's acquisition.

    Requires Docker and Buildx. Preserve a borrowed generated-name match, and
    recover partial acquisition by name if creation fails to return a handle.

    Returns:
        Notebook-style builder property output captured before cleanup.
    """
    result = []

    with _created_example(
        lambda name: docker.buildx.create(name=name),
        lambda: _example_inventory(
            ["buildx", "ls", "--format", "{{.Name}}\t{{.Name}}"],
            lambda identifier: Builder(
                docker.client_config, identifier, is_immutable_id=True
            ),
        ),
        lambda builder: builder.remove(),
    ) as builder:
        to_evaluate = [
            "builder.name",
            "builder.driver",
            "builder.last_activity",
            "builder.dynamic",
            "builder.nodes[0].name",
            "builder.nodes[0].endpoint",
            "builder.nodes[0].flags",
            "builder.nodes[0].status",
            "builder.nodes[0].version",
            "builder.nodes[0].ids",
            "builder.nodes[0].platforms",
            "builder.nodes[0].labels",
        ]

        for i, attribute_access in enumerate(to_evaluate):
            value = eval(attribute_access, {}, {"builder": builder})
            result.append(write_code(i + 4, attribute_access, value))

    return "\n".join(result)


def generate_code_demo_images() -> str:
    """Render live Ubuntu image properties without removing a borrowed image.

    Requires Docker. Pull a missing image and remove its acquired latest tag
    after rendering, including failure cleanup. Image tags are also printed.

    Returns:
        Notebook-style image property output captured before cleanup.
    """
    result = []

    with _example_image("ubuntu") as image:
        print(f"Generating example for {image.repo_tags}")
        to_evaluate = [
            "image.id",
            "image.repo_tags",
            "image.repo_digests",
            "image.parent",
            "image.comment",
            "image.created",
            "image.container",
            "image.container_config",
            "image.docker_version",
            "image.author",
            "image.config",
            "image.architecture",
            "image.os",
            "image.os_version",
            "image.size",
            "image.virtual_size",
            "image.graph_driver.name",
            "image.graph_driver.data",
            "image.root_fs.type",
            "image.root_fs.layers",
            "image.root_fs.base_layer",
            "image.metadata",
        ]

        for i, attribute_access in enumerate(to_evaluate):
            value = eval(attribute_access)
            result.append(write_code(i + 4, attribute_access, value))

    return "\n".join(result)


def generate_code_demo_plugins() -> str:
    """Render a live volume plugin, installing it only when not already present.

    Requires Docker plugin support and installation permissions. Preserve an
    existing named plugin; otherwise force-remove an acquired plugin, including
    one discovered by inventory after installation failed to return a handle.

    Returns:
        Notebook-style plugin property output captured before cleanup.
    """
    result = []

    name = "mochoa/s3fs-volume-plugin"
    borrowed = next(
        (
            plugin
            for plugin in docker.plugin.list()
            if plugin.name in (name, f"{name}:latest")
        ),
        None,
    )
    acquired = []
    try:
        if borrowed is None:
            plugin = docker.plugin.install(name)
            acquired.append(plugin)
        else:
            plugin = borrowed
        print(f"Generating example for {plugin.name}")
        to_evaluate = [
            "plugin.id",
            "plugin.name",
            "plugin.enabled",
            "plugin.settings",
            "plugin.plugin_reference",
            "plugin.config",
        ]

        for i, attribute_access in enumerate(to_evaluate):
            value = eval(attribute_access)
            result.append(write_code(i + 4, attribute_access, value))
    finally:
        if borrowed is None and (
            acquired
            or any(
                installed_name in (name, f"{name}:latest")
                for installed_name in _example_names(
                    ["plugin", "list", "--format", "{{.Name}}"]
                )
            )
        ):
            docker.plugin.remove(name, force=True)

    return "\n".join(result)


def generate_code_demo_containers() -> str:
    """Render a live sleeping Ubuntu container and release acquired resources.

    Requires Docker. Remove the acquired container forcibly before removing any
    image pulled by this call; preserve borrowed containers and images.

    Returns:
        Notebook-style container property output captured before cleanup.
    """
    result = []

    with (
        _example_image("ubuntu"),
        _created_example(
            lambda name: docker.run(
                "ubuntu", ["sleep", "infinity"], name=name, detach=True, pull="never"
            ),
            lambda: _example_inventory(
                [
                    "container",
                    "list",
                    "--all",
                    "--no-trunc",
                    "--format",
                    "{{.Names}}\t{{.ID}}",
                ],
                lambda identifier: Container(
                    docker.client_config, identifier, is_immutable_id=True
                ),
            ),
            lambda container: container.remove(force=True),
        ) as container,
    ):
        to_evaluate = [
            "container.id",
            "container.created",
            "container.path",
            "container.args",
            "container.state.status",
            "container.state.running",
            "container.state.paused",
            "container.state.restarting",
            "container.state.oom_killed",
            "container.state.dead",
            "container.state.pid",
            "container.state.exit_code",
            "container.state.error",
            "container.state.started_at",
            "container.state.finished_at",
            "container.state.health",
            "container.image",
            "container.resolv_conf_path",
            "container.hostname_path",
            "container.hosts_path",
            "container.log_path",
            "container.node",
            "container.name",
            "container.restart_count",
            "container.driver",
            "container.platform",
            "container.mount_label",
            "container.process_label",
            "container.app_armor_profile",
            "container.exec_ids",
            "container.host_config.cpu_shares",
            "container.host_config.memory",
            "container.host_config.cgroup_parent",
            "container.host_config.blkio_weight",
            "container.host_config.blkio_weight_device",
            "container.host_config.blkio_device_read_bps",
            "container.host_config.blkio_device_write_bps",
            "container.host_config.blkio_device_read_iops",
            "container.host_config.blkio_device_write_iops",
            "container.host_config.cpu_period",
            "container.host_config.cpu_quota",
            "container.host_config.cpu_realtime_period",
            "container.host_config.cpu_realtime_runtime",
            "container.host_config.cpuset_cpus",
            "container.host_config.cpuset_mems",
            "container.host_config.devices",
            "container.host_config.device_cgroup_rules",
            "container.host_config.device_requests",
            "container.host_config.kernel_memory",
            "container.host_config.kernel_memory_tcp",
            "container.host_config.memory_reservation",
            "container.host_config.memory_swap",
            "container.host_config.memory_swappiness",
            "container.host_config.nano_cpus",
            "container.host_config.oom_kill_disable",
            "container.host_config.init",
            "container.host_config.pids_limit",
            "container.host_config.ulimits",
            "container.host_config.cpu_count",
            "container.host_config.cpu_percent",
            "container.host_config.binds",
            "container.host_config.container_id_file",
            "container.host_config.log_config.type",
            "container.host_config.log_config.config",
            "container.host_config.network_mode",
            "container.host_config.port_bindings",
            "container.host_config.restart_policy.name",
            "container.host_config.restart_policy.maximum_retry_count",
            "container.host_config.auto_remove",
            "container.host_config.volume_driver",
            "container.host_config.volumes_from",
            "container.host_config.mounts",
            "container.host_config.capabilities",
            "container.host_config.cap_add",
            "container.host_config.cap_drop",
            "container.host_config.dns",
            "container.host_config.dns_options",
            "container.host_config.dns_search",
            "container.host_config.extra_hosts",
            "container.host_config.group_add",
            "container.host_config.ipc_mode",
            "container.host_config.cgroup",
            "container.host_config.links",
            "container.host_config.oom_score_adj",
            "container.host_config.pid_mode",
            "container.host_config.privileged",
            "container.host_config.publish_all_ports",
            "container.host_config.readonly_rootfs",
            "container.host_config.security_opt",
            "container.host_config.storage_opt",
            "container.host_config.tmpfs",
            "container.host_config.uts_mode",
            "container.host_config.userns_mode",
            "container.host_config.shm_size",
            "container.host_config.sysctls",
            "container.host_config.runtime",
            "container.host_config.console_size",
            "container.host_config.isolation",
            "container.host_config.masked_paths",
            "container.host_config.readonly_paths",
            "container.graph_driver.name",
            "container.graph_driver.data",
            "container.size_rw",
            "container.size_root_fs",
            "container.mounts",
            "container.config.hostname",
            "container.config.domainname",
            "container.config.user",
            "container.config.attach_stdin",
            "container.config.attach_stdout",
            "container.config.attach_stderr",
            "container.config.exposed_ports",
            "container.config.tty",
            "container.config.open_stdin",
            "container.config.stdin_once",
            "container.config.env",
            "container.config.cmd",
            "container.config.healthcheck",
            "container.config.args_escaped",
            "container.config.image",
            "container.config.volumes",
            "container.config.working_dir",
            "container.config.entrypoint",
            "container.config.network_disabled",
            "container.config.mac_address",
            "container.config.on_build",
            "container.config.labels",
            "container.config.stop_signal",
            "container.config.stop_timeout",
            "container.config.shell",
            "container.network_settings.bridge",
            "container.network_settings.sandbox_id",
            "container.network_settings.hairpin_mode",
            "container.network_settings.link_local_ipv6_address",
            "container.network_settings.link_local_ipv6_prefix_length",
            "container.network_settings.ports",
            "container.network_settings.sandbox_key",
            "container.network_settings.secondary_ip_addresses",
            "container.network_settings.secondary_ipv6_addresses",
            "container.network_settings.endpoint_id",
            "container.network_settings.gateway",
            "container.network_settings.global_ipv6_address",
            "container.network_settings.global_ipv6_prefix_length",
            "container.network_settings.ip_address",
            "container.network_settings.ip_prefix_length",
            "container.network_settings.ipv6_gateway",
            "container.network_settings.mac_address",
            "container.network_settings.networks",
        ]

        for i, attribute_access in enumerate(to_evaluate):
            value = eval(attribute_access, {}, {"container": container})
            result.append(write_code(i + 4, attribute_access, value))

    return "\n".join(result)


def add_links(text: str) -> str:
    """Link the recognized Container reference in Markdown text.

    Args:
        text: Markdown containing zero or more literal Container references.

    Returns:
        Text with those references linked to the container documentation.
    """
    text = text.replace(
        "`pywhales.Container`",
        "[`pywhales.Container`](/objects/containers/)",
    )

    return text


def add_code_example(destination: Path, markdown_file: str, code: str) -> None:
    """Replace example markers in one existing object page and print progress.

    Args:
        destination: Generated documentation root containing an objects folder.
        markdown_file: Object-page filename relative to that folder.
        code: Replacement text for every example marker in the page.
    """
    path_file = destination / "objects" / markdown_file
    path_file.write_text(
        path_file.read_text(encoding="utf-8").replace("@INSERT_GENERATED_CODE@", code),
        encoding="utf-8",
    )
    print("added code example for", markdown_file)


def add_all_code_examples(destination: Path, live_examples: bool = False) -> None:
    """Fill object-page examples with offline placeholders or live output.

    Pages are rewritten in provider order without rollback. Live mode invokes
    Docker-backed providers and can create resources, install a plugin, or
    initialize Swarm; use a disposable daemon with the required plugins.

    Args:
        destination: Existing generated documentation root.
        live_examples: Invoke providers when true; the default inserts explanatory
            placeholders without calling providers or Docker.
    """
    examples = {
        "plugins.md": generate_code_demo_plugins,
        "configs.md": generate_code_demo_configs,
        "networks.md": generate_code_demo_networks,
        "volumes.md": generate_code_demo_volumes,
        "images.md": generate_code_demo_images,
        "containers.md": generate_code_demo_containers,
        "builders.md": generate_code_demo_builders,
        "nodes.md": generate_code_demo_nodes,
        "services.md": generate_code_demo_services,
    }
    for markdown_file, provider in examples.items():
        code = (
            provider()
            if live_examples
            else (
                "# Live Docker output omitted. Run `uv run --locked python -m docs.autogen "
                "--live-examples` with Docker available to generate it.\n"
            )
        )
        add_code_example(destination, markdown_file, code)
