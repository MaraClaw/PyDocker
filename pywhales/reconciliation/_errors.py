"""Safe, payload-free reconciliation failures.

Adapters must translate backend failures using these errors with ``from None``;
never attach backend exceptions, credentials, commands or response bodies.
"""


class ReconciliationError(Exception):
    """Base for errors containing only fixed diagnostic messages."""


class IdentityConflict(ReconciliationError):
    """An operation identity already names different immutable intent."""

    def __init__(self) -> None:
        super().__init__("Operation identity conflicts with submitted intent")


class StoreConflict(ReconciliationError):
    """A conditional version, lease, or fencing generation no longer matches."""

    def __init__(self) -> None:
        super().__init__("Journal version or worker lease conflicts")


class StoreUnavailable(ReconciliationError):
    """Recoverable store failure; stop decisions and recover by reading again."""

    def __init__(self) -> None:
        super().__init__("Journal unavailable; stop actions and resume recovery later")


class AdapterUnavailable(ReconciliationError):
    """Inventory or convergence observation failed without usable evidence."""

    def __init__(self) -> None:
        super().__init__("Reconciliation observation unavailable")


class AmbiguousMutation(ReconciliationError):
    """A remote mutation may have happened; inventory must precede recovery."""

    def __init__(self) -> None:
        super().__init__(
            "Mutation outcome unknown; reconcile inventory before decisions"
        )


class InvalidDescription(ReconciliationError):
    """Input does not meet the journal's bounded metadata schema."""

    def __init__(self) -> None:
        super().__init__("Invalid reconciliation metadata")
