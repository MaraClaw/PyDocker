from collections.abc import Generator
from contextlib import contextmanager


@contextmanager
def resource_cleanup(body_error: BaseException | None, message: str) -> Generator[None]:
    try:
        yield
    except BaseException as cleanup_error:
        if body_error is None:
            raise
        raise BaseExceptionGroup(message, [body_error, cleanup_error]) from None
