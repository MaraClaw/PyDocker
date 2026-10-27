from typing import override


class DockerException(Exception):
    def __init__(
        self,
        command_launched: list[str],
        return_code: int,
        stdout: bytes | None = None,
        stderr: bytes | None = None,
    ) -> None:
        self.docker_command: list[str] = command_launched
        self.return_code: int = return_code
        if stdout is None:
            self.stdout: str | None = None
        else:
            self.stdout: str | None = stdout.decode(errors="replace")
        if stderr is None:
            self.stderr: str | None = None
        else:
            self.stderr: str | None = stderr.decode(errors="replace")
        command_launched_str = " ".join(command_launched)
        error_msg = (
            f"The command executed was `{command_launched_str}`.\n"
            f"It returned with code {return_code}\n"
        )
        if stdout is not None:
            error_msg += f"The content of stdout is '{self.stdout}'\n"
        else:
            error_msg += (
                "The content of stdout can be found above the "
                "stacktrace (it wasn't captured).\n"
            )
        if stderr is not None:
            error_msg += f"The content of stderr is '{self.stderr}'\n"
        else:
            error_msg += (
                "The content of stderr can be found above the "
                "stacktrace (it wasn't captured)."
            )
        super().__init__(error_msg)


class NoSuchContainer(DockerException):
    pass


class NoSuchImage(DockerException):
    pass


class NoSuchNetwork(DockerException):
    pass


class NoSuchService(DockerException):
    pass


class NotASwarmManager(DockerException):
    pass


class NoSuchVolume(DockerException):
    pass


class DockerTimeout(DockerException):
    """The local CLI exceeded its launch-based deadline and was reaped."""

    @override
    def __str__(self) -> str:
        return f"The local CLI exceeded its deadline.\n{super().__str__()}"


def get_docker_exception_type(output: bytes | None) -> type[DockerException]:
    if output is None or len(output) == 0:
        return DockerException
    decoded = output.decode(errors="replace").lower()
    if "no such image" in decoded:
        return NoSuchImage
    if "no such service" in decoded or (
        "service" in decoded and "not found" in decoded
    ):
        return NoSuchService
    if "no such container" in decoded:
        return NoSuchContainer
    if "this node is not a swarm manager" in decoded:
        return NotASwarmManager
    if "no such volume" in decoded:
        return NoSuchVolume
    if "network" in decoded and "not found" in decoded:
        return NoSuchNetwork
    return DockerException
