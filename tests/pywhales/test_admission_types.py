from collections.abc import Callable, Iterator
from typing import assert_type

from pywhales import Container, DockerClient
from pywhales.admission import (
    AdmissionOwner,
    AdmissionRequest,
    AdmissionSnapshot,
    AdmittedStream,
    PressureClass,
)
from pywhales.components.image.streams import MultiImageStream
from pywhales.utils import ProcessStream


def _check_admission_generic_inference(
    owner: AdmissionOwner,
    pressure: PressureClass,
    client: DockerClient,
    operations: Iterator[Callable[[], Container]],
    multi: MultiImageStream,
    process: ProcessStream,
) -> None:
    def integer() -> int:
        return 1

    assert_type(owner.request(pressure), AdmissionRequest)
    assert_type(owner.request(pressure).call(lambda: client.create("image")), Container)
    assert_type(owner.request(pressure).call(integer), int)
    assert_type(owner.iter_calls(pressure, operations), Iterator[Container])
    assert_type(
        owner.request(pressure).stream(lambda: process),
        AdmittedStream[tuple[str, bytes]],
    )
    assert_type(
        owner.request(pressure).stream(lambda: multi), AdmittedStream[tuple[str, bytes]]
    )
    assert_type(next(owner.request(pressure).stream(lambda: multi)), tuple[str, bytes])
    assert_type(owner.snapshot(), AdmissionSnapshot)
