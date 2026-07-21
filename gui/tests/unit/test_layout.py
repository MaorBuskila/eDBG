"""Smoke tests for the v3 mode shell.

The shell is one primary window, three stretch columns, and one pane pool.
Nothing floats, nothing docks, and no pane is built twice — those three
properties are what "the layout cannot be broken" reduces to.
"""

from __future__ import annotations

import pytest

import dearpygui.dearpygui as dpg
from gui import modes
import gui.app as app


@pytest.fixture()
def built_ctx():
    dpg.create_context()
    dpg.configure_app(docking=False)
    app._build_layout()
    yield
    dpg.destroy_context()


def _ancestors(tag) -> list:
    out, node = [], tag
    while node:
        node = dpg.get_item_parent(node)
        if node:
            out.append(node)
    return out


def test_layout_builds_into_one_root_window(built_ctx):
    assert dpg.does_item_exist(app.TAG["root"])
    assert dpg.does_item_exist(app.TAG["shell"])


def test_all_tag_widgets_exist(built_ctx):
    missing = [name for name, tag in app.TAG.items()
               if not dpg.does_item_exist(tag)]
    assert not missing, f"TAG widgets not created: {missing}"


def test_tags_are_unique():
    values = list(app.TAG.values())
    assert len(values) == len(set(values)), "duplicate TAG values"


def test_no_floating_panel_windows_remain(built_ctx):
    # The seven dockable windows are gone; a stray one would float again.
    for old in ["win_control", "win_regs", "win_code", "win_mem",
                "win_bp", "win_aux", "win_log"]:
        assert not dpg.does_item_exist(old), f"{old} survived the shell rewrite"


def test_dock_helpers_are_deleted():
    for gone in ["_DOCK_FRAC", "_compute_dock_layout", "_apply_dock_layout"]:
        assert not hasattr(app, gone), f"{gone} still present"


def test_every_pane_in_the_registry_is_built_exactly_once(built_ctx):
    # Spec AC 1. A pane built twice is a pane that can disagree with itself.
    for pane in modes.PANES:
        assert dpg.does_item_exist(pane), f"{pane} not built"
        assert app.TAG[pane] == pane


def test_every_pane_has_a_section_wrapper(built_ctx):
    # Show/hide targets the wrapper, not the pane, so a hidden Memory takes its
    # address bar with it instead of leaving orphaned controls on screen.
    for pane in modes.PANES:
        assert dpg.does_item_exist(app.section_tag(pane))


def test_three_columns_exist_with_registry_weights(built_ctx):
    for col, weight in zip(modes.COLUMNS, modes.col_weights(modes.DEFAULT_MODE)):
        tag = app.column_tag(col)
        assert dpg.does_item_exist(tag)
        cfg = dpg.get_item_configuration(tag)
        assert cfg["init_width_or_weight"] == pytest.approx(weight)


def test_panes_live_in_the_column_the_registry_assigns(built_ctx):
    for pane in modes.PANES:
        col = next(c for c in modes.COLUMNS
                   for m in modes.MODES.values() if pane in m.panes[c])
        assert app.cell_tag(col) in _ancestors(app.section_tag(pane)), \
            f"{pane} is not in column {col}"


def test_mode_bar_has_a_button_per_mode(built_ctx):
    for name in modes.MODES:
        assert dpg.does_item_exist(app.mode_button_tag(name))


def test_default_mode_is_step(built_ctx):
    assert app.current_mode() == modes.DEFAULT_MODE


def test_hud_and_command_bar_are_outside_the_columns(built_ctx):
    # Persistent chrome must survive every mode switch, including Log mode,
    # which collapses both side columns.
    for tag in (app.TAG["status_text"], app.TAG["input_cmd"]):
        assert app.TAG["shell"] not in _ancestors(tag)


# ── Pane height arithmetic (pure, no DPG) ────────────────────────────

@pytest.mark.parametrize("mode", list(modes.MODES))
def test_panes_plus_chrome_fit_their_column(mode):
    # Unaccounted chrome is what pushes a column's bottom pane off-screen.
    avail = 890
    h = app._pane_heights(avail, mode)
    for col in modes.COLUMNS:
        visible = modes.MODES[mode].panes[col]
        if not visible:
            continue
        used = sum(h[p] + app._PANE_CHROME[p] for p in visible)
        assert used <= avail, f"{mode}.{col} overflows by {used - avail}px"


@pytest.mark.parametrize("mode", list(modes.MODES))
def test_only_visible_panes_get_heights(mode):
    assert set(app._pane_heights(890, mode)) == set(modes.visible_panes(mode))


def test_heights_scale_with_viewport():
    small = app._pane_heights(600, "step")
    big = app._pane_heights(1200, "step")
    assert big["pane_regs"] > small["pane_regs"]


def test_tiny_viewport_does_not_produce_unusable_panes():
    for h in app._pane_heights(120, "step").values():
        assert h >= 40
