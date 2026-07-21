"""Dirty-flag core — pure, no dearpygui.

The whole point: the flag clears on *paint*, never on *mark*. That single rule
is what makes repaint-on-reveal fall out for free — a pane hidden across N
stops is still dirty when its mode is finally selected, so it renders current
state rather than stale state or nothing at all.
"""

from __future__ import annotations

import subprocess
import sys

from gui.dirty import DirtySet


def test_imports_without_dearpygui():
    src = "import sys; sys.modules['dearpygui'] = None; import gui.dirty"
    r = subprocess.run([sys.executable, "-c", src], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_clean_pane_is_not_taken():
    d = DirtySet()
    assert d.take("pane_regs") is False


def test_mark_then_take_paints_once():
    d = DirtySet()
    d.mark("pane_regs")
    assert d.take("pane_regs") is True
    assert d.take("pane_regs") is False, "take must clear — no double paint"


def test_marked_n_times_while_hidden_is_still_dirty():
    # Spec AC 4. A pane nobody painted across many stops must still repaint the
    # instant it becomes visible.
    d = DirtySet()
    for _ in range(50):
        d.mark("pane_memory")
    assert d.take("pane_memory") is True
    assert d.take("pane_memory") is False


def test_marks_are_independent_per_pane():
    d = DirtySet()
    d.mark("pane_regs")
    assert d.take("pane_memory") is False
    assert d.take("pane_regs") is True


def test_pending_reports_unpainted_panes():
    d = DirtySet()
    d.mark("pane_regs")
    d.mark("pane_memory")
    assert d.pending() == {"pane_regs", "pane_memory"}
    d.take("pane_regs")
    assert d.pending() == {"pane_memory"}


def test_pending_is_a_copy():
    d = DirtySet()
    d.mark("pane_regs")
    d.pending().clear()
    assert d.take("pane_regs") is True


def test_mark_all_and_clear():
    d = DirtySet()
    d.mark_all(["pane_regs", "pane_memory"])
    assert d.pending() == {"pane_regs", "pane_memory"}
    d.clear()
    assert d.pending() == set()
