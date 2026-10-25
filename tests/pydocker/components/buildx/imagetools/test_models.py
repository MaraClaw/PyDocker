import json
from pathlib import Path

import pytest

from pydocker.components.buildx.imagetools.models import Manifest
from pydocker.test_utils import get_all_jsons


@pytest.mark.parametrize("json_file", get_all_jsons("manifests"))
def test_load_json(json_file: Path) -> None:
    Manifest(**json.loads(json_file.read_text()))
