import pytest

from pywhales import docker
from pywhales.exceptions import DockerException


def test_exception_attributes() -> None:
    with pytest.raises(DockerException) as excinfo:
        docker.image.tag("wrong::image", "wrong::tag")

    exception = excinfo.value
    assert "The command executed was" in str(exception)
    assert exception.docker_command[1:5] == [
        "image",
        "tag",
        "wrong::image",
        "wrong::tag",
    ]
    # The exact return code and error string might vary with future docker
    # versions, so following asserts are somewhat relaxed
    assert exception.return_code > 0
    assert not bool(exception.stdout)
    assert exception.stderr is not None
    assert "wrong::" in exception.stderr


def test_not_showing_password_in_exception() -> None:
    chosen_password = "ignore_password"
    with pytest.raises(DockerException) as excinfo:
        docker.login(username="ignore_user", password=chosen_password)
    exception = excinfo.value
    assert "The command executed was" in str(exception)
    assert chosen_password not in str(exception)
    assert chosen_password not in exception.docker_command
    assert exception.return_code > 0
    assert not bool(exception.stdout)
