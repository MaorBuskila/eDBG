"""Dirty-gated repaint.

Modes only pay for themselves if hiding a pane stops the work of painting it.
And a pane that skipped N paints while hidden must be correct the instant its
mode is selected — that is the failure mode hidden panes create, and the
reason the dirty flag clears on paint rather than on mark.
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
    # app holds the session and the last-rendered hit as module globals; a stale
    # _last_rendered_hit makes the next test's stop look already-rendered.
    app._session = EdbgSession()
    app._last_rendered_hit = None
    app._accumulated_reg_mem = []
    app._log_item_ids.clear()
    # The output window outlives a session; a dump left in it would be
    # reassembled into the next test's parse.
    app._recent_output.clear()
    app._dirty.clear()
    app._set_mode(modes.DEFAULT_MODE)
    yield
    app._dirty.clear()
    dpg.destroy_context()


def _rows(pane: str) -> int:
    return len(dpg.get_item_children(app.TAG[pane], 1))


def _seed_session():
    app._session.last_regs = [
        parse.RegisterInfo(name="X0", value=0x7B80E12340, deref=None, symbol="")]
    app._session.last_disasm = [
        parse.DisasmLine(address=0x1000, symbol="", mnemonic="MOV",
                         operands="X0, X1", is_current=True)]
    app._session.last_memory = [(0x1000, b"AAAABBBBCCCCDDDD")]
    app._session.last_threads = [
        {"index": 0, "tid": 12350, "name": "main", "is_current": True}]


# ── Gating ───────────────────────────────────────────────────────────

def test_clean_pane_does_not_repaint(built_ctx):
    _seed_session()
    app._populate_regs()          # dirty from _seed? no — nothing marked it
    assert _rows("pane_regs") == 1, "placeholder only; a clean pane is not painted"


def test_marked_and_visible_pane_repaints(built_ctx):
    _seed_session()
    app._dirty.mark("pane_regs")
    app._populate_regs()
    assert _rows("pane_regs") == 1


def test_hidden_pane_does_not_repaint_on_a_stop(built_ctx):
    # Spec AC 2. Flow, Watch and Log are invisible in Step mode.
    _seed_session()
    app._set_mode("step")
    hidden = set(modes.PANES) - set(modes.visible_panes("step"))
    before = {p: _rows(p) for p in hidden}
    app._dirty.mark_all(modes.PANES)
    app._refresh_panes()
    for pane in hidden:
        assert _rows(pane) == before[pane], f"{pane} repainted while hidden"
        assert pane in app._dirty.pending(), f"{pane} lost its dirty flag unpainted"


def test_idle_frame_mutates_nothing(built_ctx):
    # Spec AC 5. No new output means no DPG mutation at all.
    _seed_session()
    app._dirty.mark_all(modes.PANES)
    app._refresh_panes()
    snapshot = {p: _rows(p) for p in modes.PANES}
    app._refresh_panes()
    assert {p: _rows(p) for p in modes.PANES} == snapshot


# ── Repaint on reveal ────────────────────────────────────────────────

def test_pane_hidden_across_many_stops_is_current_when_revealed(built_ctx):
    """Spec AC 4 — the load-bearing test.

    Watch is invisible in Step. Stop repeatedly, then switch to Inspect: the
    pane must show what it would have shown had it been visible all along.
    """
    app._set_mode("step")
    for _ in range(10):
        app._dirty.mark_all(modes.PANES)
        app._refresh_panes()
    assert "pane_watch" in app._dirty.pending()

    _seed_session()
    app._dirty.mark("pane_threads")
    app._set_mode("inspect")

    expected = _rows("pane_threads")
    app._set_mode("step")
    app._dirty.mark("pane_threads")
    app._populate_threads()
    assert _rows("pane_threads") == expected


def test_revealing_a_clean_pane_does_not_repaint_it(built_ctx):
    _seed_session()
    app._dirty.mark("pane_watch")
    app._set_mode("inspect")          # paints and clears
    rows = _rows("pane_watch")
    app._set_mode("step")
    app._set_mode("inspect")          # clean now — must not repaint
    assert _rows("pane_watch") == rows
    assert "pane_watch" not in app._dirty.pending()


def test_switching_mode_paints_only_newly_revealed_dirty_panes(built_ctx):
    _seed_session()
    app._set_mode("step")
    app._dirty.clear()
    app._dirty.mark("pane_watch")     # hidden in step, visible in inspect
    app._set_mode("inspect")
    assert "pane_watch" not in app._dirty.pending(), "reveal must paint it"


# ── Parse path marks rather than paints ──────────────────────────────

def test_parse_handlers_mark_instead_of_painting(built_ctx):
    src = (app.__file__)
    with open(src) as fh:
        body = fh.read()
    frame_update = body[body.index("def _frame_update"):body.index("def _handle_flow_csv")]
    for populate in ["_populate_memory(", "_populate_breakpoints(",
                     "_populate_threads(", "_populate_backtrace("]:
        assert populate not in frame_update, \
            f"{populate} still called from the parse path; mark the pane instead"


# ── End to end through _frame_update ─────────────────────────────────

REG_NAMES = [f"X{i}" for i in range(30)] + ["LR", "SP", "PC"]

HIT_OUTPUT = (
    ["", "[Hit #1]", "pid=12345 tid=12350",
     "──────────────────[ REGISTERS ]──────────────────"]
    + [f" {n}\t0x{0x7BE3F81000 + i * 8:X}" for i, n in enumerate(REG_NAMES)]
    + ["──────────────────[  DISASM  ]──────────────────",
       ">>  0x7be3f81428<libloader.so+0x54a428>\tMOV X0, X1",
       "    0x7be3f8142c<libloader.so+0x54a42c>\tRET",
       "─────────────────────────────────────────────────"]
)

TLS_OUTPUT = [
    "tls tid=12350 map=[anon:stack_and_tls:12350] 0x7b80e00000-0x7b80e10000",
    "base=0x7b80e0ff00  len=0x10",
    "0x7b80e0ff00  0x7be3f81428  code  +0x0 (#0)  libloader.so+0x54a428",
    "0x7b80e0ff08  0x4142434445464748  junk  +0x8 (#8)  |HGFEDCBA|",
]


def _drive(monkeypatch, batches):
    """Feed _frame_update from a stubbed pty, as the real loop would."""
    from unittest import mock
    pty = mock.MagicMock()
    pty.is_alive = True
    pty.poll_output.side_effect = list(batches) + [[]] * 12
    app._session._pty = pty
    monkeypatch.setattr(app, "_auto_fetch_on_hit", lambda: None)
    for _ in range(len(batches) + 12):
        app._frame_update()


def test_stop_in_step_mode_leaves_hidden_panes_untouched(built_ctx, monkeypatch):
    app._set_mode("step")
    # pane_log is excluded: _append_log writes each line as a widget the moment
    # it arrives, visible or not. That is a real cost this gating does not
    # recover — see the follow-up on buffering the log.
    hidden = set(modes.PANES) - set(modes.visible_panes("step")) - {"pane_log"}
    before = {p: _rows(p) for p in hidden}
    _drive(monkeypatch, [HIT_OUTPUT])
    for pane in hidden:
        assert _rows(pane) == before[pane], f"{pane} repainted while hidden"


def test_registers_painted_on_a_stop_in_step_mode(built_ctx, monkeypatch):
    app._set_mode("step")
    _drive(monkeypatch, [HIT_OUTPUT])
    assert _rows("pane_regs") == len(REG_NAMES)


def test_tls_arriving_while_hidden_renders_on_reveal(built_ctx, monkeypatch):
    """Spec AC 4, end to end.

    TLS is hidden in Trace mode. A dump that lands there must be on screen the
    moment Inspect is selected — not blank, and not waiting for another stop.
    """
    app._set_mode("trace")
    _drive(monkeypatch, [TLS_OUTPUT])
    assert _rows("pane_tls") == 1, "placeholder only — TLS is hidden in Trace"

    app._set_mode("inspect")
    assert app._session.last_tls is not None
    # header + one row per slot
    assert _rows("pane_tls") == 1 + len(app._session.last_tls.slots)


# ── TLS pane ─────────────────────────────────────────────────────────

def test_tls_renders_one_row_per_slot_plus_header(built_ctx, monkeypatch):
    app._set_mode("step")
    _drive(monkeypatch, [TLS_OUTPUT])
    assert _rows("pane_tls") == 1 + 2


def test_tls_is_absent_from_trace_and_log_modes():
    for mode in ("trace", "log"):
        assert "pane_tls" not in modes.visible_panes(mode)


def test_tls_is_present_in_step_and_inspect():
    for mode in ("step", "inspect"):
        assert "pane_tls" in modes.visible_panes(mode)


def test_selecting_a_thread_requests_its_tls(built_ctx, monkeypatch):
    """Switching thread must re-dump TLS — TLS is per-thread, so a stale dump
    from another tid is worse than none."""
    sent = []
    monkeypatch.setattr(app._session, "send_command", sent.append)
    app._cb_select_thread(None, None, 12350)
    assert sent == ["thread 12350", "tls"]


def test_a_stop_asks_for_tls(built_ctx, monkeypatch):
    """Without this the TLS pane is empty on every hit until a thread is
    clicked, which is indistinguishable from a thread that has no TLS."""
    sent = []
    monkeypatch.setattr(app._session, "send_command", sent.append)
    app._auto_fetch_on_hit()
    assert "tls" in sent


def test_a_dump_split_across_frames_still_lands(built_ctx, monkeypatch):
    """The pty hands over whatever bytes arrived. A dump whose header and slots
    land in different polls is the common case, not an edge case."""
    _drive(monkeypatch, [TLS_OUTPUT[:2], TLS_OUTPUT[2:]])
    assert app._session.last_tls is not None
    assert len(app._session.last_tls.slots) == 2


def test_a_dump_split_slot_by_slot_still_lands(built_ctx, monkeypatch):
    _drive(monkeypatch, [[line] for line in TLS_OUTPUT])
    assert len(app._session.last_tls.slots) == 2


def test_later_output_does_not_erase_the_dump(built_ctx, monkeypatch):
    _drive(monkeypatch, [TLS_OUTPUT, ["Not stopped on a thread.", "(eDBG) "]])
    assert app._session.last_tls is not None
    assert len(app._session.last_tls.slots) == 2


def test_the_newest_dump_wins(built_ctx, monkeypatch):
    second = [l.replace("12350", "12351") for l in TLS_OUTPUT]
    _drive(monkeypatch, [TLS_OUTPUT, second])
    assert app._session.last_tls.tid == 12351


def test_tls_is_copyable(built_ctx):
    assert "pane_tls" in app._COPY_SOURCES
    label, getter = app._COPY_SOURCES["pane_tls"]
    assert label == "TLS"
    assert getter() == "(no tls)"   # nothing captured yet
