import numpy as np
import pytest

from openaudiofix.errors import StageCheckError
from openaudiofix.modules import base
from openaudiofix import modules


@pytest.fixture
def fake_modules():
    """Registers fake modules and always removes them again (Ruling P7)."""
    added = []

    def add(m):
        added.append(m.id)
        return modules.register(m)

    yield add
    for i in added:
        modules.unregister(i)


def test_changed_regions_merges_close_changes_and_spans_channels():
    a = np.zeros((1000, 2))
    b = a.copy()
    b[100, 0] = 1; b[150, 1] = 1; b[600:610, 0] = 1
    fx = base.changed_regions(a, b, "seam", merge=64)
    assert fx == [base.Fix(100, 151, -1, "seam"), base.Fix(600, 610, -1, "seam")]


def test_changed_regions_empty_when_identical():
    a = np.ones((10, 1))
    assert base.changed_regions(a, a.copy(), "seam") == []


def test_check_output_rejects_shape_change_and_nan():
    a = np.zeros((100, 2))
    base.check_output(a, a.copy())
    with pytest.raises(StageCheckError):
        base.check_output(a, np.zeros((99, 2)))
    bad = a.copy(); bad[5, 1] = np.nan
    with pytest.raises(StageCheckError):
        base.check_output(a, bad)


def test_channels_of_returns_contiguous_copies():
    a = np.arange(12, dtype=np.float64).reshape(6, 2)
    ch = base.channels_of(a)
    assert len(ch) == 2 and ch[1].flags["C_CONTIGUOUS"]
    ch[0][0] = 99
    assert a[0, 0] == 0


def test_registry_orders_by_default_order(fake_modules):
    class A(base.RepairModule):
        id, default_order = "zz_a", 50
    class B(base.RepairModule):
        id, default_order = "zz_b", 5
    fake_modules(A()); fake_modules(B())
    assert [m.id for m in modules.ordered(["zz_a", "zz_b"])] == ["zz_b", "zz_a"]
    with pytest.raises(KeyError):
        modules.get("nope")


def test_fake_modules_do_not_leak_between_tests():
    assert not [m for m in modules.all_modules() if m.id.startswith("zz_")]
