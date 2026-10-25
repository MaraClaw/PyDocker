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
            self.stdout: str | None = stdout.decode()
        if stderr is None:
            self.stderr: str | None = None
        else:
            self.stderr: str | None = stderr.decode()
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


class NoSuchPod(DockerException):
    pass


class NoSuchService(DockerException):
    pass


class NotASwarmManager(DockerException):
    pass


class NoSuchVolume(DockerException):
    pass
