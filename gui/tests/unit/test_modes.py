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


def test_every_pane_has_a_floor_and_a_row_weight():
    # A pane missing from either table crashes the height arithmetic on the
    # first mode switch that shows it.
    for pane in modes.PANES:
        assert pane in modes.MIN_COL_FRAC, f"{pane} has no MIN_COL_FRAC"
        assert pane in modes.PANE_ROW_WEIGHT, f"{pane} has no PANE_ROW_WEIGHT"


def test_trace_reads_history_then_the_selected_step():
    # Left to right is the order the operator works in: pick a run and a step in
    # the tree, then read what that step captured.
    assert modes.MODES["trace"].panes["left"] == ("pane_flow_history",)
    assert modes.MODES["trace"].panes["center"] == ("pane_flow_regs", "pane_flow")
    assert modes.MODES["trace"].panes["right"] == ("pane_flow_mem", "pane_flow_tls")


def test_every_step_detail_pane_is_optional():
    # SPEC_gui_v4 AC 4: the operator chooses which captured data is on screen.
    assert modes.MODES["trace"].optional == frozenset(
        {"pane_flow_regs", "pane_flow", "pane_flow_mem", "pane_flow_tls"})


def test_optional_panes_belong_to_their_own_mode():
    # An optional pane the mode never shows is a toggle that does nothing.
    for name, mode in modes.MODES.items():
        shown = {p for col in modes.COLUMNS for p in mode.panes[col]}
        assert mode.optional <= shown, f"{name} makes unshown panes optional"


def test_only_trace_swaps_the_control_row():
    # SPEC_gui_v4 AC 3: run controls act on a live process, so the mode reading a
    # finished run gets data toggles in that row instead.
    assert modes.MODES["trace"].controls == "trace"
    for name in modes.MODES:
        if name != "trace":
            assert modes.MODES[name].controls == "run"


def test_row_weights_renormalise_over_survivors():
    # Hiding TLS must give its pixels to Memory, not leave a gap.
    full = modes.row_weights("trace", "right")
    assert sum(full.values()) == pytest.approx(1.0)
    survivors = modes.row_weights("trace", "right", hidden={"pane_flow_tls"})
    assert set(survivors) == {"pane_flow_mem"}
    assert survivors["pane_flow_mem"] == pytest.approx(1.0)


def test_hiding_a_whole_column_leaves_no_panes():
    hidden = modes.MODES["trace"].panes["right"]
    assert modes.row_weights("trace", "right", hidden=hidden) == {}


def test_visible_panes_drops_hidden_ones():
    v = modes.visible_panes("trace", hidden={"pane_flow", "pane_flow_tls"})
    assert v == ["pane_flow_history", "pane_flow_regs", "pane_flow_mem"]


def test_watch_belongs_to_inspect_alone():
    # A watch list has nothing to say about a finished trace.
    assert "pane_watch" in modes.visible_panes("inspect")
    for name in modes.MODES:
        if name != "inspect":
            assert "pane_watch" not in modes.visible_panes(name)


def test_live_registers_are_not_in_trace_mode():
    # Trace mode's centre column is per-step registers from the CSV; the live
    # register pane keeps its own column and stays out.
    assert "pane_regs" not in modes.visible_panes("trace")


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
