"""Smoke: does DPG accept the v2 fixed-shell construction? (assumption A3)

The popup crash proved that "the symbol exists" is not the same as "DPG
accepts it here" — `dpg.popup()` exists and still fails on a child window.
So the Phase 3 shell gets its API accepted *before* Task 3.1 starts.

What this cannot tell you is whether dragging a column border feels right.
Run gui/tests/smoke/../../../scratchpad/a3_shell_spike.py for that.
"""

from __future__ import annotations

import pytest

dpg = pytest.importorskip("dearpygui.dearpygui",
                          reason="dearpygui not importable here")

REGIONS = {
    "LEFT": ["Registers", "Stack", "Threads"],
    "CENTER": ["Disassembly", "Backtrace", "Memory", "Flow"],
    "RIGHT": ["Breakpoints", "Watch", "Log"],
}


@pytest.fixture
def ctx():
    dpg.create_context()
    dpg.create_viewport(title="shell smoke", width=1200, height=800)
    yield
    dpg.destroy_context()


def test_docking_can_be_disabled(ctx):
    """v2 removes floating windows entirely."""
    dpg.configure_app(docking=False)


def test_resizable_table_shell_builds(ctx):
    """Three stretch columns with draggable inner borders."""
    with dpg.window(tag="root"):
        with dpg.table(header_row=False, resizable=True, borders_innerV=True,
                       policy=dpg.mvTable_SizingStretchProp, height=-1,
                       tag="shell"):
            for name, weight in (("LEFT", 0.22), ("CENTER", 0.50),
                                 ("RIGHT", 0.28)):
                dpg.add_table_column(init_width_or_weight=weight,
                                     tag=f"col_{name}")
            with dpg.table_row():
                for region, tabs in REGIONS.items():
                    with dpg.table_cell():
                        with dpg.tab_bar(tag=f"tabbar_{region}"):
                            for t in tabs:
                                with dpg.tab(label=t, tag=f"tab_{region}_{t}"):
                                    dpg.add_text(f"{region}/{t}")
    assert dpg.does_item_exist("shell")
    for region in REGIONS:
        assert dpg.does_item_exist(f"tabbar_{region}")


def test_primary_window_replaces_manual_tiling(ctx):
    """set_primary_window auto-fills and tracks the viewport, which is what
    let the old _apply_dock_layout pixel arithmetic be deleted."""
    with dpg.window(tag="root"):
        dpg.add_text("hi")
    dpg.set_primary_window("root", True)


def test_column_visibility_toggles_for_zoom(ctx):
    """F12 zoom hides the side regions."""
    with dpg.window(tag="root"):
        with dpg.table(header_row=False, tag="shell"):
            dpg.add_table_column(tag="col_LEFT")
            dpg.add_table_column(tag="col_CENTER")
            with dpg.table_row():
                dpg.add_text("l")
                dpg.add_text("c")
    dpg.configure_item("col_LEFT", show=False)
    dpg.configure_item("col_LEFT", show=True)


def test_tab_can_be_selected_programmatically(ctx):
    """Layout presets switch the active tab per region."""
    with dpg.window(tag="root"):
        with dpg.tab_bar(tag="tb"):
            with dpg.tab(label="A", tag="tab_a"):
                dpg.add_text("a")
            with dpg.tab(label="B", tag="tab_b"):
                dpg.add_text("b")
    dpg.set_value("tb", "tab_b")


def test_table_clipper_for_large_flow_table(ctx):
    """Assumption A4: 10k-row flow table needs the built-in clipper."""
    with dpg.window(tag="root"):
        with dpg.table(header_row=True, clipper=True, scrollY=True,
                       tag="flow", height=400):
            for c in ("step", "va", "rva"):
                dpg.add_table_column(label=c)
            for i in range(10000):
                with dpg.table_row():
                    dpg.add_text(str(i))
                    dpg.add_text(hex(0x7BE3F81428 + i * 4))
                    dpg.add_text(hex(0x12A40C + i * 4))
    assert dpg.does_item_exist("flow")


def test_clipboard_roundtrip(ctx):
    dpg.set_clipboard_text("eDBG copy check")
    assert dpg.get_clipboard_text() == "eDBG copy check"


def test_readonly_multiline_input_for_selectable_text(ctx):
    """The char-level-selection path for Task 2.4."""
    with dpg.window(tag="root"):
        dpg.add_input_text(tag="raw", multiline=True, readonly=True,
                           default_value="X0 0x1\nX1 0x2", width=-1, height=-1)
    assert dpg.get_value("raw").startswith("X0")


def test_selectable_rows_for_row_selection(ctx):
    """The row-selection path for Task 2.4."""
    with dpg.window(tag="root"):
        with dpg.table(header_row=False, tag="t"):
            dpg.add_table_column()
            for i in range(3):
                with dpg.table_row():
                    dpg.add_selectable(label=f"row {i}", span_columns=True,
                                       tag=f"sel_{i}")
    dpg.set_value("sel_1", True)
    assert dpg.get_value("sel_1") is True
