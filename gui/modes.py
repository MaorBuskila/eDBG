"""Mode registry for the v3 shell — pure data, no ``dearpygui`` import.

A mode is ``{visible_panes, col_weights}``. Switching modes shows and hides
existing panes and rewrites three column weights; it never creates, destroys,
or re-parents a widget. Adding a mode is a ``MODES`` entry.

Column fractions are measured, not chosen — see ``MIN_COL_FRAC``.
"""

from __future__ import annotations

from dataclasses import dataclass

COLUMNS = ("left", "center", "right")

#: Every pane the shell builds. A pane not listed here cannot enter a mode.
PANES = (
    "pane_regs",
    "pane_breakpoints",
    "pane_disasm",
    "pane_memory",
    "pane_backtrace",
    "pane_flow",
    "pane_threads",
    "pane_tls",
    "pane_watch",
    "pane_log",
)

#: Minimum column fraction each pane needs to render its widest line without
#: clipping, from ``dpg.get_text_size`` at FONT_SIZE=15 on a 1520px viewport.
#: Measured, not guessed: memory at 0.22 had 310px against 539px needed, and
#: registers carrying a symbol plus a deref need 0.278 — the annotations that
#: make the pane worth having are exactly what a narrow column drops first.
MIN_COL_FRAC = {
    "pane_regs":        0.278,
    "pane_breakpoints": 0.220,
    "pane_disasm":      0.314,
    "pane_memory":      0.355,
    "pane_backtrace":   0.220,
    "pane_flow":        0.314,
    "pane_threads":     0.081,
    "pane_tls":         0.310,
    "pane_watch":       0.220,
    "pane_log":         0.314,
}

#: A collapsed column keeps a tiny weight rather than ``show=False`` — hiding a
#: DPG table column re-flows the survivors unpredictably.
COLLAPSED = 0.0001


@dataclass(frozen=True)
class Mode:
    label: str
    panes: dict[str, tuple[str, ...]]
    weights: tuple[float, float, float]


_LOG_CENTER = 1.0 - 2 * COLLAPSED

MODES: dict[str, Mode] = {
    "step": Mode(
        label="Step",
        panes={
            "left":   ("pane_regs", "pane_breakpoints"),
            "center": ("pane_disasm", "pane_memory", "pane_backtrace"),
            "right":  ("pane_threads", "pane_tls"),
        },
        weights=(0.28, 0.40, 0.32),
    ),
    "trace": Mode(
        label="Trace",
        panes={
            "left":   ("pane_regs",),
            "center": ("pane_flow",),
            "right":  ("pane_watch",),
        },
        weights=(0.28, 0.40, 0.32),
    ),
    "inspect": Mode(
        label="Inspect",
        panes={
            "left":   ("pane_regs",),
            "center": ("pane_memory",),
            "right":  ("pane_threads", "pane_tls", "pane_watch"),
        },
        weights=(0.28, 0.40, 0.32),
    ),
    "log": Mode(
        label="Log",
        panes={
            "left":   (),
            "center": ("pane_log",),
            "right":  (),
        },
        weights=(COLLAPSED, _LOG_CENTER, COLLAPSED),
    ),
}

MODE_KEYS = {"step": "F1", "trace": "F2", "inspect": "F3", "log": "F4"}

DEFAULT_MODE = "step"


def col_weights(mode: str) -> tuple[float, float, float]:
    return MODES[mode].weights


def visible_panes(mode: str) -> list[str]:
    """Panes shown in `mode`, ordered left column to right."""
    m = MODES[mode]
    return [p for col in COLUMNS for p in m.panes[col]]


def column_of(mode: str, pane: str) -> str | None:
    for col in COLUMNS:
        if pane in MODES[mode].panes[col]:
            return col
    return None
