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
    # This file tests what the panes render, so every one of them is on. Which
    # of them are off by default is a mode-switch concern, tested there.
    off = set(app._pane_off)
    app._pane_off.clear()
    app._set_mode("trace")
    yield
    app._pane_off.clear()
    app._pane_off.update(off)
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


def _texts(pane: str) -> list:
    """Every string rendered in `pane`, groups flattened."""
    out = []
    for item in dpg.get_item_children(app.TAG[pane], 1):
        cfg = dpg.get_item_configuration(item)
        if cfg.get("label"):
            out.append(cfg["label"])
        value = dpg.get_value(item)
        if isinstance(value, str) and value:
            out.append(value)
        for child in dpg.get_item_children(item, 1):
            ccfg = dpg.get_item_configuration(child)
            if ccfg.get("label"):
                out.append(ccfg["label"])
            cvalue = dpg.get_value(child)
            if isinstance(cvalue, str) and cvalue:
                out.append(cvalue)
    return out


def _selectables(pane: str) -> list:
    """Clickable rows in `pane`, at either nesting level."""
    out = []
    for item in dpg.get_item_children(app.TAG[pane], 1):
        items = [item] + list(dpg.get_item_children(item, 1))
        for i in items:
            if dpg.get_item_type(i).endswith("mvSelectable"):
                out.append(i)
    return out


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


# ── v4: the history tree ─────────────────────────────────────────────

def _children(item) -> list:
    return dpg.get_item_children(item, 1)


def _tree_nodes() -> list:
    return _children(app.TAG["pane_flow_history"])


def _child_labels(item) -> list:
    out = []
    for c in _children(item):
        cfg = dpg.get_item_configuration(c)
        if cfg.get("label"):
            out.append(cfg["label"])
    return out


META = '"# lib=libloader.so,rva=0x1c6f68,symbol=Java_com_foo_bar"\n'


def test_a_run_root_names_the_function_it_entered(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "libloader_0x1c6f68_flow.csv", META + BARE))
    app._populate_flow_history()
    label = dpg.get_item_configuration(_tree_nodes()[0])["label"]
    assert "libloader+0x1c6f68" in label
    assert "Java_com_foo_bar" in label
    assert "3 steps" in label


def test_a_run_without_a_symbol_still_reads_cleanly(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    app._populate_flow_history()
    label = dpg.get_item_configuration(_tree_nodes()[0])["label"]
    assert "libloader+0x1c6f68" in label
    assert "<>" not in label and "  <" not in label


def test_the_selected_run_lists_its_steps(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    app._populate_flow_history()
    labels = _child_labels(_tree_nodes()[0])
    assert len(labels) == 3
    assert "0x1c6f6c" in labels[1]


def test_an_unselected_run_costs_one_widget_not_its_whole_trace(built_ctx, tmp_path):
    # Every DPG row is laid out each frame whether its node is open or not, so
    # history of ten long runs must not be ten long lists.
    text = "step,va,rva\n" + "".join(
        f"{i},0x{0x1000 + i:x},0x{i:x}\n" for i in range(app._FLOW_MAX_ROWS * 2))
    app._add_flow_run(_run(tmp_path, "big_0x1_flow.csv", text))
    app._add_flow_run(_run(tmp_path, "b_0x2_flow.csv", BARE))
    app._populate_flow_history()
    assert len(_children(_tree_nodes()[1])) == 1


def test_opening_an_older_run_selects_it_at_its_first_step(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    app._add_flow_run(_run(tmp_path, "b_0x2_flow.csv", BARE))
    app._cb_flow_step(None, True, 2)
    app._populate_flow_history()
    opener = _children(_tree_nodes()[1])[0]
    cfg = dpg.get_item_configuration(opener)
    app._cb_flow_pick(None, True, cfg["user_data"])
    assert app._flow_selected == 1 and app._flow_step == 0


def test_clicking_a_step_selects_that_step_of_that_run(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    app._populate_flow_history()
    step_row = _children(_tree_nodes()[0])[2]
    cfg = dpg.get_item_configuration(step_row)
    app._cb_flow_pick(None, True, cfg["user_data"])
    assert (app._flow_selected, app._flow_step) == (0, 2)


def test_picking_a_step_marks_every_detail_pane(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    app._dirty.clear()
    app._cb_flow_pick(None, True, (0, 1))
    assert {"pane_flow", "pane_flow_regs", "pane_flow_mem",
            "pane_flow_tls"} <= app._dirty.pending()


def test_a_long_run_lists_a_window_of_its_steps(built_ctx, tmp_path):
    text = "step,va,rva\n" + "".join(
        f"{i},0x{0x1000 + i:x},0x{i:x}\n" for i in range(app._FLOW_MAX_ROWS * 2))
    app._add_flow_run(_run(tmp_path, "big_0x1_flow.csv", text))
    app._populate_flow_history()
    # windowed steps plus the note saying what was clipped
    assert len(_children(_tree_nodes()[0])) == app._FLOW_MAX_ROWS + 1


def test_the_step_window_follows_the_selection(built_ctx, tmp_path):
    text = "step,va,rva\n" + "".join(
        f"{i},0x{0x1000 + i:x},0x{i:x}\n" for i in range(app._FLOW_MAX_ROWS * 2))
    app._add_flow_run(_run(tmp_path, "big_0x1_flow.csv", text))
    deep = app._FLOW_MAX_ROWS * 2 - 1
    app._cb_flow_pick(None, True, (0, deep))
    app._populate_flow_history()
    labels = _child_labels(_tree_nodes()[0])
    assert any(str(deep) in l for l in labels), \
        "a step outside the window cannot be reached from the tree"


def test_the_launch_controls_live_with_the_history_they_append_to(built_ctx):
    # Run Flow creates the entries this pane lists, and the run-control row it
    # used to sit beside is gone in Trace mode.
    section = dpg.get_item_children(app.section_tag("pane_flow_history"), 1)
    assert app.TAG["btn_flow"] in [dpg.get_item_alias(i) or i
                                   for group in section
                                   for i in dpg.get_item_children(group, 1)]


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


def test_the_tls_sidecar_is_pulled_with_its_run(built_ctx, tmp_path, monkeypatch):
    remote = "/data/local/tmp/libloader_0x1c6f68_flow.csv"
    remote_tls = "/data/local/tmp/libloader_0x1c6f68_flow_tls.csv"
    asked = []

    def fake_pull(src, dest):
        asked.append(src)
        with open(dest, "w") as fh:
            fh.write(TLS_SIDECAR if src == remote_tls else BARE)
        return True

    monkeypatch.setattr(app._session, "pull_file", fake_pull)
    monkeypatch.setattr(app, "_FLOW_DATA_DIR", str(tmp_path))
    app._handle_flow_csv([f"Trace saved to {remote} (3 steps)"])
    assert remote_tls in asked
    assert app._flow_runs[0].has_tls


def test_a_run_without_a_sidecar_still_enters_history(built_ctx, tmp_path,
                                                      monkeypatch):
    remote = "/data/local/tmp/libloader_0x1c6f68_flow.csv"

    def fake_pull(src, dest):
        if src.endswith("_flow_tls.csv"):
            return False          # --tls was never asked for
        with open(dest, "w") as fh:
            fh.write(BARE)
        return True

    monkeypatch.setattr(app._session, "pull_file", fake_pull)
    monkeypatch.setattr(app, "_FLOW_DATA_DIR", str(tmp_path))
    app._handle_flow_csv([f"Trace saved to {remote} (3 steps)"])
    assert len(app._flow_runs) == 1
    assert not app._flow_runs[0].has_tls


def test_the_launch_command_carries_the_tls_slot_count(built_ctx, monkeypatch):
    sent = []
    monkeypatch.setattr(app._session, "send_command", lambda cmd: sent.append(cmd))
    dpg.set_value(app.TAG["input_flow_addr"], "0x1c6f68")
    dpg.set_value(app.TAG["chk_flow_tls"], True)
    dpg.set_value(app.TAG["input_flow_tls_slots"], "16")
    app._cb_flow(None, None, None)
    assert "--tls 16" in sent[0]


def test_tls_is_left_out_when_it_was_not_asked_for(built_ctx, monkeypatch):
    sent = []
    monkeypatch.setattr(app._session, "send_command", lambda cmd: sent.append(cmd))
    dpg.set_value(app.TAG["input_flow_addr"], "0x1c6f68")
    dpg.set_value(app.TAG["chk_flow_tls"], False)
    app._cb_flow(None, None, None)
    assert "--tls" not in sent[0]


def test_a_pulled_csv_enters_history(built_ctx, tmp_path, monkeypatch):
    remote = "/data/local/tmp/libloader_0x1c6f68_flow.csv"
    (tmp_path / "src.csv").write_text(BARE)

    def fake_pull(src, dest):
        if src.endswith("_flow_tls.csv"):
            return False
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


def test_a_run_without_regs_says_so_instead_of_borrowing_live_ones(
        built_ctx, tmp_path):
    # A step index from a file on disk and a register from the live process are
    # two different moments in time. One pane must not show both.
    app._session.last_regs = [
        parse.RegisterInfo(name="X0", value=0x1234, deref=None, symbol="")]
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    app._populate_flow_regs()
    assert _rows("pane_flow_regs") == 1
    assert "--regs" in _texts("pane_flow_regs")[0]
    assert "0x1234" not in " ".join(_texts("pane_flow_regs"))


def test_no_run_selected_shows_a_placeholder(built_ctx):
    app._dirty.mark("pane_flow_regs")
    app._populate_flow_regs()
    assert _rows("pane_flow_regs") == 1


# ── v4: the step memory probe ────────────────────────────────────────

WITH_MEM = """\
step,va,rva,mem_addr,mem_value
0,0x7ab60abf68,0x1c6f68,0x7b4070d728,0x4142434445464748
1,0x7ab60abf6c,0x1c6f6c,0x7b4070d728,ERR
"""


def test_step_memory_shows_the_probed_pair(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", WITH_MEM))
    app._populate_flow_mem()
    joined = " ".join(_texts("pane_flow_mem"))
    assert "0x7b4070d728" in joined
    assert "0x4142434445464748" in joined


def test_a_failed_probe_reads_as_an_error_not_a_zero(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", WITH_MEM))
    app._cb_flow_pick(None, True, (0, 1))
    app._populate_flow_mem()
    joined = " ".join(_texts("pane_flow_mem"))
    assert "ERR" in joined and "0x0" not in joined


def test_a_run_without_mem_says_which_flag_was_missing(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    app._populate_flow_mem()
    assert "--mem" in " ".join(_texts("pane_flow_mem"))


# ── v4: the step TLS slots ───────────────────────────────────────────

TLS_SIDECAR = """\
step,slot,addr,value,class,annot
0,0,0x7b4070d750,0x7b4070d800,stack,
0,1,0x7b4070d758,0x7ab60e0078,code,libloader.so+0x1fb078
1,0,0x7b4070d750,0x0,junk,|........|
"""


def _run_with_tls(tmp_path, text=BARE):
    (tmp_path / "a_0x1_flow_tls.csv").write_text(TLS_SIDECAR)
    return _run(tmp_path, "a_0x1_flow.csv", text)


def test_step_tls_lists_the_captured_slots(built_ctx, tmp_path):
    app._add_flow_run(_run_with_tls(tmp_path))
    app._populate_flow_tls()
    joined = " ".join(_texts("pane_flow_tls"))
    assert "0x0000007ab60e0078" in joined
    assert "code" in joined
    assert "libloader.so+0x1fb078" in joined


def test_step_tls_follows_the_selected_step(built_ctx, tmp_path):
    app._add_flow_run(_run_with_tls(tmp_path))
    app._cb_flow_pick(None, True, (0, 1))
    app._populate_flow_tls()
    joined = " ".join(_texts("pane_flow_tls"))
    assert "junk" in joined
    assert "0x0000007ab60e0078" not in joined


def test_a_step_the_capture_never_reached_is_empty_not_wrong(built_ctx, tmp_path):
    app._add_flow_run(_run_with_tls(tmp_path))
    app._cb_flow_pick(None, True, (0, 2))
    app._populate_flow_tls()
    assert "(no tls" in " ".join(_texts("pane_flow_tls")).lower()


def test_a_run_without_tls_says_which_flag_was_missing(built_ctx, tmp_path):
    app._add_flow_run(_run(tmp_path, "a_0x1_flow.csv", BARE))
    app._populate_flow_tls()
    assert "--tls" in " ".join(_texts("pane_flow_tls"))


# ── v4: every captured value is clickable, and only copies ───────────

def test_captured_values_are_clickable(built_ctx, tmp_path):
    app._add_flow_run(_run_with_tls(tmp_path, WITH_REGS))
    app._populate_flow_regs()
    app._populate_flow_tls()
    assert _selectables("pane_flow_regs"), "a register value cannot be picked up"
    assert _selectables("pane_flow_tls"), "a TLS slot cannot be picked up"


def test_clicking_a_captured_value_talks_to_no_process(built_ctx, tmp_path,
                                                       monkeypatch):
    # The process this run traced is gone. A click here is a clipboard action.
    sent = []
    monkeypatch.setattr(app._session, "send_command", lambda cmd: sent.append(cmd))
    monkeypatch.setattr(app.dpg, "set_clipboard_text", lambda text: None)
    app._add_flow_run(_run_with_tls(tmp_path, WITH_REGS))
    app._populate_flow_regs()
    for item in _selectables("pane_flow_regs"):
        cfg = dpg.get_item_configuration(item)
        cfg["callback"](item, True, cfg["user_data"])
    assert sent == []


def test_no_flow_pane_reads_live_session_state():
    # SPEC_gui_v4 AC 1. The fallback this replaces rendered live registers under
    # a step index from a file, which is two moments in time in one pane.
    import inspect
    for fn in (app._populate_flow, app._populate_flow_regs,
               app._populate_flow_mem, app._populate_flow_tls,
               app._populate_flow_history, app._flow_regs_to_text):
        src = inspect.getsource(fn)
        assert "_session.last_" not in src, f"{fn.__name__} reads live state"


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
