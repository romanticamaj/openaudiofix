import hashlib
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("AFIX_DEFAULT_EXPORT", "")  # tests never save exports into the real Documents folder
REPO = Path(__file__).resolve().parents[1]
PRIVATE_DIR = Path(os.environ.get("AFIX_PRIVATE_AUDIO", REPO / "private-audio"))


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


@pytest.fixture
def private_audio():
    def get(name):
        p = PRIVATE_DIR / name
        if not p.exists():
            pytest.skip(f"private regression audio {name} not available (set AFIX_PRIVATE_AUDIO)")
        return p
    return get


@pytest.fixture(scope="session")
def v5_refs():
    return json.loads((REPO / "tests" / "regression" / "v5_refs.json").read_text(encoding="utf-8"))
