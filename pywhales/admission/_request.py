"""Single-use deferred admission handles without retained user operations."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING

from ._guard import operation_scope
from ._state import Bucket, Outcome, Ticket
from ._stream import AdmittedStream

if TYPE_CHECKING:
    from ._owner import AdmissionOwner


class AdmissionRequest:
    """Inert single-use handle; cancellation only applies before active admission."""

    def __init__(
        self, owner: AdmissionOwner, bucket: Bucket, timeout: float | None
    ) -> None:
        """Bind an unregistered ticket to the owner's selected class."""
        self._owner = owner
        self._bucket = bucket
        self._timeout = timeout
        self._ticket = Ticket()

    def call[T](self, operation: Callable[[], T]) -> T:
        """Admit then invoke work once, retaining capacity through BaseException.

        SDK preflights and children must be inside operation and called directly,
        not admitted again through this owner. No active cancellation or rollback
        of daemon-side work is provided. Operation failures retain their identity.
        """
        self._owner._acquire(self)
        outcome = Outcome.FAILED
        try:
            with operation_scope(id(self._owner)):
                result = operation()
            outcome = Outcome.COMPLETED
            return result
        finally:
            self._owner._release(self, outcome)

    def cancel(self) -> bool:
        """Atomically cancel before active admission; return whether this call won."""
        return self._owner._cancel(self)

    def stream[T](
        self, factory: Callable[[], AbstractContextManager[Iterator[T]]]
    ) -> AdmittedStream[T]:
        """Admit before factory invocation and source entry, then own its context.

        Factory and failed entry own partial-resource cleanup. One outer permit
        covers all direct SDK children, including composite image streams. Source
        lifecycle behavior is delegated, not replaced or retrofitted into the SDK.
        """
        self._owner._acquire(self)
        try:
            with operation_scope(id(self._owner)):
                source = factory()
                iterator = source.__enter__()
        except BaseException:
            self._owner._release(self, Outcome.FAILED)
            raise
        return AdmittedStream(self, source, iterator)
