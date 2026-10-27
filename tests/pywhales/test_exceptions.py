import pytest

from pywhales import DockerClient
from pywhales.exceptions import DockerException


@pytest.mark.parametrize("raw", [False, True])
def test_exception_rendering_keeps_raw_attributes_when_policy_changes(
    raw: bool,
) -> None:
    secret = "exception-secret-sentinel"

    error = DockerException(
        ["engine", secret], 7, secret.encode(), secret.encode(), raw_diagnostics=raw
    )

    assert (secret in str(error)) is raw
    assert (secret in repr(error)) is raw
    assert error.docker_command == ["engine", secret]
    assert error.stdout == error.stderr == secret
    assert error.return_code == 7


def test_exception_rendering_is_safe_when_policy_is_omitted() -> None:
    error = DockerException(
        ["engine", "default-secret"], 9, b"default-secret", b"default-secret"
    )

    assert "default-secret" not in str(error)
    assert "9" in str(error)


def test_exception_attributes() -> None:
    with pytest.raises(DockerException) as excinfo:
        DockerClient(raw_diagnostics=True).image.tag("wrong::image", "wrong::tag")

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
        DockerClient(raw_diagnostics=True).login(
            username="ignore_user", password=chosen_password
        )
    exception = excinfo.value
    assert "The command executed was" in str(exception)
    assert chosen_password not in str(exception)
    assert chosen_password not in exception.docker_command
    assert exception.return_code > 0
    assert not bool(exception.stdout)
