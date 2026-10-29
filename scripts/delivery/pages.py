"""Check the actual Pages tar wrapper before permitting deployment."""

from pathlib import Path

from . import limits
from .api import require
from .archives import safe_path, tar_files
from .artifacts import sha256
from .budgets import Expansion, local


def verify_pages_tar(site: Path, archive: Path) -> None:
    """Require the uploader's tar to preserve every verified site file exactly."""
    data = local(archive, limits.PAGES_TAR_BYTES)
    contents = tar_files(data, limits.SITE, pages=True)
    actual = {name: sha256(content) for name, content in contents.items()}
    expected: dict[str, str] = {}
    budget = Expansion(limits.SITE)
    for path in site.rglob("*"):
        require(
            not path.is_symlink() and (path.is_file() or path.is_dir()),
            "Pages site special file",
        )
        safe_path(path.relative_to(site).as_posix())
        budget.entry(0)
        if path.is_file():
            with path.open("rb") as stream:
                expected[path.relative_to(site).as_posix()] = sha256(
                    budget.member(stream)
                )
    require(
        bool(expected) and actual == expected, "Pages archive differs from tested site"
    )
