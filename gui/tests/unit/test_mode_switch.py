"""Mode switching — show/hide and weights only.

A mode switch that rebuilt or re-parented panes would cost what the old
docking layout cost and lose scroll position on every keystroke. These tests
pin it to configuration writes.
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
    app._set_mode(modes.DEFAULT_MODE)
    yield
    app._set_mode(modes.DEFAULT_MODE)
    dpg.destroy_context()


def _shown() -> set[str]:
    return {p for p in modes.PANES
            if dpg.get_item_configuration(app.section_tag(p))["show"]}


@pytest.mark.parametrize("mode", list(modes.MODES))
def test_mode_shows_exactly_its_panes(built_ctx, mode):
    app._set_mode(mode)
    assert _shown() == set(modes.visible_panes(mode, app._hidden_panes(mode)))


# ── v4: the control row belongs to the mode ──────────────────────────

def _control_rows_shown() -> set[str]:
    return {row for row in modes.CONTROLS
            if dpg.get_item_configuration(app.control_row_tag(row))["show"]}


@pytest.mark.parametrize("mode", list(modes.MODES))
def test_exactly_one_control_row_is_up(built_ctx, mode):
    app._set_mode(mode)
    assert _control_rows_shown() == {modes.MODES[mode].controls}


def test_trace_offers_no_button_that_acts_on_a_live_process(built_ctx):
    app._set_mode("trace")
    for tag in ("btn_continue", "btn_interrupt", "btn_step", "btn_next",
                "btn_finish", "btn_until"):
        assert dpg.get_item_configuration(
            app.control_row_tag("run"))["show"] is False, \
            f"{tag} is reachable while reading a finished run"


# ── v4: toggles compose with the mode ────────────────────────────────

def test_a_toggled_off_pane_leaves_the_screen(built_ctx):
    app._set_mode("trace")
    app._cb_pane_toggle(None, False, "pane_flow_tls")
    assert "pane_flow_tls" not in _shown()
    app._cb_pane_toggle(None, True, "pane_flow_tls")
    assert "pane_flow_tls" in _shown()


def test_a_toggled_off_pane_gives_its_pixels_away(built_ctx):
    app._set_mode("trace")
    before = app._pane_heights(900, "trace")["pane_flow_mem"]
    after = app._pane_heights(900, "trace", {"pane_flow_tls"})["pane_flow_mem"]
    assert after > before


def test_revealing_a_pane_repaints_it_with_the_current_selection(built_ctx):
    # The v3 reveal contract, applied to toggles: a pane hidden across N
    # selections must not come back showing the selection it left on.
    app._set_mode("trace")
    app._cb_pane_toggle(None, False, "pane_flow_tls")
    app._dirty.clear()
    app._cb_pane_toggle(None, True, "pane_flow_tls")
    assert "pane_flow_tls" not in app._dirty.pending(), \
        "the reveal must paint, not just mark"


def test_a_toggled_off_pane_does_not_repaint(built_ctx):
    # A toggle that only hides the pixels still pays for the pane on every
    # selection, which is the cost the toggle exists to remove.
    app._set_mode("trace")
    app._cb_pane_toggle(None, False, "pane_flow_tls")
    app._dirty.mark("pane_flow_tls")
    app._populate_flow_tls()
    assert "pane_flow_tls" in app._dirty.pending(), \
        "a hidden pane painted, and lost the flag its reveal needs"
    app._cb_pane_toggle(None, True, "pane_flow_tls")


def test_only_an_optional_pane_can_be_turned_off(built_ctx):
    app._set_mode("trace")
    app._cb_pane_toggle(None, False, "pane_flow_history")
    assert "pane_flow_history" in _shown()


def test_a_toggle_in_one_mode_does_not_hide_a_pane_in_another(built_ctx):
    app._set_mode("trace")
    app._cb_pane_toggle(None, False, "pane_flow_regs")
    app._set_mode("step")
    assert _shown() == set(modes.visible_panes("step"))


def test_every_optional_pane_has_a_checkbox(built_ctx):
    for pane in modes.MODES["trace"].optional:
        assert dpg.does_item_exist(app.pane_toggle_tag(pane)), \
            f"{pane} can be hidden but not un-hidden"


@pytest.mark.parametrize("mode", list(modes.MODES))
def test_mode_applies_registry_weights(built_ctx, mode):
    app._set_mode(mode)
    got = [dpg.get_item_configuration(app.column_tag(c))["init_width_or_weight"]
           for c in modes.COLUMNS]
    assert got == pytest.approx(list(modes.col_weights(mode)))


def test_switching_never_creates_or_destroys_a_pane(built_ctx):
    # Spec AC 1: one instance, forever.
    before = {p: dpg.get_item_info(app.TAG[p]) for p in modes.PANES}
    for mode in modes.MODES:
        app._set_mode(mode)
    for pane in modes.PANES:
        assert dpg.does_item_exist(app.TAG[pane])
        assert dpg.get_item_info(app.TAG[pane])["parent"] == before[pane]["parent"], \
            f"{pane} was re-parented"


def test_switching_does_not_touch_pane_contents(built_ctx):
    # Spec AC 3: a switch with no intervening stop is O(1) in pane content.
    dpg.add_text("sentinel", parent=app.TAG["pane_regs"])
    kids = dpg.get_item_children(app.TAG["pane_regs"], 1)
    app._set_mode("log")
    app._set_mode("step")
    assert dpg.get_item_children(app.TAG["pane_regs"], 1) == kids


def test_round_trip_restores_the_starting_mode(built_ctx):
    start = _shown()
    app._set_mode("inspect")
    app._set_mode("log")
    app._set_mode(modes.DEFAULT_MODE)
    assert _shown() == start


def test_log_mode_collapses_both_side_columns(built_ctx):
    app._set_mode("log")
    left, _, right = [
        dpg.get_item_configuration(app.column_tag(c))["init_width_or_weight"]
        for c in modes.COLUMNS]
    # Collapsed, not hidden — hiding a column re-flows the survivors.
    assert left == pytest.approx(modes.COLLAPSED)
    assert right == pytest.approx(modes.COLLAPSED)
    for col in modes.COLUMNS:
        assert dpg.get_item_configuration(app.column_tag(col))["show"] is True


def test_mode_button_callback_switches_mode(built_ctx):
    app._cb_mode(app.mode_button_tag("inspect"), None, "inspect")
    assert app.current_mode() == "inspect"


def test_function_keys_map_to_modes():
    # F5/F10/F11 already drive continue/next/step; the mode keys must not
    # collide with them.
    run_keys = {"F5", "F10", "F11"}
    assert not (set(modes.MODE_KEYS.values()) & run_keys)


def test_every_mode_is_reachable_by_key(built_ctx):
    for name, key in modes.MODE_KEYS.items():
        app._set_mode_by_key(key)
        assert app.current_mode() == name


def test_mode_keys_exist_in_dpg(built_ctx):
    # getattr(dpg, "mvKey_F1") is how the handlers are registered; a renamed
    # constant would fail at startup rather than in a test.
    for key in modes.MODE_KEYS.values():
        assert hasattr(dpg, f"mvKey_{key}")


def test_keybindings_register_without_error(built_ctx):
    app._setup_keybindings()


@pytest.mark.parametrize("mode", list(modes.MODES))
def test_active_mode_is_marked_in_the_button_label(built_ctx, mode):
    app._set_mode(mode)
    labels = {m: dpg.get_item_configuration(app.mode_button_tag(m))["label"]
              for m in modes.MODES}
    active = [m for m, l in labels.items() if l.startswith("▸")]
    assert active == [mode]


def test_mode_marking_survives_a_context_swap(built_ctx):
    # A theme id from a destroyed context is invalid; a label is not. This is
    # the regression that made every mode-switch test error out at setup.
    app._set_mode("inspect")
    assert dpg.get_item_configuration(
        app.mode_button_tag("inspect"))["label"].startswith("▸")
