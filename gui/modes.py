"""Mode registry for the v3 shell — pure data, no ``dearpygui`` import.

A mode is ``{visible_panes, col_weights, optional, controls}``. Switching modes
shows and hides existing panes and rewrites three column weights; it never
creates, destroys, or re-parents a widget. Adding a mode is a ``MODES`` entry.

Column fractions are measured, not chosen — see ``MIN_COL_FRAC``.

A pane is on screen iff its mode lists it **and** its toggle is on, so every
query taking a ``hidden`` set answers the second half of that expression. The
toggle state itself belongs to the app; only the arithmetic lives here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

COLUMNS = ("left", "center", "right")

#: Every pane the shell builds. A pane not listed here cannot enter a mode.
PANES = (
    "pane_regs",
    "pane_breakpoints",
    "pane_flow_history",
    "pane_disasm",
    "pane_memory",
    "pane_backtrace",
    "pane_flow_regs",
    "pane_flow",
    "pane_flow_mem",
    "pane_flow_tls",
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
    "pane_flow_history": 0.220,
    "pane_disasm":      0.314,
    "pane_memory":      0.355,
    "pane_backtrace":   0.220,
    # No symbol or deref column, so it needs less than the live register pane.
    "pane_flow_regs":   0.240,
    "pane_flow":        0.314,
    # One probed address and its eight bytes — the narrowest pane in the shell.
    "pane_flow_mem":    0.220,
    # Same slot line as the live TLS pane, so the same floor.
    "pane_flow_tls":    0.310,
    "pane_threads":     0.081,
    "pane_tls":         0.310,
    "pane_watch":       0.220,
    "pane_log":         0.314,
}

#: A collapsed column keeps a tiny weight rather than ``show=False`` — hiding a
#: DPG table column re-flows the survivors unpredictably.
COLLAPSED = 0.0001


#: Control rows the HUD prebuilds. A mode names one; both are never up at once.
CONTROLS = ("run", "trace")


@dataclass(frozen=True)
class Mode:
    label: str
    panes: dict[str, tuple[str, ...]]
    weights: tuple[float, float, float]
    #: Panes the operator can toggle off within this mode.
    optional: frozenset = field(default_factory=frozenset)
    #: Which prebuilt control row the HUD shows. Run controls act on a live
    #: process, which a mode reading a finished run has nothing to do with.
    controls: str = "run"


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
            "left":   ("pane_flow_history",),
            "center": ("pane_flow_regs", "pane_flow"),
            "right":  ("pane_flow_mem", "pane_flow_tls"),
        },
        weights=(0.26, 0.34, 0.40),
        optional=frozenset({"pane_flow_regs", "pane_flow",
                            "pane_flow_mem", "pane_flow_tls"}),
        controls="trace",
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

#: Share of its column's height a pane takes, normalised across the panes
#: actually visible in the current mode.
PANE_ROW_WEIGHT = {
    "pane_regs":        0.72,
    "pane_breakpoints": 0.28,
    "pane_flow_history": 1.00,
    "pane_disasm":      0.45,
    "pane_memory":      0.35,
    "pane_backtrace":   0.20,
    "pane_flow_regs":   0.60,
    "pane_flow":        0.40,
    "pane_flow_mem":    0.30,
    "pane_flow_tls":    0.70,
    "pane_threads":     0.25,
    "pane_tls":         0.55,
    "pane_watch":       0.20,
    "pane_log":         1.00,
}


def _derive_pane_column() -> dict[str, str]:
    """Map each pane to its one column, rejecting panes that move between modes.

    A pane that changed column across modes could not be a single widget
    instance — it would have to be re-parented on every switch, which is the
    design this shell exists to avoid.
    """
    out: dict[str, str] = {}
    for name, mode in MODES.items():
        for col in COLUMNS:
            for pane in mode.panes[col]:
                if out.setdefault(pane, col) != col:
                    raise ValueError(
                        f"{pane} is in {out[pane]} but {name} puts it in {col}")
    return out


PANE_COLUMN = _derive_pane_column()


def _reject_unshown_optionals() -> None:
    """A toggle for a pane the mode never shows is a control that does nothing."""
    for name, mode in MODES.items():
        shown = {p for col in COLUMNS for p in mode.panes[col]}
        stray = mode.optional - shown
        if stray:
            raise ValueError(f"{name} makes unshown panes optional: {stray}")
        if mode.controls not in CONTROLS:
            raise ValueError(f"{name} wants unknown control row {mode.controls}")


_reject_unshown_optionals()


def panes_in_column(col: str) -> list[str]:
    """Every pane the shell builds into `col`, in build order."""
    return [p for p in PANES if PANE_COLUMN[p] == col]


def row_weights(mode: str, col: str, hidden=()) -> dict[str, float]:
    """Normalised height share per visible pane in `col`.

    Renormalising over the survivors is what makes a toggled-off pane give its
    pixels away instead of leaving a gap.
    """
    hidden = set(hidden)
    visible = [p for p in MODES[mode].panes[col] if p not in hidden]
    total = sum(PANE_ROW_WEIGHT[p] for p in visible)
    if not total:
        return {}
    return {p: PANE_ROW_WEIGHT[p] / total for p in visible}


def col_weights(mode: str) -> tuple[float, float, float]:
    return MODES[mode].weights


def visible_panes(mode: str, hidden=()) -> list[str]:
    """Panes on screen in `mode`, ordered left column to right."""
    hidden = set(hidden)
    m = MODES[mode]
    return [p for col in COLUMNS for p in m.panes[col] if p not in hidden]


def column_of(mode: str, pane: str) -> str | None:
    for col in COLUMNS:
        if pane in MODES[mode].panes[col]:
            return col
    return None
