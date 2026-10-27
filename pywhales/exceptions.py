"""CLI failure types, confidential captured details, and stderr classification.

Exception messages withhold command and captured payload values by default.
Raw attributes remain available and confidential regardless of message policy.
"""

from typing import override


class DockerException(Exception):
    """CLI failure retaining command details independently of diagnostic text.

    Safe messages expose the exit code but not argv or captured payloads. Raw
    diagnostic opt-in includes those values in exception text; it does not
    change the attributes or stderr-based exception classification.

    Attributes:
        docker_command: Launched arguments, retained without redaction.
        return_code: Local CLI exit code.
        stdout: Captured stdout decoded with replacement, or ``None`` when no
            captured value was supplied. May contain confidential data.
        stderr: Captured stderr decoded with replacement, or ``None`` when no
            captured value was supplied. May contain confidential data.
    """

    def __init__(
        self,
        command_launched: list[str],
        return_code: int,
        stdout: bytes | None = None,
        stderr: bytes | None = None,
        *,
        raw_diagnostics: bool = False,
    ) -> None:
        """Retain CLI details and construct safe or explicitly raw error text.

        Args:
            command_launched: Launched arguments retained as docker_command.
            return_code: Exit code of the local CLI process.
            stdout: Captured stdout bytes; ``None`` means no captured value.
            stderr: Captured stderr bytes; ``None`` means no captured value.
            raw_diagnostics: Include command and captured-output values in the
                message when true. Defaults to a safe summary without redacting
                the raw attributes.
        """
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
        if not raw_diagnostics:
            super().__init__(
                f"The command returned with code {return_code}. "
                "Command and captured output details are withheld."
            )
            return
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
    """Local CLI timeout after launch-based expiry and process cleanup.

    Killing and reaping the local CLI does not guarantee cancellation of
    daemon-side work. Captured details follow DockerException's safe-message
    policy while remaining available through confidential raw attributes.
    """

    @override
    def __str__(self) -> str:
        """Return the timeout notice followed by policy-selected failure text."""
        return f"The local CLI exceeded its deadline.\n{super().__str__()}"


def get_docker_exception_type(output: bytes | None) -> type[DockerException]:
    """Select an exception class from case-insensitive stderr message patterns.

    Args:
        output: Captured stderr bytes, or ``None`` when stderr was not captured.

    Returns:
        The first matching specialized failure class, or DockerException for
        absent, empty, or unrecognized output. No exception is instantiated.
    """
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
