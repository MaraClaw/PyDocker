"""Check the actual Pages tar wrapper before permitting deployment."""

import tarfile
from pathlib import Path, PurePosixPath

from .api import require
from .artifacts import sha256


def verify_pages_tar(site: Path, archive: Path) -> None:
    """Require the uploader's tar to preserve every verified site file exactly."""
    expected = {
        path.relative_to(site).as_posix(): sha256(path.read_bytes())
        for path in site.rglob("*")
        if path.is_file()
    }
    actual: dict[str, str] = {}
    with tarfile.open(archive, "r:") as package:
        for member in package.getmembers():
            require(member.isdir() or member.isfile(), "Pages archive special file")
            if member.isdir():
                continue
            path = PurePosixPath(member.name)
            require(
                not path.is_absolute() and ".." not in path.parts, "Pages archive path"
            )
            filename = path.as_posix()
            require(filename not in actual, "Pages archive duplicate file")
            stream = package.extractfile(member)
            require(stream is not None, "Pages archive unreadable file")
            assert stream is not None
            with stream:
                actual[filename] = sha256(stream.read())
    require(
        bool(expected) and actual == expected, "Pages archive differs from tested site"
    )
