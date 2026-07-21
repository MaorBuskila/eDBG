"""Dear PyGui application — layout + callbacks.

Patterned after MemDumper memscan GUI: TAG dict for widget IDs, small
_safe wrappers around callbacks, monospace for hex, session object as
single source of truth.
"""

from __future__ import annotations

import os
import functools
import signal
import time
from collections import deque
import dearpygui.dearpygui as dpg
from gui.session import EdbgSession, State
from gui import flowtrace
from gui import parse
from gui import textdump
from gui import theme
from gui import modes
from gui.dirty import DirtySet
from gui.theme import (
    CLR_GREEN, CLR_RED, CLR_YELLOW, CLR_CYAN, CLR_WHITE, CLR_GRAY, CLR_BLUE,
)

# ── Widget-ID registry (TAG dict pattern from memscan) ──────────────
TAG = {
    # connection panel
    "input_package":      "inp_package",
    "input_lib":          "inp_lib",
    "input_breaks":       "inp_breaks",
    "btn_connect":        "btn_connect",
    "btn_disconnect":     "btn_disconnect",
    "status_text":        "txt_status",
    # run controls
    "btn_continue":       "btn_continue",
    "btn_interrupt":      "btn_interrupt",
    "btn_step":           "btn_step",
    "btn_next":           "btn_next",
    "btn_finish":         "btn_finish",
    "btn_until":          "btn_until",
    # panes — tag equals the gui.modes registry name, one instance each
    "pane_regs":          "pane_regs",
    "pane_disasm":        "pane_disasm",
    "pane_memory":        "pane_memory",
    "pane_backtrace":     "pane_backtrace",
    "pane_log":           "pane_log",
    "pane_breakpoints":   "pane_breakpoints",
    "pane_flow":          "pane_flow",
    "pane_flow_history":  "pane_flow_history",
    "pane_flow_regs":     "pane_flow_regs",
    "pane_flow_mem":      "pane_flow_mem",
    "pane_flow_tls":      "pane_flow_tls",
    "pane_tls":           "pane_tls",
    "pane_watch":         "pane_watch",
    # shell
    "root":               "root",
    "shell":              "shell",
    # examine
    "input_examine_addr": "inp_exam_addr",
    "input_examine_size": "inp_exam_size",
    "btn_examine":        "btn_examine",
    # command bar
    "input_cmd":          "inp_cmd",
    "btn_send":           "btn_send",
    # memory write
    "input_write_addr":   "inp_write_addr",
    "input_write_hex":    "inp_write_hex",
    "btn_write":          "btn_write",
    # display
    "input_display_name": "inp_disp_name",
    "input_display_addr": "inp_disp_addr",
    "input_display_len":  "inp_disp_len",
    "btn_display_add":    "btn_disp_add",
    # flow
    "input_flow_addr":    "inp_flow_addr",
    "chk_flow_over":      "chk_flow_over",
    "input_flow_max":     "inp_flow_max",
    "chk_flow_regs":      "chk_flow_regs",
    "input_flow_mem":     "inp_flow_mem",
    "chk_flow_quiet":     "chk_flow_quiet",
    "btn_flow":           "btn_flow",
    # bp table
    "input_bp_type":      "inp_bp_type",
    "input_bp_addr":      "inp_bp_addr",
    "btn_bp_add":         "btn_bp_add",
    # thread
    "pane_threads":       "pane_threads",
    # until popup
    "until_popup":        "until_popup",
    "input_until_addr":   "inp_until_addr",
}


def section_tag(pane: str) -> str:
    """Wrapper holding a pane's header, its controls, and the pane itself.

    Show/hide targets this, not the pane — hiding `pane_memory` alone would
    strand its address bar on screen.
    """
    return f"sec_{pane}"


def column_tag(col: str) -> str:
    return f"col_{col}"


def cell_tag(col: str) -> str:
    return f"cell_{col}"


def mode_button_tag(mode: str) -> str:
    return f"modebtn_{mode}"


def text_toggle_tag(pane: str) -> str:
    return f"txtmode_{pane}"


def control_row_tag(row: str) -> str:
    """The HUD row a mode asks for. Exactly one is ever up."""
    return f"controls_{row}"


def pane_toggle_tag(pane: str) -> str:
    return f"panetoggle_{pane}"

# ── Mono font tag ────────────────────────────────────────────────────
_MONO_FONT = "font_mono"

# ── Per-widget theme handles (populated by theme.setup_theme in main) ──
_theme_green_btn = None
_theme_red_btn = None
_theme_run_btn = None
_pane_theme = None          # tight spacing for colored child-window panes

# Theme + font setup now live in gui/theme.py (theme.setup_theme / setup_fonts).

# ── Global session ───────────────────────────────────────────────────
_session = EdbgSession()

# Track last hit number to know when panes need refresh
_last_rendered_hit: int | None = None
_log_line_count: int = 0

# ── Colored log tracking ────────────────────────────────────────────
_LOG_MAX_LINES = 500
_log_item_ids: deque = deque()

# Rolling window of recent ANSI-stripped output, for responses that span more
# polls than one. A full TLS dump is utils.TlsDumpLen/8 + 2 = 258 lines, so the
# window has to hold comfortably more than that for a split dump to reassemble.
_recent_output: deque = deque(maxlen=512)

# ── Accumulated register memory (per hit) ───────────────────────────
_accumulated_reg_mem: list[tuple[int, bytes]] = []


# =====================================================================
#  Safe callback wrappers
# =====================================================================

def _safe(fn):
    """Wrap a callback so exceptions show in the log instead of crashing."""
    @functools.wraps(fn)
    def wrapper(sender=None, app_data=None, user_data=None):
        try:
            fn(sender, app_data, user_data)
        except Exception as exc:
            _append_log(f"[GUI ERROR] {exc}")
    return wrapper


# =====================================================================
#  Helpers
# =====================================================================

def _append_log(text: str) -> None:
    """Append a colored line to the log pane (interprets ANSI escapes)."""
    parent = TAG["pane_log"]
    if not dpg.does_item_exist(parent):
        return

    segments = parse.parse_ansi_line(text)
    # Use a horizontal group so segments sit on one line
    grp = dpg.add_group(horizontal=True, parent=parent)
    for seg in segments:
        dpg.add_text(seg.text, color=seg.color, parent=grp)

    _log_item_ids.append(grp)
    while len(_log_item_ids) > _LOG_MAX_LINES:
        old_id = _log_item_ids.popleft()
        if dpg.does_item_exist(old_id):
            dpg.delete_item(old_id)

    # Auto-scroll to bottom
    try:
        dpg.set_y_scroll(parent, dpg.get_y_scroll_max(parent))
    except Exception:
        pass


def _set_status(text: str, color=CLR_WHITE) -> None:
    dpg.set_value(TAG["status_text"], text)
    dpg.configure_item(TAG["status_text"], color=color)
    # HUD state dot mirrors the status color
    dot = TAG["status_text"] + "_dot"
    if dpg.does_item_exist(dot):
        dpg.configure_item(dot, color=color)


def _update_run_controls() -> None:
    """Enable/disable run control buttons based on session state."""
    stopped = _session.state == State.STOPPED
    connected = _session.is_connected
    running = _session.state == State.RUNNING

    dpg.configure_item(TAG["btn_continue"],  enabled=stopped)
    dpg.configure_item(TAG["btn_step"],      enabled=stopped)
    dpg.configure_item(TAG["btn_next"],      enabled=stopped)
    dpg.configure_item(TAG["btn_finish"],    enabled=stopped)
    dpg.configure_item(TAG["btn_until"],     enabled=stopped)
    dpg.configure_item(TAG["btn_interrupt"], enabled=running or stopped)
    dpg.configure_item(TAG["btn_examine"],   enabled=stopped)
    dpg.configure_item(TAG["btn_write"],     enabled=stopped)
    dpg.configure_item(TAG["btn_send"],      enabled=connected)
    dpg.configure_item(TAG["btn_connect"],   enabled=not connected)
    dpg.configure_item(TAG["btn_disconnect"], enabled=connected)
    dpg.configure_item(TAG["btn_bp_add"],    enabled=connected)
    dpg.configure_item(TAG["btn_flow"],      enabled=stopped)
    dpg.configure_item(TAG["btn_display_add"], enabled=stopped)


# =====================================================================
#  Pane population (colored child-window output)
# =====================================================================

_dirty = DirtySet()


def _pane_visible(pane: str) -> bool:
    return pane in modes.MODES[_active_mode].panes[modes.PANE_COLUMN[pane]]


def _take_paint(pane: str) -> bool:
    """Claim the right to repaint `pane`, if it changed and someone can see it.

    The flag is consumed only when the paint actually happens, so a pane that
    stays hidden across many stops is still dirty when its mode is selected.

    A pane in text mode is rendered here and the caller told to stop: the
    selectable buffer replaces the coloured rows wholesale, so every painter
    gets text mode from this one branch rather than repeating it.
    """
    if not _pane_visible(pane):
        return False
    if not _dirty.take(pane):
        return False
    if pane in _text_mode:
        _paint_as_text(pane)
        return False
    return True


def _repaint_revealed() -> None:
    """Paint panes that became visible while carrying an unpainted change."""
    for pane in modes.visible_panes(_active_mode):
        if pane in _dirty.pending():
            _PANE_PAINTERS[pane]()


def _populate_regs() -> None:
    """Fill the registers pane with colored text."""
    if not _take_paint("pane_regs"):
        return
    tag = TAG["pane_regs"]
    dpg.delete_item(tag, children_only=True)
    _render_regs(tag, _session.last_regs)


def _render_regs(tag: str, regs: list) -> None:
    """One row per live register. Shared with trace mode's `--regs` fallback."""
    if not regs:
        dpg.add_text("(no registers)", parent=tag, color=theme.TEXT_DIM)
        return
    for r in regs:
        with dpg.group(horizontal=True, parent=tag):
            # Register name — red for pointer regs, cyan otherwise
            is_ptr = bool(r.symbol or r.deref)
            name_clr = theme.ACCENT_RED if is_ptr else theme.ACCENT_CYAN
            dpg.add_text(f" {r.name:<4}", color=name_clr)
            # Hex value
            dpg.add_text(f" 0x{r.value:X}", color=theme.ACCENT_CYAN)
            # Symbol
            if r.symbol:
                dpg.add_text(f"  {r.symbol}", color=theme.ACCENT_GREEN)
            # Deref
            if r.deref:
                dpg.add_text(f"  ◂— {r.deref}", color=theme.ACCENT_AMBER)


def _populate_disasm() -> None:
    """Fill the disasm pane with colored text."""
    if not _take_paint("pane_disasm"):
        return
    tag = TAG["pane_disasm"]
    dpg.delete_item(tag, children_only=True)
    if not _session.last_disasm:
        dpg.add_text("(no disassembly)", parent=tag, color=theme.TEXT_DIM)
        return
    for d in _session.last_disasm:
        with dpg.group(horizontal=True, parent=tag):
            # Current-instruction marker
            if d.is_current:
                dpg.add_text(">>", color=theme.ACCENT_GREEN)
            else:
                dpg.add_text("  ", color=theme.TEXT_DIM)
            # Address and symbol — one click sends the RVA to the flow box,
            # which is where a hand-copied address was headed anyway.
            label = f" 0x{d.address:x}"
            if d.symbol:
                label += f"<{d.symbol}>"
            dpg.add_selectable(label=label, user_data=_disasm_address(d),
                               callback=_cb_use_address, width=0)
            # Spacing
            dpg.add_text("\t", color=theme.TEXT)
            # Mnemonic — amber for most, red for branches
            mnem_clr = theme.ACCENT_RED if d.mnemonic.upper().startswith("B") else theme.ACCENT_AMBER
            dpg.add_text(d.mnemonic, color=mnem_clr)
            # Operands
            if d.operands:
                dpg.add_text(f" {d.operands}", color=theme.ACCENT_CYAN)


def _populate_backtrace() -> None:
    """Fill the backtrace pane with colored text."""
    if not _take_paint("pane_backtrace"):
        return
    tag = TAG["pane_backtrace"]
    dpg.delete_item(tag, children_only=True)
    if not _session.last_backtrace:
        dpg.add_text("(no backtrace)", parent=tag, color=theme.TEXT_DIM)
        return
    for f in _session.last_backtrace:
        with dpg.group(horizontal=True, parent=tag):
            dpg.add_text(f"#{f.index:<3}", color=theme.ACCENT_AMBER)
            dpg.add_text(f" 0x{f.address:016x}", color=theme.ACCENT_CYAN)
            dpg.add_text(" in ", color=theme.TEXT_DIM)
            dpg.add_text(f"{f.symbol}", color=theme.ACCENT_GREEN)


def _populate_memory(mem_lines: list[tuple[int, bytes]] | None = None) -> None:
    """Fill the memory pane with colored hex dump."""
    global _accumulated_reg_mem
    if not _take_paint("pane_memory"):
        return
    data = mem_lines if mem_lines is not None else _accumulated_reg_mem
    tag = TAG["pane_memory"]
    dpg.delete_item(tag, children_only=True)
    if not data:
        dpg.add_text("(no memory)", parent=tag, color=theme.TEXT_DIM)
        return
    for addr, raw in data:
        hex_str = raw.hex()
        hex_fmt = " ".join(hex_str[i:i + 2] for i in range(0, len(hex_str), 2))
        ascii_str = "".join(chr(b) if 0x20 <= b < 0x7f else "." for b in raw)
        with dpg.group(horizontal=True, parent=tag):
            dpg.add_text(f"0x{addr:08x}", color=theme.ACCENT_CYAN)
            dpg.add_text(f"  {hex_fmt:<48s}", color=theme.TEXT)
            dpg.add_text(f"  |{ascii_str}|", color=theme.ACCENT_GREEN)


def _populate_breakpoints() -> None:
    """Fill the breakpoints pane with colored text."""
    if not _take_paint("pane_breakpoints"):
        return
    tag = TAG["pane_breakpoints"]
    dpg.delete_item(tag, children_only=True)
    if not _session.last_breakpoints:
        dpg.add_text("(no breakpoints)", parent=tag, color=theme.TEXT_DIM)
        return
    for bp in _session.last_breakpoints:
        with dpg.group(horizontal=True, parent=tag):
            status = "[+]" if bp["enabled"] else "[-]"
            status_clr = theme.ACCENT_GREEN if bp["enabled"] else theme.ACCENT_RED
            dpg.add_text(status, color=status_clr)
            dpg.add_text(f" {bp['id']}: ", color=theme.ACCENT_AMBER)
            if bp["type"] == "hardware":
                dpg.add_text(f"0x{bp['offset']:x} Hardware", color=theme.ACCENT_CYAN)
            else:
                dpg.add_text(f"{bp['library']}+0x{bp['offset']:x}", color=theme.ACCENT_CYAN)


@_safe
def _cb_select_thread(sender, app_data, user_data):
    """Switch thread, then re-dump TLS.

    TLS is per-thread, so leaving the previous thread's dump on screen under a
    new tid is worse than showing nothing.
    """
    _session.send_command(f"thread {user_data}")
    _session.send_command("tls")


def _populate_threads() -> None:
    """Fill the threads pane with colored text."""
    if not _take_paint("pane_threads"):
        return
    tag = TAG["pane_threads"]
    dpg.delete_item(tag, children_only=True)
    if not _session.last_threads:
        dpg.add_text("(no threads)", parent=tag, color=theme.TEXT_DIM)
        return
    for t in _session.last_threads:
        with dpg.group(horizontal=True, parent=tag):
            is_cur = t["is_current"]
            marker = ">>" if is_cur else "  "
            dpg.add_text(marker, color=theme.ACCENT_GREEN if is_cur else theme.TEXT_DIM)
            dpg.add_text(f"[{t['index']}]", color=theme.ACCENT_AMBER)
            dpg.add_selectable(label=f" {t['tid']}: {t['name']}",
                               user_data=t["tid"], callback=_cb_select_thread)


# What each TLS slot class means at a glance: a code pointer is not the same
# kind of find as a junk qword, and colour is the fastest way to say so.
_TLS_CLASS_COLOR = {
    "code":   theme.ACCENT_RED,
    "string": theme.ACCENT_GREEN,
    "heap":   theme.ACCENT_AMBER,
    "mapped": theme.ACCENT_AMBER,
    "stack":  theme.ACCENT_CYAN,
    "junk":   theme.TEXT_DIM,
}


def _populate_tls() -> None:
    """Fill the TLS pane with class-coloured stack_and_tls slots."""
    if not _take_paint("pane_tls"):
        return
    tag = TAG["pane_tls"]
    dpg.delete_item(tag, children_only=True)
    dump = _session.last_tls
    if dump is None or not dump.slots:
        dpg.add_text("(no tls)", parent=tag, color=theme.TEXT_DIM)
        return
    with dpg.group(horizontal=True, parent=tag):
        dpg.add_text(f"tid {dump.tid}", color=theme.ACCENT_AMBER)
        dpg.add_text(f" base 0x{dump.base:x}", color=theme.TEXT_DIM)
    for s in dump.slots:
        with dpg.group(horizontal=True, parent=tag):
            dpg.add_text(f"+0x{s.offset:<4x}", color=theme.TEXT_DIM)
            dpg.add_text(f" 0x{s.value:016x}", color=theme.ACCENT_CYAN)
            dpg.add_text(f" {s.cls:<7}",
                         color=_TLS_CLASS_COLOR.get(s.cls, theme.TEXT))
            if s.annotation:
                dpg.add_text(f" {s.annotation}", color=theme.ACCENT_GREEN)


# =====================================================================
#  Clipboard  (dpg.add_text has no selection model — see gui/textdump.py)
# =====================================================================

# pane tag -> (menu label, callable returning the pane's text)
_COPY_SOURCES = {
    "pane_regs":        ("Registers",
                         lambda: textdump.regs_to_text(_session.last_regs)),
    "pane_disasm":      ("Disassembly",
                         lambda: textdump.disasm_to_text(_session.last_disasm)),
    "pane_backtrace":   ("Backtrace",
                         lambda: textdump.backtrace_to_text(
                             _session.last_backtrace)),
    "pane_memory":      ("Memory",
                         lambda: textdump.memory_to_text(_session.last_memory)),
    "pane_breakpoints": ("Breakpoints",
                         lambda: textdump.breakpoints_to_text(
                             _session.last_breakpoints)),
    "pane_threads":     ("Threads",
                         lambda: textdump.threads_to_text(
                             _session.last_threads)),
    "pane_tls":         ("TLS",
                         lambda: textdump.tls_to_text(_session.last_tls)),
    "pane_flow":        ("Flow trace",
                         lambda: flowtrace.flow_to_text(_flow_run())),
    "pane_flow_history": ("Flow history", lambda: _flow_history_to_text()),
    "pane_flow_regs":   ("Step registers", lambda: _flow_regs_to_text()),
    "pane_flow_mem":    ("Step memory", lambda: _flow_mem_to_text()),
    "pane_flow_tls":    ("Step TLS", lambda: _flow_tls_to_text()),
    "pane_log":         ("Log",
                         lambda: textdump.log_to_text(_session.transcript)),
}


_CTX_POPUP = "ctx_copy_popup"
_CTX_TITLE = "ctx_copy_title"
_CTX_COPY = "ctx_copy_pane"
_ctx_pane: str | None = None


def _copy(text: str, what: str) -> None:
    dpg.set_clipboard_text(text)
    _append_log(f"[GUI] copied {what} ({len(text)} chars)")


def _disasm_address(d) -> str:
    """The half of a disasm row worth reusing.

    `flow` and the breakpoint commands take a library-relative offset, so
    `0x7ab60e0078<libloader.so+0x1fb078>` is worth `0x1fb078` — the absolute
    address only helps when there is no symbol to relativise against.
    """
    if d.symbol and "+0x" in d.symbol:
        return "0x" + d.symbol.split("+0x", 1)[1].strip(">")
    return f"0x{d.address:x}"


@_safe
def _cb_use_address(sender, app_data, user_data):
    """Clipboard plus the flow box: the two places a clicked address goes."""
    addr = user_data
    _copy(addr, addr)
    dpg.set_value(TAG["input_flow_addr"], addr)


# ── Selectable text mode ─────────────────────────────────────────────
#
# A pane in text mode renders as one readonly multiline input instead of
# coloured rows. That widget is the only one in DPG with a selection model, so
# it is the only way to drag-select an RVA and press Ctrl+C.

_text_mode: set = set()


@_safe
def _cb_toggle_text(sender, app_data, user_data):
    pane = user_data
    if pane in _text_mode:
        _text_mode.discard(pane)
    else:
        _text_mode.add(pane)
    dpg.configure_item(text_toggle_tag(pane),
                       label="COLOR" if pane in _text_mode else "TEXT")
    _dirty.mark(pane)
    _PANE_PAINTERS[pane]()


def _paint_as_text(pane: str) -> None:
    tag = TAG[pane]
    dpg.delete_item(tag, children_only=True)
    _label, getter = _COPY_SOURCES[pane]
    dpg.add_input_text(parent=tag, multiline=True, readonly=True,
                       default_value=getter(), width=-1, height=-1)


@_safe
def _cb_copy_pane(sender, app_data, user_data):
    if _ctx_pane is None:
        return
    label, getter = _COPY_SOURCES[_ctx_pane]
    _copy(getter(), label)
    dpg.configure_item(_CTX_POPUP, show=False)


@_safe
def _cb_copy_all(sender, app_data, user_data):
    _copy(textdump.session_to_text(_session), "session")
    if dpg.does_item_exist(_CTX_POPUP):
        dpg.configure_item(_CTX_POPUP, show=False)


def _build_copy_popup() -> None:
    """One shared context popup.

    `dpg.popup()` cannot be used here: it binds an item handler registry to its
    parent, and `mvChildWindow` rejects `mvClickedHandler` — every output pane
    is a child window. A global right-click handler plus one popup window works
    for any item type.
    """
    with dpg.window(tag=_CTX_POPUP, popup=True, show=False,
                    no_title_bar=True, autosize=True):
        dpg.add_text("", tag=_CTX_TITLE, color=CLR_CYAN)
        dpg.add_separator()
        dpg.add_menu_item(label="Copy pane", tag=_CTX_COPY,
                          callback=_cb_copy_pane)
        dpg.add_menu_item(label="Copy everything", callback=_cb_copy_all)


def _on_right_click(sender, app_data, user_data=None):
    """Open the copy popup over whichever pane is under the cursor."""
    global _ctx_pane
    for pane_key, (label, _getter) in _COPY_SOURCES.items():
        tag = TAG[pane_key]
        if dpg.does_item_exist(tag) and dpg.is_item_hovered(tag):
            _ctx_pane = pane_key
            dpg.set_value(_CTX_TITLE, label.upper())
            dpg.configure_item(_CTX_COPY, label=f"Copy {label}")
            dpg.configure_item(_CTX_POPUP, show=True,
                               pos=dpg.get_mouse_pos(local=False))
            return


def _attach_copy_menus() -> None:
    """Wire right-click-to-copy. Never fatal — the Edit menu is the fallback."""
    try:
        _build_copy_popup()
        with dpg.handler_registry():
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Right,
                                        callback=_on_right_click)
    except Exception as exc:
        print(f"[GUI] right-click copy unavailable: {exc}")


# =====================================================================
#  Flow traces  (finished runs on disk, not live process state)
# =====================================================================

_FLOW_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

#: Rows rendered at once. Every row is a widget DPG lays out each frame, and a
#: 10k-step trace is unreadable anyway — so the pane shows a window around the
#: selected step instead of the whole run.
_FLOW_MAX_ROWS = 500

_FLOW_PANES = ("pane_flow", "pane_flow_history", "pane_flow_regs",
               "pane_flow_mem", "pane_flow_tls")

#: Panes showing the selected step. The history tree is not one of them — it
#: owns the selection rather than following it.
_FLOW_STEP_PANES = ("pane_flow", "pane_flow_regs", "pane_flow_mem",
                    "pane_flow_tls")

_flow_runs: list = []
_flow_selected: int | None = None
_flow_step: int = 0


#: What a pane says when the run simply never captured it. Naming the flag is
#: the difference between "this is empty" and "this looks broken".
_MISSING_FLAG = {
    "--regs": "(run captured without --regs)",
    "--mem":  "(run captured without --mem)",
    "--tls":  "(run captured without --tls)",
}


def _flow_run():
    """The selected run, or None."""
    if _flow_selected is None or not 0 <= _flow_selected < len(_flow_runs):
        return None
    return _flow_runs[_flow_selected]


def _flow_changed_regs() -> set:
    run = _flow_run()
    return run.changed_at(_flow_step) if run else set()


def _add_flow_run(run) -> None:
    """Put a finished run at the head of history and select it.

    A re-run of an RVA is a separate entry: comparing a trace against the one
    it repeats is the reason to keep history at all.
    """
    global _flow_selected, _flow_step
    if run is None:
        return
    _flow_runs.insert(0, run)
    _flow_selected = 0
    _flow_step = 0
    _dirty.mark_all(_FLOW_PANES)


def _seed_flow_history(directory: str | None = None) -> None:
    """Adopt the CSVs already pulled, so history survives a GUI restart."""
    global _flow_runs, _flow_selected, _flow_step
    _flow_runs = flowtrace.discover_runs(directory or _FLOW_DATA_DIR)
    _flow_selected = 0 if _flow_runs else None
    _flow_step = 0
    _dirty.mark_all(_FLOW_PANES)


@_safe
def _cb_flow_run(sender, app_data, user_data):
    global _flow_selected, _flow_step
    _flow_selected = user_data
    _flow_step = 0
    _dirty.mark_all(_FLOW_PANES)


@_safe
def _cb_flow_step(sender, app_data, user_data):
    global _flow_step
    _flow_step = user_data
    _dirty.mark_all(_FLOW_STEP_PANES)


@_safe
def _cb_flow_pick(sender, app_data, user_data):
    """Select a run and a step at once — a row in the tree names both.

    The tree is the only selector in Trace mode, and a step row belongs to
    exactly one run, so there is nothing for a separate run click to add.
    """
    global _flow_selected, _flow_step
    _flow_selected, _flow_step = user_data
    _dirty.mark_all(_FLOW_PANES)


def _flow_history_to_text() -> str:
    if not _flow_runs:
        return "(no flow runs)"
    return "\n".join(f"{r.label}  {r.steps} steps  {r.path}" for r in _flow_runs)


def _flow_regs_to_text() -> str:
    run = _flow_run()
    if run is None:
        return "(no step selected)"
    if not run.has_regs:
        return _MISSING_FLAG["--regs"]
    return "\n".join(f"{name:<6} 0x{value:x}"
                     for name, value in run.regs_at(_flow_step))


def _flow_mem_to_text() -> str:
    run = _flow_run()
    if run is None:
        return "(no step selected)"
    if not run.has_mem:
        return _MISSING_FLAG["--mem"]
    addr, value = run.mem_at(_flow_step)
    shown = "ERR" if value is None else f"0x{value:x}"
    return f"[0x{addr:x}] = {shown}"


def _flow_tls_to_text() -> str:
    run = _flow_run()
    if run is None:
        return "(no step selected)"
    if not run.has_tls:
        return _MISSING_FLAG["--tls"]
    slots = run.tls_at(_flow_step)
    if not slots:
        return "(no tls at this step)"
    return "\n".join(
        f"+0x{s.slot * 8:<4x} 0x{s.value:016x}  {s.cls:<7} {s.annot}".rstrip()
        for s in slots)


@_safe
def _cb_copy_value(sender, app_data, user_data):
    """A captured value goes to the clipboard and nowhere else.

    The process this run traced is gone, so there is nothing here to ask.
    """
    _copy(user_data, user_data)


def _flow_step_header(tag: str, run) -> None:
    """Which step of which run the pane below is showing.

    The RVA is the one value here worth reusing: `flow` takes a
    library-relative offset, so one click re-launches from this step.
    """
    with dpg.group(horizontal=True, parent=tag):
        dpg.add_text(f"step #{_flow_step}", color=theme.ACCENT_AMBER)
        dpg.add_text(f" of {run.steps}", color=theme.TEXT_DIM)
        rva = run.rows[_flow_step].rva
        dpg.add_selectable(label=f"  +0x{rva:x}", width=0,
                           user_data=f"0x{rva:x}", callback=_cb_use_address)


def _flow_window(total: int, selected: int) -> tuple:
    """Half-open row range to render, kept centred on `selected`."""
    if total <= _FLOW_MAX_ROWS:
        return 0, total
    start = max(0, min(selected - _FLOW_MAX_ROWS // 2, total - _FLOW_MAX_ROWS))
    return start, start + _FLOW_MAX_ROWS


def _populate_flow() -> None:
    if not _take_paint("pane_flow"):
        return
    tag = TAG["pane_flow"]
    dpg.delete_item(tag, children_only=True)
    run = _flow_run()
    if run is None:
        dpg.add_text("(no flow trace)", parent=tag, color=theme.TEXT_DIM)
        return
    with dpg.group(horizontal=True, parent=tag):
        dpg.add_text(run.label, color=theme.ACCENT_AMBER)
        dpg.add_text(f" {run.steps} steps", color=theme.TEXT_DIM)
    start, end = _flow_window(run.steps, _flow_step)
    for i in range(start, end):
        dpg.add_selectable(label=flowtrace.step_to_text(run.rows[i]),
                           parent=tag, user_data=i,
                           default_value=(i == _flow_step),
                           callback=_cb_flow_step)
    if end - start < run.steps:
        dpg.add_text(f"(showing steps {start}-{end - 1} of {run.steps})",
                     parent=tag, color=theme.TEXT_DIM)


def _flow_run_label(run) -> str:
    when = time.strftime("%H:%M:%S", time.localtime(run.mtime))
    symbol = f"  {run.symbol}" if run.symbol else ""
    return f"{run.label}{symbol}  {run.steps} steps  {when}"


def _populate_flow_history() -> None:
    """Runs are folders, steps are files.

    Only the selected run lists its steps. Every DPG row is laid out each frame
    whether its node is open or not, so a history of long runs would otherwise
    cost the frame every step of every one of them.
    """
    if not _take_paint("pane_flow_history"):
        return
    tag = TAG["pane_flow_history"]
    dpg.delete_item(tag, children_only=True)
    if not _flow_runs:
        dpg.add_text("(no flow runs)", parent=tag, color=theme.TEXT_DIM)
        return
    for i, run in enumerate(_flow_runs):
        selected = i == _flow_selected
        node = dpg.add_tree_node(label=_flow_run_label(run), parent=tag,
                                 default_open=selected)
        if not selected:
            dpg.add_selectable(label=f"open  {run.steps} steps", parent=node,
                               user_data=(i, 0), callback=_cb_flow_pick)
            continue
        start, end = _flow_window(run.steps, _flow_step)
        for j in range(start, end):
            dpg.add_selectable(label=f"{j:<5} {run.lib}+0x{run.rows[j].rva:x}",
                               parent=node, user_data=(i, j),
                               default_value=(j == _flow_step),
                               callback=_cb_flow_pick)
        if end - start < run.steps:
            dpg.add_text(f"(showing steps {start}-{end - 1} of {run.steps})",
                         parent=node, color=theme.TEXT_DIM)


def _populate_flow_regs() -> None:
    if not _take_paint("pane_flow_regs"):
        return
    tag = TAG["pane_flow_regs"]
    dpg.delete_item(tag, children_only=True)
    run = _flow_run()
    if run is None:
        dpg.add_text("(no step selected)", parent=tag, color=theme.TEXT_DIM)
        return
    if not run.has_regs:
        dpg.add_text(_MISSING_FLAG["--regs"], parent=tag, color=theme.TEXT_DIM)
        return
    _flow_step_header(tag, run)
    changed = _flow_changed_regs()
    for name, value in run.regs_at(_flow_step):
        with dpg.group(horizontal=True, parent=tag):
            moved = name in changed
            dpg.add_text(" *" if moved else "  ", color=theme.ACCENT_RED)
            dpg.add_text(f"{name:<6}", color=theme.ACCENT_CYAN)
            dpg.add_selectable(label=f"0x{value:x}", width=0,
                               user_data=f"0x{value:x}", callback=_cb_copy_value)


def _populate_flow_mem() -> None:
    if not _take_paint("pane_flow_mem"):
        return
    tag = TAG["pane_flow_mem"]
    dpg.delete_item(tag, children_only=True)
    run = _flow_run()
    if run is None:
        dpg.add_text("(no step selected)", parent=tag, color=theme.TEXT_DIM)
        return
    if not run.has_mem:
        dpg.add_text(_MISSING_FLAG["--mem"], parent=tag, color=theme.TEXT_DIM)
        return
    addr, value = run.mem_at(_flow_step)
    with dpg.group(horizontal=True, parent=tag):
        dpg.add_selectable(label=f"[0x{addr:x}]", width=0,
                           user_data=f"0x{addr:x}", callback=_cb_copy_value)
        dpg.add_text(" = ", color=theme.TEXT_DIM)
        if value is None:
            dpg.add_text("ERR", color=theme.ACCENT_RED)
        else:
            dpg.add_selectable(label=f"0x{value:x}", width=0,
                               user_data=f"0x{value:x}", callback=_cb_copy_value)


def _populate_flow_tls() -> None:
    if not _take_paint("pane_flow_tls"):
        return
    tag = TAG["pane_flow_tls"]
    dpg.delete_item(tag, children_only=True)
    run = _flow_run()
    if run is None:
        dpg.add_text("(no step selected)", parent=tag, color=theme.TEXT_DIM)
        return
    if not run.has_tls:
        dpg.add_text(_MISSING_FLAG["--tls"], parent=tag, color=theme.TEXT_DIM)
        return
    slots = run.tls_at(_flow_step)
    if not slots:
        dpg.add_text("(no tls at this step)", parent=tag, color=theme.TEXT_DIM)
        return
    for s in slots:
        with dpg.group(horizontal=True, parent=tag):
            dpg.add_text(f"+0x{s.slot * 8:<4x}", color=theme.TEXT_DIM)
            dpg.add_selectable(label=f"0x{s.value:016x}", width=0,
                               user_data=f"0x{s.value:x}", callback=_cb_copy_value)
            dpg.add_text(f" {s.cls:<7}",
                         color=_TLS_CLASS_COLOR.get(s.cls, theme.TEXT))
            if s.annot:
                dpg.add_text(f" {s.annot}", color=theme.ACCENT_GREEN)


def _populate_watch() -> None:
    if not _take_paint("pane_watch"):
        return


def _populate_log() -> None:
    # _append_log writes each line as it arrives, so there is nothing to
    # repaint — but the flag still clears only when the pane is visible, so the
    # log obeys the same protocol as everything else.
    _take_paint("pane_log")


_PANE_PAINTERS = {
    "pane_regs":        _populate_regs,
    "pane_breakpoints": _populate_breakpoints,
    "pane_disasm":      _populate_disasm,
    "pane_memory":      _populate_memory,
    "pane_backtrace":   _populate_backtrace,
    "pane_flow":        _populate_flow,
    "pane_flow_history": _populate_flow_history,
    "pane_flow_regs":   _populate_flow_regs,
    "pane_flow_mem":    _populate_flow_mem,
    "pane_flow_tls":    _populate_flow_tls,
    "pane_threads":     _populate_threads,
    "pane_tls":         _populate_tls,
    "pane_watch":       _populate_watch,
    "pane_log":         _populate_log,
}


def _refresh_panes() -> None:
    """Repaint every pane that is both dirty and visible."""
    for paint in _PANE_PAINTERS.values():
        paint()


def _clear_panes() -> None:
    """Clear all inspection panes to placeholder text."""
    for tag in [TAG["pane_regs"], TAG["pane_disasm"], TAG["pane_backtrace"],
                TAG["pane_memory"], TAG["pane_breakpoints"], TAG["pane_threads"]]:
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag, children_only=True)
            dpg.add_text("", parent=tag, color=theme.TEXT_DIM)


def _show_running() -> None:
    """Show 'running...' in inspection panes."""
    for tag in [TAG["pane_regs"], TAG["pane_disasm"]]:
        if dpg.does_item_exist(tag):
            dpg.delete_item(tag, children_only=True)
            dpg.add_text("... running ...", parent=tag, color=theme.TEXT_DIM)


# =====================================================================
#  Auto-fetch on breakpoint hit
# =====================================================================

def _auto_fetch_on_hit() -> None:
    """Send bt, info thread, and memory examine for pointer registers."""
    _session.send_command("bt")
    _session.send_command("info thread")
    _session.send_command("tls")

    count = 0
    for r in _session.last_regs:
        if count >= 8:
            break
        # Heuristic: ARM64 userspace pointers live above ~4 GB
        if r.value >= 0x1000000000 and r.value <= 0x7FFFFFFFFFFF:
            _session.send_command(f"x 0x{r.value:x} 64")
            count += 1


# =====================================================================
#  Callbacks
# =====================================================================

@_safe
def _cb_connect(sender, app_data, user_data):
    pkg = dpg.get_value(TAG["input_package"]).strip()
    lib = dpg.get_value(TAG["input_lib"]).strip()
    brk_raw = dpg.get_value(TAG["input_breaks"]).strip()

    if not pkg or not lib:
        _set_status("Error: package and library required", CLR_RED)
        return

    breaks = [b.strip() for b in brk_raw.split(",") if b.strip()] if brk_raw else []

    _set_status("Connecting...", CLR_YELLOW)
    _session.start(pkg, lib, breaks)

    if _session.state == State.ERROR:
        _set_status(f"Error: {_session.transcript[-1] if _session.transcript else 'unknown'}", CLR_RED)
    else:
        _set_status(f"Attached — {pkg} / {lib}", CLR_GREEN)
    _update_run_controls()


@_safe
def _cb_disconnect(sender, app_data, user_data):
    _session.stop()
    _set_status("Disconnected", CLR_GRAY)
    _clear_panes()
    _update_run_controls()


@_safe
def _cb_continue(sender, app_data, user_data):
    _session.continue_()
    _set_status("Running...", CLR_YELLOW)
    _show_running()
    _update_run_controls()


@_safe
def _cb_interrupt(sender, app_data, user_data):
    _session.interrupt()
    _set_status("Interrupt sent", CLR_YELLOW)


@_safe
def _cb_step(sender, app_data, user_data):
    _session.send_command("s")
    _session.state = State.RUNNING
    _set_status("Stepping...", CLR_YELLOW)
    _show_running()
    _update_run_controls()


@_safe
def _cb_next(sender, app_data, user_data):
    _session.send_command("n")
    _session.state = State.RUNNING
    _set_status("Next...", CLR_YELLOW)
    _show_running()
    _update_run_controls()


@_safe
def _cb_finish(sender, app_data, user_data):
    _session.send_command("fi")
    _session.state = State.RUNNING
    _set_status("Finishing...", CLR_YELLOW)
    _show_running()
    _update_run_controls()


@_safe
def _cb_until(sender, app_data, user_data):
    dpg.configure_item(TAG["until_popup"], show=True)


@_safe
def _cb_until_go(sender, app_data, user_data):
    addr = dpg.get_value(TAG["input_until_addr"]).strip()
    if addr:
        _session.send_command(f"u {addr}")
        _session.state = State.RUNNING
        _set_status(f"Until {addr}...", CLR_YELLOW)
        _show_running()
        _update_run_controls()
    dpg.configure_item(TAG["until_popup"], show=False)


@_safe
def _cb_send_cmd(sender, app_data, user_data):
    line = dpg.get_value(TAG["input_cmd"]).strip()
    if not line:
        return
    _session.send_command(line)
    dpg.set_value(TAG["input_cmd"], "")

    # Handle commands that expect continued output
    cmd = line.split()[0].lower()
    if cmd in ("c", "continue", "r", "run"):
        _session.state = State.RUNNING
        _set_status("Running...", CLR_YELLOW)
        _show_running()
        _update_run_controls()
    elif cmd in ("s", "step"):
        _session.state = State.RUNNING
        _set_status("Stepping...", CLR_YELLOW)
        _show_running()
        _update_run_controls()
    elif cmd in ("n", "next"):
        _session.state = State.RUNNING
        _set_status("Next...", CLR_YELLOW)
        _show_running()
        _update_run_controls()
    elif cmd in ("fi", "finish"):
        _session.state = State.RUNNING
        _set_status("Finishing...", CLR_YELLOW)
        _show_running()
        _update_run_controls()
    elif cmd in ("bt", "bt1", "bt2", "backtrace", "backtrace1", "backtrace2"):
        pass  # will parse from output
    elif cmd.startswith("info"):
        pass  # will parse from output


@_safe
def _cb_examine(sender, app_data, user_data):
    addr = dpg.get_value(TAG["input_examine_addr"]).strip()
    size = dpg.get_value(TAG["input_examine_size"]).strip() or "64"
    if not addr:
        return
    _session.send_command(f"x {addr} {size}")


@_safe
def _cb_write_mem(sender, app_data, user_data):
    addr = dpg.get_value(TAG["input_write_addr"]).strip()
    hex_data = dpg.get_value(TAG["input_write_hex"]).strip()
    if addr and hex_data:
        _session.send_command(f"w {addr} {hex_data}")


@_safe
def _cb_bp_add(sender, app_data, user_data):
    bp_type = dpg.get_value(TAG["input_bp_type"])
    addr = dpg.get_value(TAG["input_bp_addr"]).strip()
    if not addr:
        return
    cmd_map = {
        "b (file offset)": "b",
        "vb (virtual)": "vb",
        "hb (hardware)": "hb",
        "watch (write)": "watch",
        "rwatch (read)": "rwatch",
    }
    cmd = cmd_map.get(bp_type, "b")
    _session.send_command(f"{cmd} {addr}")
    dpg.set_value(TAG["input_bp_addr"], "")
    # Refresh BP list
    _session.send_command("info b")


@_safe
def _cb_bp_enable(sender, app_data, user_data):
    bp_id = user_data
    _session.send_command(f"enable {bp_id}")
    _session.send_command("info b")


@_safe
def _cb_bp_disable(sender, app_data, user_data):
    bp_id = user_data
    _session.send_command(f"disable {bp_id}")
    _session.send_command("info b")


@_safe
def _cb_bp_delete(sender, app_data, user_data):
    bp_id = user_data
    _session.send_command(f"delete {bp_id}")
    _session.send_command("info b")


@_safe
def _cb_display_add(sender, app_data, user_data):
    name = dpg.get_value(TAG["input_display_name"]).strip()
    addr = dpg.get_value(TAG["input_display_addr"]).strip()
    length = dpg.get_value(TAG["input_display_len"]).strip() or "64"
    if addr:
        # Go's order is `display <address> <len> <name>` (cli/repl.go).
        _session.send_command(f"display {addr} {length} {name}")


@_safe
def _cb_flow(sender, app_data, user_data):
    addr = dpg.get_value(TAG["input_flow_addr"]).strip()
    if not addr:
        return
    # Go's flow parser matches double-dash flags exactly (cli/repl.go).
    parts = [f"flow {addr}"]
    if dpg.get_value(TAG["chk_flow_over"]):
        parts.append("--over")
    max_steps = dpg.get_value(TAG["input_flow_max"]).strip()
    if max_steps:
        parts.append(f"--max {max_steps}")
    if dpg.get_value(TAG["chk_flow_regs"]):
        parts.append("--regs")
    mem_reg = dpg.get_value(TAG["input_flow_mem"]).strip()
    if mem_reg:
        parts.append(f"--mem {mem_reg}")
    if dpg.get_value(TAG["chk_flow_quiet"]):
        parts.append("--quiet")
    _session.send_command(" ".join(parts))
    _session.state = State.RUNNING
    _set_status("Flow tracing...", CLR_YELLOW)
    _update_run_controls()


@_safe
def _cb_thread_select(sender, app_data, user_data):
    tid = user_data
    _session.send_command(f"thread {tid}")


def _cb_cmd_enter(sender, app_data, user_data):
    """Handle Enter key in command bar."""
    _cb_send_cmd(sender, app_data, user_data)


# =====================================================================
#  Frame update (called every render frame)
# =====================================================================

def _frame_update():
    """Poll session and update UI. Called each DPG frame."""
    global _last_rendered_hit, _accumulated_reg_mem

    new_lines = _session.poll_and_parse()

    # Append new lines to log (with ANSI color interpretation)
    for line in new_lines:
        _append_log(line)

    # Update status bar
    state = _session.state
    if state == State.STOPPED and _session.last_hit:
        hit = _session.last_hit
        _set_status(
            f"STOPPED — Hit #{hit.hit_number}  pid={hit.pid} tid={hit.tid}",
            CLR_RED
        )
        # Refresh panes only on new hit
        if _last_rendered_hit != hit.hit_number:
            _last_rendered_hit = hit.hit_number
            # Seed accumulated memory with display-section data
            _accumulated_reg_mem = list(_session.last_memory)
            _dirty.mark_all(modes.PANES)
            # Auto-fetch backtrace, threads, and register memory
            _auto_fetch_on_hit()
    elif state == State.RUNNING:
        _set_status("Running...", CLR_YELLOW)
    elif state == State.DISCONNECTED and _session.transcript:
        # Process may have exited
        _set_status("Disconnected", CLR_GRAY)

    _update_run_controls()

    # Detect flow CSV completion (look for "flow finished" in new lines)
    for line in new_lines:
        clean = parse.strip_ansi(line).lower()
        if "flow" in clean and ("finished" in clean or "done" in clean or "csv" in clean):
            _handle_flow_csv(new_lines)
            break

    # Parse inline responses (memory examine, breakpoint info, thread info,
    # backtrace, tls). Handlers mark panes; painting happens once, below, and
    # only for panes the active mode actually shows.
    for line in new_lines:
        clean = parse.strip_ansi(line)
        mem = parse.parse_memory([clean])
        if mem:
            _accumulated_reg_mem.extend(mem)
            _dirty.mark("pane_memory")
        bps = parse.parse_breakpoints([clean])
        if bps:
            _session.last_breakpoints.extend(bps)
            _dirty.mark("pane_breakpoints")
        threads = parse.parse_threads([clean])
        if threads:
            _session.last_threads.extend(threads)
            _dirty.mark("pane_threads")
        bt = parse.parse_backtrace([clean])
        if bt:
            _session.last_backtrace.extend(bt)
            _dirty.mark("pane_backtrace")

    # A dump is many lines and the pty hands over whatever bytes arrived, so a
    # header and its slots routinely land in different polls. Parsing a rolling
    # window instead of one batch is what makes a split dump survive; re-parsing
    # only when TLS-shaped output arrived is what keeps it off the hot path.
    clean_lines = [parse.strip_ansi(l) for l in new_lines]
    _recent_output.extend(clean_lines)
    if any(parse.is_tls_line(l) for l in clean_lines):
        tls = parse.parse_tls(list(_recent_output))
        if tls is not None and tls.slots:
            _session.last_tls = tls
            _dirty.mark("pane_tls")

    _refresh_panes()


def _handle_flow_csv(lines: list[str]) -> None:
    """Pull the finished run off the device and enter it into history."""
    os.makedirs(_FLOW_DATA_DIR, exist_ok=True)
    for line in lines:
        clean = parse.strip_ansi(line)
        if ".csv" not in clean.lower():
            continue
        for token in clean.split():
            if not token.endswith(".csv"):
                continue
            local = os.path.join(_FLOW_DATA_DIR, os.path.basename(token))
            if not _session.pull_file(token, local):
                _append_log(f"[GUI] Failed to pull {token}")
                return
            run = flowtrace.parse_flow_csv(local)
            if run is None:
                _append_log(f"[GUI] Flow CSV unreadable: {local}")
                return
            _add_flow_run(run)
            _append_log(f"[GUI] Flow {run.label}: {run.steps} steps → {local}")
            return


# =====================================================================
#  Layout
# =====================================================================

_MARGIN = 6            # px outer gap
_CHROME_H = 150        # px of HUD + mode bar + command bar above/below the shell

_active_mode: str = modes.DEFAULT_MODE

#: Checkbox per optional pane, in the order they read on the row.
_PANE_TOGGLE_LABEL = {
    "pane_flow_regs": "Registers",
    "pane_flow_mem":  "Memory",
    "pane_flow_tls":  "TLS",
    "pane_flow":      "Trace rows",
}

#: Optional panes the operator has turned off. Trace rows start off: the tree
#: already lists the same steps, so the pane earns its place by being asked for.
_pane_off: set = {"pane_flow"}


def _hidden_panes(mode: str) -> set:
    """Panes off in `mode`. Only a pane the mode calls optional can be off."""
    return _pane_off & modes.MODES[mode].optional


def current_mode() -> str:
    return _active_mode


def _hdr(label: str) -> None:
    """HUD-style section label."""
    dpg.add_text(label, color=CLR_CYAN)


def _build_menu_bar() -> None:
    with dpg.viewport_menu_bar():
        with dpg.menu(label="File"):
            dpg.add_menu_item(label="Quit", callback=lambda: dpg.stop_dearpygui())
        with dpg.menu(label="Edit"):
            dpg.add_menu_item(
                label="Copy everything",
                callback=_safe(lambda s, a, u:
                               _copy(textdump.session_to_text(_session),
                                     "session")))
            dpg.add_separator()
            for _key, (_label, _getter) in _COPY_SOURCES.items():
                dpg.add_menu_item(
                    label=f"Copy {_label}",
                    callback=_safe(lambda s, a, u, g=_getter, l=_label:
                                   _copy(g(), l)))
            dpg.add_separator()
            dpg.add_menu_item(label="(or right-click any pane)", enabled=False)
        with dpg.menu(label="Session"):
            dpg.add_menu_item(label="Refresh BPs", callback=lambda: _session.send_command("info b"))
            dpg.add_menu_item(label="Refresh Threads", callback=lambda: _session.send_command("info thread"))
            dpg.add_menu_item(label="Backtrace", callback=lambda: _session.send_command("bt"))
        dpg.add_menu_item(label="eDBG", enabled=False)


def _build_hud() -> None:
    """Status, connection and run controls — persistent, never mode-switched."""
    with dpg.group(horizontal=True):
        dpg.add_text("eDBG", color=CLR_BLUE)
        dpg.add_text("|", color=theme.BORDER)
        dpg.add_text("●", tag=TAG["status_text"] + "_dot", color=CLR_GRAY)
        dpg.add_text("Disconnected", tag=TAG["status_text"], color=CLR_GRAY)
    dpg.add_separator()

    with dpg.group(horizontal=True):
        _hdr("PKG"); dpg.add_input_text(tag=TAG["input_package"], width=240,
                                        default_value="com.shlomi.RollABall2019")
        _hdr("LIB"); dpg.add_input_text(tag=TAG["input_lib"], width=180,
                                        default_value="libloader.so")
        _hdr("BREAK -b"); dpg.add_input_text(tag=TAG["input_breaks"], width=320,
                                             hint="0x1234, 0x5678 (file offsets)")
        dpg.add_button(label="Connect", tag=TAG["btn_connect"], callback=_cb_connect)
        dpg.add_button(label="Disconnect", tag=TAG["btn_disconnect"],
                       callback=_cb_disconnect, enabled=False)

    # Both rows are built once and swapped by show/hide, the same trick the
    # pane pool uses. Which one a mode wants is in the registry, not here.
    with dpg.group(horizontal=True, tag=control_row_tag("run")):
        dpg.add_button(label=" Continue (F5) ", tag=TAG["btn_continue"],
                       callback=_cb_continue, enabled=False)
        dpg.add_button(label=" Interrupt ", tag=TAG["btn_interrupt"],
                       callback=_cb_interrupt, enabled=False)
        dpg.add_spacer(width=12)
        dpg.add_button(label=" Step (F11) ", tag=TAG["btn_step"],
                       callback=_cb_step, enabled=False)
        dpg.add_button(label=" Next (F10) ", tag=TAG["btn_next"],
                       callback=_cb_next, enabled=False)
        dpg.add_button(label=" Finish ", tag=TAG["btn_finish"],
                       callback=_cb_finish, enabled=False)
        dpg.add_button(label=" Until ", tag=TAG["btn_until"],
                       callback=_cb_until, enabled=False)

    with dpg.group(horizontal=True, tag=control_row_tag("trace"), show=False):
        _hdr("SHOW")
        for pane, label in _PANE_TOGGLE_LABEL.items():
            dpg.add_checkbox(label=label, tag=pane_toggle_tag(pane),
                             default_value=pane not in _pane_off,
                             user_data=pane, callback=_cb_pane_toggle)


def _mode_button_label(name: str, active: bool) -> str:
    """Active mode is marked in the label, not by a bound theme.

    Theme ids belong to the DPG context that created them, so a global holding
    one goes stale the moment a context is replaced. A label carries no such
    lifetime.
    """
    marker = "▸" if active else " "
    return f"{marker} {modes.MODES[name].label} ({modes.MODE_KEYS[name]}) "


def _build_mode_bar() -> None:
    dpg.add_separator()
    with dpg.group(horizontal=True):
        for name in modes.MODES:
            dpg.add_button(label=_mode_button_label(name, name == modes.DEFAULT_MODE),
                           tag=mode_button_tag(name),
                           user_data=name, callback=_cb_mode)
    dpg.add_separator()


def _build_command_bar() -> None:
    with dpg.group(horizontal=True):
        dpg.add_text("(eDBG)", color=CLR_CYAN)
        dpg.add_input_text(tag=TAG["input_cmd"], width=-90,
                           hint="type REPL command...",
                           on_enter=True, callback=_cb_cmd_enter)
        dpg.add_button(label=" Send ", tag=TAG["btn_send"],
                       callback=_cb_send_cmd, enabled=False)


# ── Panes ────────────────────────────────────────────────────────────
#
# One instance each, built into the column gui.modes assigns. A mode shows or
# hides the section wrapper and rewrites column weights; it never rebuilds,
# re-parents, or duplicates a pane.

def _pane_title(pane: str, label: str) -> None:
    """Pane header plus its text-mode toggle.

    Both sit on one row so the header stays the single line ``_PANE_CHROME``
    budgets for it.
    """
    with dpg.group(horizontal=True):
        dpg.add_text(label, color=CLR_CYAN)
        if pane in _COPY_SOURCES:
            dpg.add_button(label="TEXT", tag=text_toggle_tag(pane), small=True,
                           user_data=pane, callback=_cb_toggle_text)


def _pane_body(pane: str, empty: str) -> None:
    with dpg.child_window(tag=TAG[pane], height=-1, width=-1, border=False):
        dpg.add_text(empty, color=theme.TEXT_DIM)


def _build_pane_regs() -> None:
    _pane_title("pane_regs", "REGISTERS")
    _pane_body("pane_regs", "(no registers)")


def _build_pane_breakpoints() -> None:
    _pane_title("pane_breakpoints", "BREAKPOINTS")
    with dpg.group(horizontal=True):
        dpg.add_combo(
            items=["b (file offset)", "vb (virtual)", "hb (hardware)",
                   "watch (write)", "rwatch (read)"],
            tag=TAG["input_bp_type"], default_value="b (file offset)", width=140)
        dpg.add_input_text(tag=TAG["input_bp_addr"], width=110, hint="0x...")
        dpg.add_button(label="Add", tag=TAG["btn_bp_add"],
                       callback=_cb_bp_add, enabled=False)
    _pane_body("pane_breakpoints", "(no breakpoints)")


def _build_pane_disasm() -> None:
    _pane_title("pane_disasm", "DISASSEMBLY")
    _pane_body("pane_disasm", "(no disassembly)")


def _build_pane_memory() -> None:
    _pane_title("pane_memory", "MEMORY")
    with dpg.group(horizontal=True):
        _hdr("Addr"); dpg.add_input_text(tag=TAG["input_examine_addr"],
                                         width=150, hint="0x...")
        _hdr("Size"); dpg.add_input_text(tag=TAG["input_examine_size"],
                                         width=60, default_value="64")
        dpg.add_button(label="Examine", tag=TAG["btn_examine"],
                       callback=_cb_examine, enabled=False)
    with dpg.group(horizontal=True):
        _hdr("Write"); dpg.add_input_text(tag=TAG["input_write_addr"],
                                          width=150, hint="0x...")
        dpg.add_input_text(tag=TAG["input_write_hex"], width=180, hint="41424344")
        dpg.add_button(label="Write", tag=TAG["btn_write"],
                       callback=_cb_write_mem, enabled=False)
    _pane_body("pane_memory", "(no memory)")


def _build_pane_backtrace() -> None:
    _pane_title("pane_backtrace", "BACKTRACE")
    _pane_body("pane_backtrace", "(no backtrace)")


def _build_pane_flow() -> None:
    _pane_title("pane_flow", "FLOW TRACE")
    _pane_body("pane_flow", "(no flow trace)")


def _build_pane_flow_history() -> None:
    """The tree, and the controls that create what it lists.

    Run Flow appends to this history, and the run-control row it used to sit
    beside is not on screen in Trace mode.
    """
    _pane_title("pane_flow_history", "FLOW HISTORY")
    with dpg.group(horizontal=True):
        _hdr("Addr"); dpg.add_input_text(tag=TAG["input_flow_addr"],
                                         width=140, hint="0x...")
        dpg.add_checkbox(label="Over", tag=TAG["chk_flow_over"])
        _hdr("Max"); dpg.add_input_text(tag=TAG["input_flow_max"],
                                        width=70, default_value="10000")
    with dpg.group(horizontal=True):
        dpg.add_checkbox(label="Regs", tag=TAG["chk_flow_regs"])
        _hdr("Mem"); dpg.add_input_text(tag=TAG["input_flow_mem"],
                                        width=60, hint="X0")
        dpg.add_checkbox(label="Quiet", tag=TAG["chk_flow_quiet"])
        dpg.add_button(label="Run Flow", tag=TAG["btn_flow"],
                       callback=_cb_flow, enabled=False)
    _pane_body("pane_flow_history", "(no flow runs)")


def _build_pane_flow_regs() -> None:
    _pane_title("pane_flow_regs", "STEP REGISTERS")
    _pane_body("pane_flow_regs", "(no step selected)")


def _build_pane_flow_mem() -> None:
    _pane_title("pane_flow_mem", "STEP MEMORY")
    _pane_body("pane_flow_mem", "(no step selected)")


def _build_pane_flow_tls() -> None:
    _pane_title("pane_flow_tls", "STEP TLS")
    _pane_body("pane_flow_tls", "(no step selected)")


def _build_pane_threads() -> None:
    _pane_title("pane_threads", "THREADS")
    _pane_body("pane_threads", "(no threads)")


def _build_pane_tls() -> None:
    _pane_title("pane_tls", "TLS")
    _pane_body("pane_tls", "(no tls)")


def _build_pane_watch() -> None:
    _pane_title("pane_watch", "WATCH")
    with dpg.group(horizontal=True):
        _hdr("Name"); dpg.add_input_text(tag=TAG["input_display_name"],
                                         width=90, hint="myvar")
        _hdr("Addr"); dpg.add_input_text(tag=TAG["input_display_addr"],
                                         width=130, hint="0x...")
        _hdr("Len"); dpg.add_input_text(tag=TAG["input_display_len"],
                                        width=50, default_value="64")
        dpg.add_button(label="Add", tag=TAG["btn_display_add"],
                       callback=_cb_display_add, enabled=False)
    _pane_body("pane_watch", "(no watches)")


def _build_pane_log() -> None:
    _pane_title("pane_log", "LOG / TRANSCRIPT")
    _pane_body("pane_log", "")


_PANE_BUILDERS = {
    "pane_regs":        _build_pane_regs,
    "pane_breakpoints": _build_pane_breakpoints,
    "pane_disasm":      _build_pane_disasm,
    "pane_memory":      _build_pane_memory,
    "pane_backtrace":   _build_pane_backtrace,
    "pane_flow":        _build_pane_flow,
    "pane_flow_history": _build_pane_flow_history,
    "pane_flow_regs":   _build_pane_flow_regs,
    "pane_flow_mem":    _build_pane_flow_mem,
    "pane_flow_tls":    _build_pane_flow_tls,
    "pane_threads":     _build_pane_threads,
    "pane_tls":         _build_pane_tls,
    "pane_watch":       _build_pane_watch,
    "pane_log":         _build_pane_log,
}


def _build_shell() -> None:
    with dpg.table(tag=TAG["shell"], header_row=False, resizable=True,
                   borders_innerV=True, policy=dpg.mvTable_SizingStretchProp,
                   height=-28):
        for col, weight in zip(modes.COLUMNS,
                               modes.col_weights(modes.DEFAULT_MODE)):
            dpg.add_table_column(tag=column_tag(col), init_width_or_weight=weight)
        with dpg.table_row():
            for col in modes.COLUMNS:
                with dpg.table_cell(tag=cell_tag(col)):
                    for pane in modes.panes_in_column(col):
                        with dpg.group(tag=section_tag(pane)):
                            _PANE_BUILDERS[pane]()


# Non-pane pixels each section spends above its child window: one header line
# plus one row per control group. Unaccounted-for chrome is what pushes the
# bottom pane in a column off the end of the viewport.
_HEADER_H = 22
_CONTROL_ROW_H = 30
_PANE_CHROME = {
    "pane_regs":        _HEADER_H,
    "pane_breakpoints": _HEADER_H + _CONTROL_ROW_H,
    "pane_disasm":      _HEADER_H,
    "pane_memory":      _HEADER_H + 2 * _CONTROL_ROW_H,
    "pane_backtrace":   _HEADER_H,
    "pane_flow":        _HEADER_H,
    "pane_flow_history": _HEADER_H + 2 * _CONTROL_ROW_H,
    "pane_flow_regs":   _HEADER_H,
    "pane_flow_mem":    _HEADER_H,
    "pane_flow_tls":    _HEADER_H,
    "pane_threads":     _HEADER_H,
    "pane_tls":         _HEADER_H,
    "pane_watch":       _HEADER_H + _CONTROL_ROW_H,
    "pane_log":         _HEADER_H,
}


def _pane_heights(avail: int, mode: str, hidden=()) -> dict[str, int]:
    """Pixel height per visible pane. Pure arithmetic, so it is testable.

    Each column's chrome is subtracted before the remainder is shared out, so
    the panes plus their headers and controls fit the column exactly. A pane
    toggled off is not in `shares` at all, which is how its pixels reach its
    neighbours instead of leaving a gap.
    """
    out: dict[str, int] = {}
    for col in modes.COLUMNS:
        shares = modes.row_weights(mode, col, hidden)
        chrome = sum(_PANE_CHROME[p] for p in shares)
        body = max(60 * len(shares), avail - chrome)
        for pane, share in shares.items():
            out[pane] = max(40, int(body * share))
    return out


def _apply_pane_heights() -> None:
    """Divide each column's height among the panes visible in the active mode.

    DPG tables do not resize rows, so stacked panes need explicit pixel heights.
    This is height arithmetic only — column widths and positions belong to the
    primary window and the stretch table.
    """
    if not dpg.is_viewport_ok():
        return          # headless build: heights land on the first real frame
    avail = max(200, dpg.get_viewport_client_height() - _CHROME_H)
    heights = _pane_heights(avail, _active_mode, _hidden_panes(_active_mode))
    for pane, height in heights.items():
        dpg.configure_item(TAG[pane], height=height)


def _set_mode(name: str) -> None:
    """Show the panes `name` wants, hide the rest, rewrite the column weights.

    No widget is created, destroyed, or re-parented — that is the whole reason
    a pane can appear in more than one mode.
    """
    global _active_mode
    _active_mode = name
    _apply_pane_visibility()
    for col, weight in zip(modes.COLUMNS, modes.col_weights(name)):
        dpg.configure_item(column_tag(col), init_width_or_weight=weight)
    for row in modes.CONTROLS:
        dpg.configure_item(control_row_tag(row),
                           show=row == modes.MODES[name].controls)
    for mode_name in modes.MODES:
        dpg.configure_item(mode_button_tag(mode_name),
                           label=_mode_button_label(mode_name, mode_name == name))
    _apply_pane_heights()
    _repaint_revealed()


def _apply_pane_visibility() -> None:
    """A pane is on screen iff its mode lists it and its toggle is on."""
    visible = set(modes.visible_panes(_active_mode, _hidden_panes(_active_mode)))
    for pane in modes.PANES:
        dpg.configure_item(section_tag(pane), show=pane in visible)


@_safe
def _cb_pane_toggle(sender, app_data, user_data):
    pane = user_data
    if pane not in modes.MODES[_active_mode].optional:
        return
    if app_data:
        _pane_off.discard(pane)
    else:
        _pane_off.add(pane)
    # A pane hidden across N selections must come back showing the current one,
    # so the reveal paints rather than waiting for the next selection to mark it.
    _dirty.mark(pane)
    _apply_pane_visibility()
    _apply_pane_heights()
    _repaint_revealed()


def _set_mode_by_key(key: str) -> None:
    for name, bound in modes.MODE_KEYS.items():
        if bound == key:
            _set_mode(name)
            return


@_safe
def _cb_mode(sender, app_data, user_data):
    _set_mode(user_data)


def _build_layout():
    """Build the fixed mode shell: one root window, three columns, one pane pool."""
    _build_menu_bar()
    with dpg.window(tag=TAG["root"], no_scrollbar=True):
        _build_hud()
        _build_mode_bar()
        _build_shell()
        _build_command_bar()

    with dpg.window(label="Run Until Address", tag=TAG["until_popup"],
                    modal=True, show=False, width=300, height=100):
        dpg.add_input_text(tag=TAG["input_until_addr"], hint="0x...", width=-1)
        dpg.add_button(label="Go", callback=_cb_until_go)


# =====================================================================
#  Keyboard shortcuts
# =====================================================================

def _setup_keybindings():
    """Register global keyboard shortcuts."""
    with dpg.handler_registry():
        dpg.add_key_press_handler(dpg.mvKey_F5, callback=_cb_continue)
        dpg.add_key_press_handler(dpg.mvKey_F10, callback=_cb_next)
        dpg.add_key_press_handler(dpg.mvKey_F11, callback=_cb_step)
        for name, key in modes.MODE_KEYS.items():
            dpg.add_key_press_handler(
                getattr(dpg, f"mvKey_{key}"),
                callback=_safe(lambda s, a, u, m=name: _set_mode(m)))


# =====================================================================
#  Entry point
# =====================================================================

def _apply_widget_themes():
    """Bind accent themes to specific buttons after layout is built."""
    dpg.bind_item_theme(TAG["btn_connect"], _theme_green_btn)
    dpg.bind_item_theme(TAG["btn_disconnect"], _theme_red_btn)
    for btn in [TAG["btn_continue"], TAG["btn_step"], TAG["btn_next"],
                TAG["btn_finish"], TAG["btn_until"], TAG["btn_interrupt"]]:
        dpg.bind_item_theme(btn, _theme_run_btn)

    # Bind tight-spacing pane theme to all colored output child windows
    if _pane_theme:
        for pane in modes.PANES:
            if dpg.does_item_exist(TAG[pane]):
                dpg.bind_item_theme(TAG[pane], _pane_theme)


def main():
    """Launch the eDBG Dear PyGui workbench."""
    global _theme_green_btn, _theme_red_btn, _theme_run_btn, _pane_theme
    dpg.create_context()
    dpg.configure_app(docking=False)

    theme.setup_fonts()
    accents = theme.setup_theme()
    _theme_green_btn = accents["green"]
    _theme_red_btn = accents["red"]
    _theme_run_btn = accents["run"]
    _pane_theme = theme.make_pane_content_theme()

    dpg.create_viewport(title="eDBG Debugger", width=1520, height=1040)

    _build_layout()
    _apply_widget_themes()
    _attach_copy_menus()
    _setup_keybindings()

    dpg.setup_dearpygui()
    dpg.show_viewport()

    # The primary window fills and tracks the viewport, so nothing floats and
    # no pixel arithmetic is needed for placement. Only stacked-pane heights
    # follow the viewport, because DPG tables do not resize rows.
    dpg.set_primary_window(TAG["root"], True)
    _seed_flow_history()
    _set_mode(modes.DEFAULT_MODE)
    dpg.set_viewport_resize_callback(lambda *_: _apply_pane_heights())

    # Initial log message
    _append_log("[GUI] Ready.")

    # Frame-update callback
    dpg.set_frame_callback(1, callback=lambda: None)  # ensure started

    # Ctrl+C / SIGTERM must reach the device teardown, not just kill the loop —
    # an orphaned eDBG keeps its hardware debug registers armed.
    def _on_signal(signum, frame):
        _session.stop()
        dpg.stop_dearpygui()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _on_signal)

    try:
        while dpg.is_dearpygui_running():
            _frame_update()
            dpg.render_dearpygui_frame()
    finally:
        _session.stop()
        dpg.destroy_context()
