# Implementation Plan: eDBG GUI v3 — Mode Shell

Implements [SPEC_gui_v3.md](SPEC_gui_v3.md).

## Overview

Replace seven dockable floating windows with a fixed shell: one primary window,
a four-entry mode bar (Step / Trace / Inspect / Log), one persistent pane pool.
A mode is data — `{visible_panes, col_ratios}` — applied via `configure_item(show=…)`
and column-weight writes. No pane is ever created twice, re-parented, or rebuilt
on a mode switch.

Modes only pay off if hidden panes stop repainting, so dirty-flag gating
(v2 defects 4/5) lands here as a dependency, not a follow-up.

## Architecture Decisions

- **Mode registry is pure data in `gui/modes.py`** — no `dearpygui` import, so it
  is testable headless (spec AC 6). Adding a mode is a dict entry.
- **Panes keep their existing `pane_*` tags** and are built directly inside the
  shell's column containers. Show/hide targets a per-pane *section* wrapper
  (header + controls + pane), not the pane child-window alone — otherwise the
  Memory address bar stays visible when Memory is hidden.
- **Column ratios via `dpg.configure_item(col_tag, init_width_or_weight=…)`** on a
  `mvTable_SizingStretchProp` table. A collapsed column gets weight ~0.0001, not
  `show=False` — hiding a table column re-flows the remaining ones unpredictably.
- **Dirty flags live on the session-facing side in `gui/dirty.py`** (pure, no DPG).
  `_populate_*` is gated by `dirty AND visible`. The flag clears **only on actual
  paint**, which is what makes repaint-on-reveal (spec AC 4) fall out for free
  rather than needing separate bookkeeping.
- **TLS is parsed from the existing `tls` REPL command** ([repl.go:602](cli/repl.go:602)),
  which already emits classified slots. No Go change.
- **Breakpoints resolved** (spec open question): lives in the LEFT column under
  Registers in Step and Trace modes. Short list, and it belongs next to the
  register/PC context, not in its own mode.

## Task List

### Phase 0: De-risk

- [x] **Task 1: Memory-width probe — DONE, assumption falsified.**
  Measured every pane's widest line with `dpg.get_text_size` at `FONT_SIZE=15`,
  1520px viewport.
  - Memory 16B/line needs **539px** (0.355) against 310px available at 0.22.
    Memory moved to CENTER under Disasm, where 0.40 gives it 608px.
  - The probe also falsified two column fractions from the spec's first draft:
    registers need **0.278** (not 0.22), TLS slots need **0.310** (not 0.28).
  - Columns rebalanced to **0.28 / 0.40 / 0.32**; see SPEC_gui_v3.md
    "Column fractions are measured, not chosen".

### Phase 1: Foundation (headless, no DPG)

- [ ] **Task 2: `gui/modes.py` — mode registry.** `MODES` dict, `PANES` list,
  `visible_panes(mode)`, `col_weights(mode)`, `MODE_KEYS` (F1–F4).
  - AC: `import gui.modes` succeeds with `dearpygui` uninstalled.
  - AC: every pane named in any mode exists in `PANES`; no typo'd pane can enter.
  - AC: every mode's weights are three positive floats.
  - Verify: `pytest gui/tests/unit/test_modes.py`.
  - Deps: 1. Scope: S.

- [ ] **Task 3: `gui/dirty.py` — dirty-flag core.** `DirtySet` with `mark(pane)`,
  `take(pane)` (test-and-clear), `pending()`.
  - AC: `take` on a clean pane returns False and performs no clear.
  - AC: a pane marked N times and never taken is still dirty — the
    hidden-across-N-stops case.
  - Verify: `pytest gui/tests/unit/test_dirty.py`.
  - Deps: None. Scope: S.

- [ ] **Task 4: TLS parsing.** `TlsSlot` dataclass + `parse_tls(lines)` in
  `gui/parse.py`; `last_tls` on `EdbgSession`; `tls_to_text` in `gui/textdump.py`.
  - AC: parses the real format from [repl.go:666-688](cli/repl.go:666) —
    header (`tls tid= map= 0x…-0x…`), `base=/len=`, and slot rows both with and
    without the trailing annotation.
  - AC: all six classes (string/code/stack/heap/mapped/junk) round-trip.
  - AC: a truncated/partial dump parses to the slots received, never raises.
  - Verify: `pytest gui/tests/unit/test_tls_parse.py`.
  - Deps: None. Scope: S.

### Checkpoint: Foundation
- [ ] `pytest gui/tests/unit` green.
- [ ] `gui.modes`, `gui.dirty`, `gui.parse` all import with no `dearpygui`.

### Phase 2: The shell

- [ ] **Task 5: Fixed shell replaces docking.** `configure_app(docking=False)`,
  one root window via `set_primary_window`, HUD row, mode bar, 3-column
  stretch table, command bar. Panes built into columns. Delete `_DOCK_FRAC`,
  `_compute_dock_layout`, `_apply_dock_layout`, `set_viewport_resize_callback`.
  - AC: all seven old `win_*` windows are gone; `TAG` has no orphan entries.
  - AC: grep finds zero references to the three deleted dock helpers.
  - AC: every pane widget is created exactly once (existing `test_tags_are_unique`
    and `test_all_tag_widgets_exist` pass unmodified).
  - Verify: rewritten `gui/tests/unit/test_layout.py`; app launches.
  - Deps: 2. Scope: M — [gui/app.py](gui/app.py) only.

- [ ] **Task 6: Mode switching.** Mode bar buttons + F1–F4 → `_set_mode(name)`:
  show/hide section wrappers, write column weights, highlight active button.
  - AC: switching modes with no intervening stop causes zero row-level pane
    mutations (spec AC 3).
  - AC: no `move_item` on the switch path (spec AC 1).
  - AC: F5/F10/F11 still bound and unaffected.
  - Verify: `test_layout.py::test_mode_switch_only_toggles_visibility`.
  - Deps: 5. Scope: S.

- [ ] **Task 7: Dirty-gated repaint + repaint-on-reveal.** Every `_populate_*`
  early-returns unless `dirty AND visible`; parse handlers call `mark()` instead
  of populating; `_set_mode` repaints newly-revealed dirty panes.
  - AC: a stop in Step mode records zero mutations in Flow/Watch/Log panes
    (spec AC 2).
  - AC: stop → switch to Inspect → Memory content equals what Step would have
    shown (spec AC 4 — the load-bearing test).
  - AC: an idle frame with no new output performs zero DPG mutations.
  - Verify: `pytest gui/tests/unit/test_repaint.py`.
  - Deps: 3, 6. Scope: M.

### Checkpoint: Shell
- [ ] Full suite green, including `test_command_contract` and `test_hit_atomicity`.
- [ ] App launches; all four modes render; layout identical across relaunches.

### Phase 3: TLS column

- [ ] **Task 8: TLS pane.** Right-column TLS section under Threads;
  `_populate_tls` colored by class; clicking a thread emits `thread <tid>` then
  `tls`; TLS added to `_COPY_SOURCES` and the Edit menu.
  - AC: TLS renders in Step and Inspect modes, absent in Trace and Log.
  - AC: the emitted `tls` command passes `test_command_contract`.
  - AC: TLS repaints only when dirty and visible.
  - Verify: `pytest gui/tests/unit`; manual check on a device.
  - Deps: 4, 7. Scope: M.

### Checkpoint: Complete
- [ ] All spec AC 1–7 demonstrated.
- [ ] `adb shell ps -A | grep '[e]DBG'` empty after quit (v2 AC 7b, unregressed).

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Hexdump unreadable at 22% width — the dual-width claim is hollow | High | Task 1 probes it first; mode table changes before code is written |
| DPG table column weights don't re-flow live | High | Collapse via tiny weight, never `show=False`; verified in Task 5 |
| Hidden panes still repaint → modes buy nothing | High | Task 7 asserts zero mutations, not "looks faster" |
| Stale-on-reveal | High | Flag clears only on paint; Task 7 AC 2 is the explicit test |
| `dearpygui` segfaults on bare import on this macOS host | Medium | All logic-bearing modules stay DPG-free and headless-tested |
| TLS dump too slow per stop | Medium | TLS is on-demand (thread select), not auto-fetched on stop |

## Open Questions

- Trace mode: does Registers earn its column during a 10k-step flow, or is it noise?
- Log mode as a mode vs. an F12 zoom on a Log pane living in Inspect.
- TLS refresh cadence — on thread select only (planned) vs. every stop.
