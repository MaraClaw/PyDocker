import logging

import pytest

import pywhales.utils


def test_debug_level_logging(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    command = ["echo", "hello"]
    pywhales.utils.run(command)
    assert " ".join(command) in caplog.text
