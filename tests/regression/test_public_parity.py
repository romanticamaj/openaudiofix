import json
from pathlib import Path

import pytest

from openaudiofix import chain, io as aio
from tests.conftest import sha256_file
from tests.regression.public_cases import CASES, platform_tag, write_case

REFS = json.loads((Path(__file__).parent / "public_refs.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "name",
    [pytest.param(n, marks=pytest.mark.slow) if n == "declip" else n for n in sorted(CASES)],
)
def test_public_case_matches_v5(name, tmp_path):
    ref = REFS[name]
    src = aio.decode(write_case(name, tmp_path / f"{name}.wav"))
    mods = CASES[name]["modules"]
    stages = chain.run_chain(src.audio, src.sr, mods)
    info = aio.export(stages[-1].audio, src.sr, tmp_path / "out.wav", src,
                      reduce_gain=chain.reduce_gain_for(mods))
    n = stages[-1].notes.get("n_seams", len(stages[-1].fixes))
    assert n == ref["n_fixes"]
    assert info["gain_db"] == pytest.approx(ref["gain_db"], abs=0.05)
    if ref["platform"] == platform_tag():
        assert sha256_file(tmp_path / "out.wav") == ref["sha256"]
