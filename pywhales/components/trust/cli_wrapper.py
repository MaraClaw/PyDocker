from typing import NoReturn

from pywhales.client_config import ClientConfig, DockerCLICaller


class TrustCLI(DockerCLICaller):
    def __init__(self, client_config: ClientConfig) -> None:
        super().__init__(client_config)
        self.key = None
        self.signer = None

    def inspect(self) -> NoReturn:
        """Not yet implemented"""
        raise NotImplementedError

    def revoke(self) -> NoReturn:
        """Not yet implemented"""
        raise NotImplementedError

    def sign(self) -> NoReturn:
        """Not yet implemented"""
        raise NotImplementedError
