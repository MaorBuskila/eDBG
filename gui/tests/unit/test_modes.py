"""Mode registry — pure data, no dearpygui.

The registry is the whole of SPEC_gui_v3's layout model: a mode is
{visible_panes, col_weights} and nothing else. These tests are what make
"adding a mode is a dict entry" true rather than aspirational.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from gui import modes


def test_imports_without_dearpygui():
    # Spec AC 6: the mode table must be assertable on a machine with no DPG.
    src = "import sys; sys.modules['dearpygui'] = None; import gui.modes"
    r = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_four_modes_bound_to_f1_f4():
    assert list(modes.MODES) == ["step", "trace", "inspect", "log"]
    assert [modes.MODE_KEYS[m] for m in modes.MODES] == ["F1", "F2", "F3", "F4"]


def test_every_named_pane_is_a_known_pane():
    # A typo'd pane name would otherwise silently never render.
    for name, mode in modes.MODES.items():
        for col in modes.COLUMNS:
            unknown = set(mode.panes[col]) - set(modes.PANES)
            assert not unknown, f"{name}.{col} names unknown panes: {unknown}"


def test_every_pane_appears_in_at_least_one_mode():
    # A pane in no mode is dead weight the shell still has to build.
    shown = {p for m in modes.MODES.values() for c in modes.COLUMNS for p in m.panes[c]}
    assert set(modes.PANES) == shown, f"unreachable panes: {set(modes.PANES) - shown}"


@pytest.mark.parametrize("name", ["step", "trace", "inspect", "log"])
def test_weights_are_three_positive_floats_summing_to_one(name):
    w = modes.col_weights(name)
    assert len(w) == 3
    assert all(x > 0 for x in w), "a collapsed column gets a tiny weight, never 0"
    assert sum(w) == pytest.approx(1.0)


@pytest.mark.parametrize("name", ["step", "trace", "inspect", "log"])
def test_every_visible_pane_fits_its_column(name):
    # The floors are measured, not guessed — see SPEC_gui_v3.md. This test is
    # what stops a future mode from silently clipping register annotations.
    weights = modes.col_weights(name)
    for col, weight in zip(modes.COLUMNS, weights):
        for pane in modes.MODES[name].panes[col]:
            floor = modes.MIN_COL_FRAC[pane]
            assert weight >= floor, (
                f"{name}.{col} weight {weight} < {pane} floor {floor}")


def test_memory_is_visible_while_stepping():
    # The blind spot this spec exists to remove: stepping in one mode while a
    # watched buffer mutates in another.
    assert "pane_memory" in modes.visible_panes("step")


def test_visible_panes_is_flat_and_ordered_left_to_right():
    v = modes.visible_panes("step")
    assert v.index("pane_regs") < v.index("pane_disasm") < v.index("pane_threads")


def test_unknown_mode_raises():
    with pytest.raises(KeyError):
        modes.col_weights("nope")
