# SPEC — eDBG GUI v3 (Mode Shell)

> Supersedes the **Layout Model** section of `SPEC_gui_v2.md` only. v2's
> structured NDJSON event stream, atomic stops, diffed repaint, and teardown
> contracts stand unchanged and are dependencies of this spec, not alternatives
> to it. v2 tasks 0.0 / 0.0b / 0.0c are already landed.

## Problem Statement

**How might we give a debugger operator one stable layout where every fact they
need mid-step-loop is on screen at once — without floating windows that get lost,
and without panes starved for height?**

## Recommended Direction

Replace the seven dockable floating windows with a **fixed mode shell**: one
primary window, a four-entry mode bar, and a single persistent pane pool.

A mode is **data, not structure** — `{visible_panes, col_ratios}`. Every pane
widget is created exactly once at startup and never re-parented. Switching modes
calls `configure_item(show=…)` and rewrites column ratios. Nothing is built,
destroyed, or moved.

This matters because a debugger pane is a live instrument, not a document. The
naive reading of "four tabs" puts Memory in its own tab, which means stepping in
Tab 1 while a watched buffer mutates in Tab 4 — an invisible blind spot at the
exact moment visibility matters. Under a pane pool, **the same Memory pane appears
in more than one mode at different widths**: narrow beside the disassembly while
stepping, wide when inspecting. One instance, one dirty flag, one update path.

The mode bar renders as tabs. It is not `dpg.tab` — DPG items have exactly one
parent, so genuine tab membership would force either `move_item` on every switch
(loses scroll state, re-parent bugs) or duplicated widget sets (breaks TAG
uniqueness, doubles the dirty-flag fanout). Show/hide is strictly less code than
both and preserves the existing headless layout tests.

## Modes

| Key | Mode | LEFT | CENTER | RIGHT |
|---|---|---|---|---|
| F1 | **Step** | 0.28 Registers ┄ Breakpoints | 0.40 Disasm ┄ Memory ┄ Backtrace | 0.32 Threads ┄ TLS |
| F2 | **Trace** | 0.24 Flow history | 0.28 Step registers | 0.48 Flow trace |
| F3 | **Inspect** | 0.26 Registers | 0.42 Memory | 0.32 Threads ┄ TLS ┄ Watch |
| F4 | **Log** | — | 1.00 Log / transcript | — |

`┄` = vertical stack inside one column. `—` = column collapsed.

### Column fractions are measured, not chosen

Widths come from `dpg.get_text_size` on the widest line each `_populate_*`
emits, at `FONT_SIZE=15` and the 1520px default viewport:

| pane | px | min col frac |
|---|---|---|
| memory, 16 bytes/line | 539 | 0.355 |
| disasm | 478 | 0.314 |
| tls slot | 471 | 0.310 |
| registers, symbol + deref | 423 | 0.278 |
| backtrace | 335 | 0.220 |
| threads | 123 | 0.081 |

Two consequences, both of which overturned the first draft of this table:

- **Memory cannot live in a side column.** At 0.22 it has 310 usable px against
  539 needed. It goes in CENTER stacked under Disasm, where 0.40 yields 608px
  and the full 16-bytes-per-line format fits without narrowing to 8.
- **Registers and TLS set their columns' floors** at 0.278 and 0.310. LEFT at
  0.22 would clip every register line carrying a symbol and a deref — the
  annotations that make the pane worth having.

Any future mode must satisfy these floors for the panes it shows.

Persistent chrome, outside the mode shell, never hidden by a mode switch:

```
┌──────────────────────────────────────────────────────────────┐
│ HUD: ● state │ pkg/lib/pid/tid/hit │ connect │ run controls   │  fixed h
├──────────────────────────────────────────────────────────────┤
│ [Step] [Trace] [Inspect] [Log]                                │  mode bar
├────────────┬────────────────────────────┬────────────────────┤
│ LEFT       │ CENTER                     │ RIGHT              │  stretch
│            │                            │                    │
├────────────┴────────────────────────────┴────────────────────┤
│ (eDBG) ▸ command bar                                          │  fixed h
└──────────────────────────────────────────────────────────────┘
```

Region splits are the vertical borders of a
`dpg.table(resizable=True, borders_innerV=True, policy=mvTable_SizingStretchProp)`.
Vertical cramping is solved by mode selection, not by row splitters — DPG tables
do not resize rows.

**F12 zoom** — the focused pane fills the viewport; F12 again restores. Zoom is
orthogonal to mode and does not change the active mode.

Existing F5 / F10 / F11 bindings ([app.py:1050](gui/app.py:1050)) are unchanged.

## Core Acceptance Criteria

Inherits all of `SPEC_gui_v2.md` §"Core Acceptance Criteria". Adds:

1. **Single pane instance.** Every pane widget is created exactly once.
   - AC: `test_tags_are_unique` and `test_all_tag_widgets_exist` pass unmodified;
     no `move_item` call exists on the mode-switch path.
2. **Hidden panes do not repaint.** A pane invisible in the active mode performs
   zero DPG mutations on a stop.
   - AC: instrument `_populate_*`; run a stop in Step mode; assert Flow, Watch,
     Memory-wide and Log panes recorded zero mutations.
3. **Mode switch is O(1) in pane content.** Switching modes issues only
   `configure_item(show=…)` and column-ratio writes — never a repopulate.
   - AC: switch modes with no intervening stop; assert zero row-level mutations.
4. **Show-on-reveal correctness.** A pane hidden across N stops renders the
   *current* state the instant its mode is selected — it does not show stale data
   and does not require another stop to catch up.
   - AC: stop → switch to Inspect → Memory content equals the content Step mode
     would have shown. This is the load-bearing test; it is the failure mode
     that hidden panes create.
5. **Layout is unbreakable.** `configure_app(docking=False)`,
   `set_primary_window(root, True)`. No window can be dragged, floated, closed,
   or lost. Relaunch is byte-identical.
   - AC: `_DOCK_FRAC`, `_compute_dock_layout`, `_apply_dock_layout`
     ([app.py:794-832](gui/app.py:794)) are deleted; grep confirms no references.
6. **Modes are data.** Adding a mode is a dict entry, not a code path.
   - AC: the mode table is importable and assertable without `dearpygui`.
7. **TLS pane.** The right column renders per-thread TLS slots via
   `utils.Classify` / `utils.AsciiPreview` ([utils/tls_parse.go](utils/tls_parse.go)),
   colored by class (stack / heap / anon / string), refreshed on thread select.

## Key Assumptions to Validate

- [ ] **Four modes is the right number.** Test: instrument mode-switch counts over
      a week of real sessions. If Step dominates >95%, the honest answer is one
      screen plus F12 zoom, and three modes should be deleted.
- [x] ~~**Memory is readable at Step-mode width.**~~ **FALSIFIED.** Measured 539px
      needed against 310px available at 0.22. Memory moved to CENTER under Disasm;
      column fractions rebalanced to 0.28 / 0.40 / 0.32. The probe additionally
      falsified the LEFT and RIGHT fractions — see "Column fractions are measured".
- [ ] **TLS classification is fast enough per stop.** Test: time
      `Classify` + `PeekPtrAnnotate` across a full TLS block on a real device;
      must fit the stop budget without a round-trip.
- [ ] **Hidden-pane suppression actually saves time.** Test: measure stop-to-paint
      with all panes visible vs. Step mode only. If the delta is noise, the
      cramping problem was never a performance problem and modes are purely
      ergonomic — still fine, but stop claiming otherwise.

## MVP Scope

**In** — one primary window; docking deleted; the 3-column stretch table; the
four modes above wired to F1–F4 and a clickable mode bar; single pane pool with
show/hide; dirty flags gating `_populate_*` (v2 defect 4/5) since modes are
meaningless without them; the TLS right column.

**Out of MVP** — F12 zoom, timeline scrub, register diff highlighting,
per-user custom modes.

**Ships against the current text-scraping backend.** The v2 event stream is a
dependency for correctness under load, not for this layout, and lands separately.

## Not Doing (and Why)

- **Real `dpg.tab` containers** — one parent per item makes cross-mode pane
  sharing impossible; the workarounds cost more than show/hide.
- **A dedicated Memory tab** — it is the blind spot this spec exists to remove.
- **User-draggable / savable layouts** — "docking is broken and windows get lost"
  is a stated driver. Configurability reintroduces it.
- **Collapsible sections instead of modes** — layout drifts as you collapse and
  re-expand; same instability by another door.
- **Second detached OS window** — reintroduces the lost-window problem.
- **Timeline scrub (v2 AC 9)** — real, but orthogonal. Not gated on layout.
- **Per-mode column ratios saved across launches** — ratios are constants until
  a session proves otherwise.

### Trace mode reads left to right

A flow run is finished data on disk, not live process state, so Trace mode is
driven by a selection — which run, which step — rather than by a stop:

1. **Flow history** — every run this session, plus every CSV already pulled
   into `gui/data`, so history survives a restart. A re-run of an RVA is a new
   entry; comparing a trace against the one it repeats is the point of keeping
   history.
2. **Step registers** — the selected step's registers *from the CSV*, with the
   values that moved since the previous step marked. A run captured without
   `--regs` says so and falls back to live registers.
3. **Flow trace** — the rows, windowed around the selected step. Every row is a
   widget DPG lays out each frame, so a 10k-step run renders `_FLOW_MAX_ROWS`
   of itself, not all of it.

Live Registers cannot be the centre pane: `_derive_pane_column` rejects a pane
that changes column between modes, and never re-parenting a widget is the
invariant the shell is built on. Step registers are a second pane, not a move.

Watch left Trace mode — a watch list has nothing to say about a finished run.

## Open Questions

- Log mode is full-width single-pane — is that a mode at all, or should it be
  F12 zoom on a Log pane that lives in Inspect's right column?
- ~~Does TLS refresh on thread select, on every stop, or on demand?~~ Every
  stop. On thread select alone, a plain hit showed `(no tls)`, which reads as
  "this thread has none". The dump spans more polls than one, so the parse runs
  over a rolling window of output rather than a single batch.
- ~~Does Trace mode need Registers?~~ Yes, but per-step ones from the CSV.
- Where does the breakpoint table live? It is absent from all four modes above.
