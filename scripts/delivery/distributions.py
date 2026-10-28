"""Inspect and isolate-install both exact distribution files before sealing."""

import io
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

from .api import require


def inspect(files: dict[str, bytes], version: str) -> None:
    """Check actual package metadata, typed marker and absence of the old package."""
    wheel = f"pywhales-{version}-py3-none-any.whl"
    sdist = f"pywhales-{version}.tar.gz"
    require(set(files) == {wheel, sdist}, "distribution filenames")
    with zipfile.ZipFile(io.BytesIO(files[wheel])) as archive:
        wheel_files = {
            name: archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }
    with tarfile.open(fileobj=io.BytesIO(files[sdist]), mode="r:gz") as archive:
        sdist_files: dict[str, bytes] = {}
        for member in archive.getmembers():
            require(member.isfile() or member.isdir(), "sdist special file")
            if member.isfile():
                stream = archive.extractfile(member)
                require(stream is not None, "sdist file unreadable")
                assert stream is not None
                with stream:
                    sdist_files[member.name] = stream.read()
    for contents, prefix, metadata in (
        (wheel_files, "", f"pywhales-{version}.dist-info/METADATA"),
        (sdist_files, f"pywhales-{version}/", f"pywhales-{version}/PKG-INFO"),
    ):
        require(prefix + "pywhales/py.typed" in contents, "missing py.typed")
        require(
            not any("python_on_whales" in name.split("/") for name in contents),
            "legacy package present",
        )
        require(metadata in contents, "missing distribution metadata")
        parsed = BytesParser().parsebytes(contents[metadata])
        require(
            parsed["Name"] == "pywhales" and parsed["Version"] == version,
            "distribution metadata mismatch",
        )


def smoke(root: Path) -> None:
    """Install wheel and sdist separately outside the checkout with isolated Python."""
    root = root.resolve()
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    files = {path.name: path.read_bytes() for path in (root / "dist").iterdir()}
    inspect(files, version)
    code = (
        "import importlib.metadata,importlib.util,json,pathlib,sys,pywhales;"
        "from importlib.resources import files;from pywhales import DockerClient;"
        "assert callable(DockerClient);"
        "distribution=importlib.metadata.distribution('pywhales');"
        "assert distribution.version==sys.argv[1];"
        "assert files('pywhales').joinpath('py.typed').is_file();"
        "assert importlib.util.find_spec('python_on_whales') is None;"
        "module=pathlib.Path(pywhales.__file__).resolve();"
        "assert distribution.files is not None;"
        "assert 'pywhales/__init__.py' in {str(file) for file in distribution.files};"
        "assert module==pathlib.Path(distribution.locate_file('pywhales/__init__.py')).resolve();"
        "assert json.loads(distribution.read_text('direct_url.json'))['url']==pathlib.Path(sys.argv[2]).as_uri();"
        "assert all(not module.is_relative_to(pathlib.Path(source).resolve()) for source in sys.argv[3:]);"
        "assert sys.flags.isolated"
    )
    with tempfile.TemporaryDirectory(prefix="delivery-smoke-") as temporary:
        for filename in sorted(files):
            subprocess.run(
                [
                    "uv",
                    "run",
                    "--isolated",
                    "--no-project",
                    "--no-config",
                    "--no-cache",
                    "--python",
                    "3.14.8",
                    "--with",
                    str(root / "dist" / filename),
                    "--",
                    "python",
                    "-I",
                    "-c",
                    code,
                    version,
                    str(root / "dist" / filename),
                    str(root),
                    str(Path(__file__).resolve().parents[2]),
                ],
                cwd=temporary,
                check=True,
                timeout=300,
            )
