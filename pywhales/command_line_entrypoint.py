"""Notice-only console entry point for the installed PyWhales package.

This entry point does not run Docker commands or download the Docker CLI.
"""


def main() -> None:
    """Print the notice directing users to install the Docker CLI themselves."""
    print(
        "The PyWhales command line interface does not perform any action. "
        "If you were using it to download the docker CLI, "
        "you should instead do it manually. "
        "Take a look at https://github.com/MaraClaw/PyWhales/blob/HEAD/docs/template/docker_client.md#the-docker-cli "
        "which has instructions to download the Docker client."
    )
