import logging

import pytest

import pydocker.utils


def test_debug_level_logging(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    command = ["echo", "hello"]
    pydocker.utils.run(command)
    assert " ".join(command) in caplog.text
