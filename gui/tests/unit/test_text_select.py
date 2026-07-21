"""Getting text out of a pane.

``dpg.add_text`` is a draw-only label: an operator cannot select an RVA off
the disassembly and paste it into the flow box, which is the single most
common thing to want mid-session. Two answers, both tested here — a per-pane
text mode that swaps the coloured rows for a selectable readonly buffer, and a
one-click path that puts the address where it was going anyway.
"""

from __future__ import annotations

import pytest

import dearpygui.dearpygui as dpg
from gui import modes, parse
from gui.session import EdbgSession
import gui.app as app


@pytest.fixture()
def built_ctx():
    dpg.create_context()
    dpg.configure_app(docking=False)
    app._build_layout()
    app._session = EdbgSession()
    app._text_mode.clear()
    app._dirty.clear()
    app._set_mode(modes.DEFAULT_MODE)
    yield
    app._text_mode.clear()
    app._dirty.clear()
    app._active_mode = modes.DEFAULT_MODE
    dpg.destroy_context()


def _children(pane: str) -> list:
    return dpg.get_item_children(app.TAG[pane], 1)


def _seed_disasm():
    app._session.last_disasm = [
        parse.DisasmLine(address=0x7AB60E0078, symbol="libloader.so+0x1fb078",
                         mnemonic="B", operands="0x7ab60e0060",
                         is_current=True),
        parse.DisasmLine(address=0x7AB60E007C, symbol="",
                         mnemonic="LDR", operands="X8, [X19,#104]",
                         is_current=False),
    ]


# ── Task 8: per-pane text mode ───────────────────────────────────────

def test_every_copyable_pane_has_a_text_toggle(built_ctx):
    for pane in app._COPY_SOURCES:
        assert dpg.does_item_exist(app.text_toggle_tag(pane)), \
            f"{pane} cannot be switched to selectable text"


def test_toggling_enters_and_leaves_text_mode(built_ctx):
    app._cb_toggle_text(None, None, "pane_disasm")
    assert "pane_disasm" in app._text_mode
    app._cb_toggle_text(None, None, "pane_disasm")
    assert "pane_disasm" not in app._text_mode


def test_text_mode_renders_one_selectable_buffer(built_ctx):
    _seed_disasm()
    app._cb_toggle_text(None, None, "pane_disasm")
    kids = _children("pane_disasm")
    assert len(kids) == 1
    assert dpg.get_item_type(kids[0]).endswith("mvInputText")


def test_the_buffer_holds_the_pane_text(built_ctx):
    _seed_disasm()
    app._cb_toggle_text(None, None, "pane_disasm")
    body = dpg.get_value(_children("pane_disasm")[0])
    assert "0x1fb078" in body
    assert "LDR" in body


def test_the_buffer_is_readonly(built_ctx):
    # Editing it would desynchronise the pane from the session it renders.
    _seed_disasm()
    app._cb_toggle_text(None, None, "pane_disasm")
    assert dpg.get_item_configuration(_children("pane_disasm")[0])["readonly"]


def test_leaving_text_mode_restores_the_coloured_rows(built_ctx):
    _seed_disasm()
    app._cb_toggle_text(None, None, "pane_disasm")
    app._cb_toggle_text(None, None, "pane_disasm")
    assert len(_children("pane_disasm")) == 2


def test_text_mode_survives_a_repaint(built_ctx):
    _seed_disasm()
    app._cb_toggle_text(None, None, "pane_disasm")
    app._dirty.mark("pane_disasm")
    app._populate_disasm()
    assert len(_children("pane_disasm")) == 1


def test_text_mode_is_per_pane(built_ctx):
    _seed_disasm()
    app._session.last_regs = [
        parse.RegisterInfo(name="X0", value=0x1234, deref=None, symbol="")]
    app._cb_toggle_text(None, None, "pane_disasm")
    app._dirty.mark("pane_regs")
    app._populate_regs()
    assert len(_children("pane_regs")) == 1        # one register row
    assert dpg.get_item_type(_children("pane_regs")[0]).endswith("mvGroup")


def test_a_hidden_pane_in_text_mode_still_waits_for_its_reveal(built_ctx):
    _seed_disasm()
    app._set_mode("log")                # disasm hidden
    app._cb_toggle_text(None, None, "pane_disasm")
    assert "pane_disasm" in app._dirty.pending()
    app._set_mode("step")
    assert "pane_disasm" not in app._dirty.pending()


def test_flow_panes_are_copyable(built_ctx):
    for pane in ("pane_flow", "pane_flow_history", "pane_flow_regs"):
        assert pane in app._COPY_SOURCES
        label, getter = app._COPY_SOURCES[pane]
        assert getter() == "(no flow trace)" or getter().startswith("(no ")


# ── Task 9: one click to reuse an address ────────────────────────────

def test_clicking_an_address_fills_the_flow_box(built_ctx):
    _seed_disasm()
    app._dirty.mark("pane_disasm")
    app._populate_disasm()
    app._cb_use_address(None, None, "0x1fb078")
    assert dpg.get_value(app.TAG["input_flow_addr"]) == "0x1fb078"


def test_clicking_an_address_copies_it(built_ctx, monkeypatch):
    copied = []
    monkeypatch.setattr(dpg, "set_clipboard_text", copied.append)
    app._cb_use_address(None, None, "0x1fb078")
    assert copied == ["0x1fb078"]


def test_the_rva_is_preferred_over_the_absolute_address(built_ctx):
    # `flow` takes an RVA (cli/repl.go:1047), so the library offset is the
    # useful half of `0x7ab60e0078<libloader.so+0x1fb078>`.
    _seed_disasm()
    assert app._disasm_address(app._session.last_disasm[0]) == "0x1fb078"


def test_a_row_without_a_symbol_falls_back_to_its_address(built_ctx):
    _seed_disasm()
    assert app._disasm_address(app._session.last_disasm[1]) == "0x7ab60e007c"


def test_each_disasm_row_offers_its_address(built_ctx):
    _seed_disasm()
    app._dirty.mark("pane_disasm")
    app._populate_disasm()
    for row in _children("pane_disasm"):
        kinds = [dpg.get_item_type(i) for i in dpg.get_item_children(row, 1)]
        assert any(k.endswith("mvSelectable") for k in kinds), \
            "the address must be clickable, not a draw-only label"
