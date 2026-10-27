"""Test support for resource names, cache timing, and saved JSON fixtures.

Cache timing changes affect process-wide configuration; fixture discovery reads
the repository's test data rather than contacting Docker.
"""

import random
import string
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from pywhales import client_config
from pywhales.utils import PROJECT_ROOT


def random_name() -> str:
    """Return a random 20-character lowercase-alphanumeric test resource name.

    Names are not guaranteed unique and are not suitable as security tokens.
    """
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=20))


@contextmanager
def set_cache_validity_period(x: float) -> Generator[None]:
    """Temporarily replace the process-wide resource-cache validity period.

    Restore the old value only when the context body exits normally; this helper
    does not protect restoration with a finally block. Concurrent callers share
    the same setting.

    Args:
        x: Cache validity period in seconds to use inside the context.

    Yields:
        None while the replacement period is active.
    """
    old = client_config.CACHE_VALIDITY_PERIOD
    client_config.CACHE_VALIDITY_PERIOD = x
    yield
    client_config.CACHE_VALIDITY_PERIOD = old


def get_all_jsons(object_types: str) -> list[Path]:
    """Return saved fixture paths ordered by their numeric filename stems.

    Args:
        object_types: Fixture subdirectory beneath tests/pywhales/components/jsons.

    Returns:
        Directory entries sorted numerically by stem, without filtering suffixes.

    Raises:
        OSError: If the fixture directory cannot be read.
        ValueError: If an entry's stem is not an integer.
    """
    jsons_directory = PROJECT_ROOT / "tests/pywhales/components/jsons" / object_types
    return sorted(jsons_directory.iterdir(), key=lambda x: int(x.stem))
