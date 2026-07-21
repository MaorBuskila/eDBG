"""Trace mode: history, per-step registers, and the trace itself.

A flow run is finished data on disk, not live process state, so these three
panes are driven by a selection (which run, which step) rather than by a stop.
That selection is the only thing that can desynchronise them, so it is what
these tests pin down.
"""

from __future__ import annotations

import os

import pytest

import dearpygui.dearpygui as dpg
from gui import flowtrace, modes, parse
from gui.session import EdbgSession
import gui.app as app

BARE = """\
step,va,rva
0,0x7ab60abf68,0x1c6f68
1,0x7ab60abf6c,0x1c6f6c
2,0x7ab621cca4,0x337ca4
"""

_REG_COLS = ",".join(f"x{i}" for i in range(30)) + ",lr,sp,pc,pstate"
_REGS_0 = ",".join(f"0x{i:x}" for i in range(30)) + ",0xa,0xb,0x7ab60abf68,0x0"
_REGS_1 = ",".join(f"0x{i:x}" for i in range(30)) + ",0xa,0xb,0x7ab60abf6c,0x0"
WITH_REGS = (f"step,va,rva,{_REG_COLS}\n"
             f"0,0x7ab60abf68,0x1c6f68,{_REGS_0}\n"
             f"1,0x7ab60abf6c,0x1c6f6c,{_REGS_1}\n")


@pytest.fixture()
def built_ctx():
    dpg.create_context()
    dpg.configure_app(docking=False)
    app._build_layout()
    app._session = EdbgSession()
    app._flow_runs = []
    app._flow_selected = None
    app._flow_step = 0
    app._dirty.clear()
    app._set_mode("trace")
    yield
    app._flow_runs = []
    app._flow_selected = None
    app._dirty.clear()
    # The active mode is a module global; leaving it on trace would make the
    # next test file's freshly built layout start in the wrong mode.
    app._active_mode = modes.DEFAULT_MODE
    dpg.destroy_context()


def _rows(pane: str) -> int:
    return len(dpg.get_item_children(app.TAG[pane], 1))


def _labels(pane: str) -> list:
    out = []
    for item in dpg.get_item_children(app.TAG[pane], 1):
        cfg = dpg.get_item_configuration(item)
        if cfg.get("label"):
            out.append(cfg["label"])
    return out


def _run(tmp_path, name: str, text: str):
    p = tmp_path / name
    p.write_text(text)
    return flowtrace.parse_flow_csv(str(p))


# ── Task 4: the trace pane ───────────────────────────────────────────

def test_a_new_run_is_selected_and_marks_all_three_panes(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    assert app._flow_selected == 0
    assert app._flow_step == 0
    assert {"pane_flow", "pane_flow_history",
            "pane_flow_regs"} <= app._dirty.pending()


def test_trace_renders_a_header_and_one_row_per_step(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    app._populate_flow()
    assert _rows("pane_flow") == 1 + 3


def test_trace_rows_carry_step_and_rva(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    app._populate_flow()
    labels = _labels("pane_flow")
    assert len(labels) == 3
    assert "0x337ca4" in labels[2]
    assert "#2" in labels[2]


def test_clicking_a_row_selects_that_step(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    app._dirty.clear()
    app._cb_flow_step(None, True, 2)
    assert app._flow_step == 2
    assert "pane_flow_regs" in app._dirty.pending()


def test_empty_trace_pane_without_a_run(built_ctx):
    app._dirty.mark("pane_flow")
    app._populate_flow()
    assert _rows("pane_flow") == 1


def test_long_runs_render_a_window_not_every_step(built_ctx, tmp_path):
    text = "step,va,rva\n" + "".join(
        f"{i},0x{0x1000 + i:x},0x{i:x}\n" for i in range(app._FLOW_MAX_ROWS * 2))
    app._add_flow_run(_run(tmp_path, "big_0x1_flow.csv", text))
    app._populate_flow()
    # header + capped rows + the "showing" note
    assert _rows("pane_flow") == 1 + app._FLOW_MAX_ROWS + 1


def test_the_window_follows_the_selected_step(built_ctx, tmp_path):
    text = "step,va,rva\n" + "".join(
        f"{i},0x{0x1000 + i:x},0x{i:x}\n" for i in range(app._FLOW_MAX_ROWS * 2))
    app._add_flow_run(_run(tmp_path, "big_0x1_flow.csv", text))
    deep = app._FLOW_MAX_ROWS * 2 - 1
    app._cb_flow_step(None, True, deep)
    app._populate_flow()
    assert any(f"#{deep}" in l for l in _labels("pane_flow")), \
        "a step outside the window cannot be reviewed"


# ── Task 5: the history pane ─────────────────────────────────────────

def test_history_lists_every_run_newest_first(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    app._add_flow_run(_run(tmp_path, "b_0x2_flow.csv", BARE))
    app._populate_flow_history()
    labels = _labels("pane_flow_history")
    assert len(labels) == 2
    assert "0x2" in labels[0] and "0x1" in labels[1]


def test_rerunning_the_same_rva_adds_an_entry(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    assert len(app._flow_runs) == 2, "a re-run must not erase what it replaced"


def test_selecting_an_older_run_resets_the_step(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    app._add_flow_run(_run(tmp_path, "b_0x2_flow.csv", BARE))
    app._cb_flow_step(None, True, 2)
    app._dirty.clear()
    app._cb_flow_run(None, True, 1)
    assert app._flow_selected == 1
    assert app._flow_step == 0
    assert {"pane_flow", "pane_flow_regs"} <= app._dirty.pending()


def test_history_is_seeded_from_already_pulled_csvs(built_ctx, tmp_path):
    (tmp_path / "a_0x1_flow.csv").write_text(BARE)
    (tmp_path / "b_0x2_flow.csv").write_text(BARE)
    os.utime(tmp_path / "a_0x1_flow.csv", (1000, 1000))
    os.utime(tmp_path / "b_0x2_flow.csv", (2000, 2000))
    app._seed_flow_history(str(tmp_path))
    assert [r.lib for r in app._flow_runs] == ["b", "a"]
    assert app._flow_selected == 0


def test_seeding_an_empty_dir_selects_nothing(built_ctx, tmp_path):
    app._seed_flow_history(str(tmp_path))
    assert app._flow_runs == [] and app._flow_selected is None


def test_a_pulled_csv_enters_history(built_ctx, tmp_path, monkeypatch):
    remote = "/data/local/tmp/libloader_0x1c6f68_flow.csv"
    (tmp_path / "src.csv").write_text(BARE)

    def fake_pull(src, dest):
        assert src == remote
        with open(dest, "w") as fh:
            fh.write(BARE)
        return True

    monkeypatch.setattr(app._session, "pull_file", fake_pull)
    monkeypatch.setattr(app, "_FLOW_DATA_DIR", str(tmp_path))
    app._handle_flow_csv([f"Trace saved to {remote} (3 steps)"])
    assert len(app._flow_runs) == 1
    assert app._flow_runs[0].rva == 0x1C6F68


# ── Task 6: per-step registers ───────────────────────────────────────

def test_step_registers_come_from_the_csv(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", WITH_REGS))
    app._populate_flow_regs()
    assert _rows("pane_flow_regs") == 1 + 34


def test_step_registers_track_the_selected_step(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", WITH_REGS))
    app._cb_flow_step(None, True, 1)
    app._populate_flow_regs()
    assert app._flow_changed_regs() == {"pc"}


def test_first_step_has_nothing_to_diff_against(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", WITH_REGS))
    app._populate_flow_regs()
    assert app._flow_changed_regs() == set()


def test_a_run_without_regs_falls_back_to_live_registers(built_ctx, tmp_path):
    app._session.last_regs = [
        parse.RegisterInfo(name="X0", value=0x1234, deref=None, symbol="")]
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    app._populate_flow_regs()
    # one note about the missing --regs, plus the live registers
    assert _rows("pane_flow_regs") == 1 + 1


def test_no_run_selected_shows_a_placeholder(built_ctx):
    app._dirty.mark("pane_flow_regs")
    app._populate_flow_regs()
    assert _rows("pane_flow_regs") == 1


# ── gating still applies ─────────────────────────────────────────────

def test_flow_panes_do_not_paint_outside_trace_mode(built_ctx, tmp_path):
    app._set_mode("step")
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", WITH_REGS))
    app._populate_flow()
    app._populate_flow_regs()
    app._populate_flow_history()
    assert {"pane_flow", "pane_flow_regs",
            "pane_flow_history"} <= app._dirty.pending(), \
        "a hidden pane keeps its dirty flag for the reveal"
    app._set_mode("trace")
    assert not ({"pane_flow", "pane_flow_regs", "pane_flow_history"}
                & app._dirty.pending()), "reveal must paint them"
