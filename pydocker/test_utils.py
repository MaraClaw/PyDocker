import random
import string
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from pydocker import client_config
from pydocker.utils import PROJECT_ROOT


def random_name() -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=20))


@contextmanager
def set_cache_validity_period(x: float) -> Generator[None]:
    old = client_config.CACHE_VALIDITY_PERIOD
    client_config.CACHE_VALIDITY_PERIOD = x
    yield
    client_config.CACHE_VALIDITY_PERIOD = old


def get_all_jsons(object_types: str) -> list[Path]:
    jsons_directory = PROJECT_ROOT / "tests/pydocker/components/jsons" / object_types
    return sorted(jsons_directory.iterdir(), key=lambda x: int(x.stem))
