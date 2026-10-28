"""Bounded synchronous FIFO admission independent of SDK execution."""

from collections.abc import Callable, Iterable, Iterator, Mapping
from sys import float_info
from threading import TIMEOUT_MAX, Condition
from time import monotonic
from typing import assert_never

from ._contracts import (
    AdmissionSnapshot,
    ClassLimit,
    EndpointIdentity,
    PressureClass,
    SaturationPolicy,
    validate_drain_timeout,
    validate_queue_timeout,
)
from ._errors import (
    AdmissionCancelled,
    AdmissionQueueTimeout,
    AdmissionReentryError,
    AdmissionRejected,
    RejectionReason,
)
from ._guard import check_reentry
from ._request import AdmissionRequest
from ._state import Bucket, Outcome, Ticket


class AdmissionOwner:
    """Application-shared, in-process admission state for one logical endpoint.

    Share this exact owner to share limits; equal endpoint aliases do not merge
    owners. Only pending tickets are registered, never dormant handles or user
    callables. Same-owner nesting in one context fails immediately. Cross-thread
    nested admission is unsupported and may deadlock if the outer work waits.
    """

    def __init__(
        self, endpoint: EndpointIdentity, limits: Mapping[PressureClass, ClassLimit]
    ) -> None:
        """Copy a nonempty mapping of immutable per-class limits."""
        if not limits:
            raise ValueError("limits must not be empty")
        self._endpoint = endpoint
        self._buckets = {pressure: Bucket(limit) for pressure, limit in limits.items()}
        self._condition = Condition()
        self._closed = False

    def close(self) -> None:
        """Permanently reject new admission and wake all pending callers.

        Closure is idempotent and serialized with cancellation and grants. Active
        work keeps its permits; streams still require consumption or explicit
        cleanup. Dormant requests remain unregistered and reject on activation.
        """
        with self._condition:
            if self._closed:
                return
            self._closed = True
            for bucket in self._buckets.values():
                for ticket in bucket.pending:
                    ticket.done = True
                    bucket.record(Outcome.REJECTED_CLOSED)
                bucket.pending.clear()
            self._condition.notify_all()

    def wait_drained(self, *, timeout: float) -> bool:
        """Wait for all active permits without closing or cancelling work.

        An open owner may accept more work after this returns. Close it first for
        terminal draining. Streams retain permits through source cleanup and
        in-flight reads, even after their local child processes exit.

        Args:
            timeout: Positive finite seconds from this call, including time spent
                awaiting the admission lock. Individual waits respect platform limits.

        Returns:
            True if no active permits remain, or False if the deadline expires
            while active work remains.

        Raises:
            ValueError: If timeout is boolean, nonpositive, or nonfinite.
            AdmissionReentryError: If an active callback on this owner would wait
                for its own permit. Cross-thread nesting remains unsupported.
        """
        validate_drain_timeout(timeout)
        check_reentry(id(self))
        deadline = monotonic() + min(timeout, float_info.max)
        with self._condition:
            while any(bucket.active for bucket in self._buckets.values()):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(min(remaining, TIMEOUT_MAX))
            return True

    def request(
        self, pressure_class: PressureClass, queue_timeout: float | None = None
    ) -> AdmissionRequest:
        """Return an inert single-use request without occupying pending capacity.

        A closed owner still returns inert handles; activation rejects without
        invoking user work unless cancellation was already terminal before close.

        Args:
            pressure_class: A configured application-defined pressure class.
            queue_timeout: Positive finite seconds spent awaiting admission, or
                None for no queue deadline. Timing starts at call() or stream().
        """
        validate_queue_timeout(queue_timeout)
        if pressure_class not in self._buckets:
            raise ValueError("pressure class is not configured")
        return AdmissionRequest(self, self._buckets[pressure_class], queue_timeout)

    def iter_calls[T](
        self,
        pressure_class: PressureClass,
        operations: Iterable[Callable[[], T]],
        queue_timeout: float | None = None,
    ) -> Iterator[T]:
        """Return a sequential pull-driven iterator with no prefetch or backlog.

        Each next() pulls one callable and admits it. Construction never advances
        the input; producer and operation failures propagate without consuming
        later work. This does not add an executor or change SDK child scheduling.
        """
        self.request(pressure_class, queue_timeout)

        def consume() -> Iterator[T]:
            for operation in operations:
                yield self.request(pressure_class, queue_timeout).call(operation)

        return consume()

    def snapshot(self) -> AdmissionSnapshot:
        """Return an immutable, bounded snapshot under the admission lock."""
        with self._condition:
            return AdmissionSnapshot(
                self._endpoint,
                tuple(
                    bucket.snapshot(pressure)
                    for pressure, bucket in self._buckets.items()
                ),
                self._closed,
            )

    def _acquire(self, request: AdmissionRequest) -> None:
        ticket, bucket = request._ticket, request._bucket
        deadline = None if request._timeout is None else monotonic() + request._timeout
        with self._condition:
            if ticket.started:
                raise RuntimeError("admission request is single-use")
            ticket.started = True
            if ticket.cancelled:
                raise AdmissionCancelled()
            if self._closed:
                ticket.done = True
                bucket.record(Outcome.REJECTED_CLOSED)
                raise AdmissionRejected(RejectionReason.CLOSED)
            try:
                check_reentry(id(self))
            except AdmissionReentryError:
                ticket.done = True
                bucket.record(Outcome.FAILED)
                raise
            if bucket.active < bucket.limit.active and not bucket.pending:
                if deadline is not None and monotonic() >= deadline:
                    ticket.done = True
                    bucket.record(Outcome.QUEUE_TIMED_OUT)
                    raise AdmissionQueueTimeout()
                self._grant(bucket, ticket)
                return
            match bucket.limit.policy:
                case SaturationPolicy.REJECT:
                    ticket.done = True
                    bucket.record(Outcome.REJECTED_BUSY)
                    raise AdmissionRejected(RejectionReason.BUSY)
                case SaturationPolicy.WAIT:
                    if len(bucket.pending) >= bucket.limit.pending:
                        ticket.done = True
                        bucket.record(Outcome.REJECTED_FULL)
                        raise AdmissionRejected(RejectionReason.QUEUE_FULL)
                case unreachable:
                    assert_never(unreachable)
            bucket.pending.append(ticket)
            bucket.pending_high_water = max(
                bucket.pending_high_water, len(bucket.pending)
            )
            try:
                while True:
                    if ticket.cancelled:
                        raise AdmissionCancelled()
                    if self._closed:
                        raise AdmissionRejected(RejectionReason.CLOSED)
                    remaining = None if deadline is None else deadline - monotonic()
                    if remaining is not None and remaining <= 0:
                        ticket.done = True
                        bucket.record(Outcome.QUEUE_TIMED_OUT)
                        raise AdmissionQueueTimeout()
                    if (
                        bucket.pending[0] is ticket
                        and bucket.active < bucket.limit.active
                    ):
                        self._grant(bucket, ticket)
                        return
                    self._condition.wait(
                        None if remaining is None else min(remaining, TIMEOUT_MAX)
                    )
            except BaseException:
                if not ticket.done:
                    ticket.done = True
                    bucket.record(Outcome.FAILED)
                raise
            finally:
                if ticket in bucket.pending:
                    bucket.pending.remove(ticket)
                self._condition.notify_all()

    def _grant(self, bucket: Bucket, ticket: Ticket) -> None:
        ticket.active = True
        bucket.active += 1
        bucket.active_high_water = max(bucket.active_high_water, bucket.active)

    def _cancel(self, request: AdmissionRequest) -> bool:
        with self._condition:
            ticket, bucket = request._ticket, request._bucket
            if self._closed or ticket.cancelled or ticket.active or ticket.done:
                return False
            ticket.cancelled = True
            ticket.done = True
            bucket.record(Outcome.CANCELLED)
            if ticket in bucket.pending:
                bucket.pending.remove(ticket)
            self._condition.notify_all()
            return True

    def _release(self, request: AdmissionRequest, outcome: Outcome) -> None:
        with self._condition:
            request._ticket.active = False
            request._ticket.done = True
            request._bucket.active -= 1
            request._bucket.record(outcome)
            self._condition.notify_all()
