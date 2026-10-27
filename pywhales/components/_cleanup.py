"""Resource-context cleanup that preserves body and removal failures."""

from collections.abc import Generator
from contextlib import contextmanager


@contextmanager
def resource_cleanup(body_error: BaseException | None, message: str) -> Generator[None]:
    """Preserve body and cleanup failures during resource context exit.

    Args:
        body_error: Exception from the resource context body, or None if it succeeded.
        message: Description for the group raised when both operations fail.

    Yields:
        Control to the resource-removal operation.

    Raises:
        BaseExceptionGroup: If cleanup fails while a body exception is active.
        BaseException: The original cleanup failure if the body succeeded.
    """
    try:
        yield
    except BaseException as cleanup_error:
        if body_error is None:
            raise
        raise BaseExceptionGroup(message, [body_error, cleanup_error]) from None
