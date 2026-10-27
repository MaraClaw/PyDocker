"""Unimplemented Docker content-trust command placeholders."""

from typing import NoReturn

from pywhales.client_config import ClientConfig, DockerCLICaller


class TrustCLI(DockerCLICaller):
    """Content-trust interface whose operations currently raise NotImplementedError."""

    def __init__(self, client_config: ClientConfig) -> None:
        super().__init__(client_config)
        self.key = None
        self.signer = None

    def inspect(self) -> NoReturn:
        """Reject trust inspection because this operation is not implemented.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError

    def revoke(self) -> NoReturn:
        """Reject trust revocation because this operation is not implemented.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError

    def sign(self) -> NoReturn:
        """Reject trust signing because this operation is not implemented.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError
