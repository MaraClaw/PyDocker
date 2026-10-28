"""Opt-in, application-owned synchronous admission around deferred SDK work."""

from ._contracts import (
    AdmissionSnapshot,
    ClassLimit,
    ClassSnapshot,
    EndpointIdentity,
    OutcomeCounts,
    PressureClass,
    SaturationPolicy,
)
from ._errors import (
    AdmissionCancelled,
    AdmissionError,
    AdmissionQueueTimeout,
    AdmissionReentryError,
    AdmissionRejected,
    RejectionReason,
)
from ._owner import AdmissionOwner
from ._request import AdmissionRequest
from ._stream import AdmittedStream

__all__ = [
    "AdmissionCancelled",
    "AdmissionError",
    "AdmissionOwner",
    "AdmissionQueueTimeout",
    "AdmissionReentryError",
    "AdmissionRejected",
    "AdmissionRequest",
    "AdmissionSnapshot",
    "AdmittedStream",
    "ClassLimit",
    "ClassSnapshot",
    "EndpointIdentity",
    "OutcomeCounts",
    "PressureClass",
    "RejectionReason",
    "SaturationPolicy",
]
