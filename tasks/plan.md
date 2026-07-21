# Implementation Plan: GUI v3.1 — Flow history, TLS on hit, selectable text

## Overview

Five gaps in the v3 mode shell (`gui/app.py`, `gui/modes.py`):

1. No pane text can be selected — an RVA on screen cannot be copied into the flow box.
2. Step mode's TLS pane is always `(no tls)` — nothing sends `tls` on a stop.
3. Trace mode's flow pane is empty — `_populate_flow` is a no-op stub; the pulled
   CSV is never read.
4. Flow runs are not kept — each run overwrites the last with no way back.
5. Trace mode carries a Watch pane that does not belong there.

Target trace mode: **left = flow run history, center = registers for the
selected step, right = flow trace rows** (plus its existing control bar).

## Current state (verified)

| Fact | Location |
|---|---|
| `flow` writes `/data/local/tmp/<lib>_0x<rva>_flow.csv`, cols `step,va,rva[,x0..x29,lr,sp,pc,pstate][,mem_addr,mem_value]` | [cli/repl.go:1141](cli/repl.go:1141), [cli/repl.go:1149](cli/repl.go:1149) |
| GUI pulls the CSV into `gui/data/` and stops there | [app.py:898](gui/app.py:898) |
| `_populate_flow` / `_populate_watch` are empty stubs | [app.py:518](gui/app.py:518) |
| `tls` is sent only from a thread click, never on a hit | [app.py:364](gui/app.py:364), [app.py:576](gui/app.py:576) |
| `parse_tls` runs on one poll batch, so a dump split across frames is lost | [app.py:890](gui/app.py:890) |
| Panes are `dpg.add_text`, which has no selection model | [app.py:1034](gui/app.py:1034) |
| A pane lives in exactly one column across all modes (enforced) | [modes.py:121](gui/modes.py:121) |

## Architecture Decisions

- **`pane_flow` moves center → right; two new panes are added** (`pane_flow_history`
  left, `pane_flow_regs` center). `pane_regs` cannot be reused in trace mode's
  center column — `_derive_pane_column` rejects a pane that changes column, and
  that invariant is what lets the shell never re-parent a widget. A second
  registers-shaped pane is the cheap side of that trade.
- **Center regs in trace mode show the *selected step's* registers from the CSV**,
  not live registers — a flow trace is a timeline, and live regs are already one
  keypress away in step mode. When the run was captured without `--regs`, the
  pane says so and falls back to live registers.
- **History is a session list seeded from `gui/data/*.csv` at startup.** The CSVs
  are already on disk; scanning them costs one `glob` and makes history survive a
  GUI restart without a new persistence format.
- **Text selection via a per-pane TEXT toggle** that swaps the colored view for a
  readonly multiline `input_text`. DPG gives that widget real drag-select,
  double-click-word, and Ctrl+C; colored `add_text` rows never will. Colour is the
  default, selection is one click away, and `gui/textdump.py` already produces the
  exact text each pane needs.
- **Flow parsing lives in `gui/flowtrace.py`**, DPG-free, like `parse.py` — so the
  CSV contract is unit-testable without a window server.

## Dependency graph

```
gui/flowtrace.py (FlowRun, parse_flow_csv)     modes.py trace reshape
        │                                              │
        └──────────────┬───────────────────────────────┘
                       │
              app.py pane builders (history, flow_regs)
                       │
        ┌──────────────┼───────────────┐
        │              │               │
   _populate_flow  history pane   flow_regs pane
        │
   row click → selected step ──────────┘

TLS-on-hit and text-select mode are independent of all of the above.
```

## Task List

### Phase 1: Foundation

---

## Task 1: `gui/flowtrace.py` — flow run model and CSV parser

**Description:** A DPG-free module that turns a pulled flow CSV into a `FlowRun`
(label, rva, csv path, mtime, column names, rows) and back into text for copy.
Handles all three column shapes the Go writer emits (bare, `--regs`, `--mem`).

**Acceptance criteria:**
- [ ] `parse_flow_csv(path) -> FlowRun` reads `step,va,rva[,regs][,mem_*]`, exposes
      `has_regs`, `has_mem`, `steps`, and `regs_at(step) -> list[(name, value)]`
- [ ] `rva` and run label derive from the filename (`<lib>_0x<rva>_flow.csv`) with
      the CSV's own first row as the fallback
- [ ] Malformed/short rows are skipped, not fatal; an empty CSV yields `steps == 0`
- [ ] `flow_to_text(run)` returns copyable text

**Verification:**
- [ ] `python3 -m pytest gui/tests/unit/test_flowtrace.py -q`
- [ ] Fixture: the real `gui/data/libloader_0x1c6f68_flow.csv`

**Dependencies:** None · **Files:** `gui/flowtrace.py`, `gui/tests/unit/test_flowtrace.py` · **Scope:** S

---

## Task 2: Trace mode reshape in `modes.py`

**Description:** Add `pane_flow_history` (left) and `pane_flow_regs` (center),
move `pane_flow` to right, drop `pane_watch` from trace, and set the three column
weights against the measured minimums.

**Acceptance criteria:**
- [ ] `MODES["trace"]` is `left=(pane_flow_history,) center=(pane_flow_regs,) right=(pane_flow,)`
- [ ] `pane_watch` still appears in `inspect` and in no other mode
- [ ] `MIN_COL_FRAC` and `PANE_ROW_WEIGHT` cover both new panes; each mode's
      weights clear every visible pane's `MIN_COL_FRAC`
- [ ] `_derive_pane_column()` still builds (no pane in two columns)

**Verification:**
- [ ] `python3 -m pytest gui/tests/unit/test_modes.py gui/tests/unit/test_layout.py -q`
- [ ] Add a test asserting every mode's per-column weight ≥ max `MIN_COL_FRAC` of
      its visible panes

**Dependencies:** None · **Files:** `gui/modes.py`, `gui/tests/unit/test_modes.py` · **Scope:** S

---

## Task 3: Build the two new panes

**Description:** Builders, tags, chrome heights, and painter stubs so the shell
constructs and switches cleanly before any flow data exists.

**Acceptance criteria:**
- [ ] `TAG` entries + `_build_pane_flow_history` / `_build_pane_flow_regs`
      registered in `_PANE_BUILDERS`, `_PANE_PAINTERS`, `_PANE_CHROME`
- [ ] Empty state reads `(no flow runs)` / `(no step selected)`
- [ ] F2 shows exactly three panes, no stranded controls

**Verification:**
- [ ] `python3 -m pytest gui/tests -q`
- [ ] `python3 -m pytest gui/tests/smoke -q` (desktop; needs a window server)

**Dependencies:** 2 · **Files:** `gui/app.py` · **Scope:** S

---

### Checkpoint: Foundation
- [ ] Full suite green, smoke layout builds
- [ ] F1–F4 switch without a DPG warning; trace mode shows 3 placeholder panes

---

### Phase 2: Flow vertical slice

---

## Task 4: Render the selected flow run

**Description:** Parse the pulled CSV into history and paint its rows — step, VA,
RVA, and the `--mem` columns when present. Clicking a row selects that step.

**Acceptance criteria:**
- [ ] `_handle_flow_csv` parses the pulled file, appends a `FlowRun`, selects it,
      and marks `pane_flow` / `pane_flow_history` / `pane_flow_regs` dirty
- [ ] Rows render as `#step  0xva  +0xrva` with the selected row highlighted
- [ ] Row click sets `_flow_step` and marks `pane_flow_regs` dirty
- [ ] Runs over ~2000 steps stay responsive (cap rendered rows, note the cap)

**Verification:**
- [ ] Unit: painter fed a fixture run emits one row per step (existing `_rows`
      helper in `gui/tests/unit/test_repaint.py`)
- [ ] Manual: `flow 0x1c6f68 --regs` on device → rows appear on completion

**Dependencies:** 1, 3 · **Files:** `gui/app.py`, `gui/tests/unit/test_repaint.py` · **Scope:** M

---

## Task 5: Flow run history pane

**Description:** Left pane lists every run this session plus every CSV already in
`gui/data/`, newest first; clicking one makes it the selected run.

**Acceptance criteria:**
- [ ] Startup seeds history from `glob(gui/data/*_flow.csv)` sorted by mtime
- [ ] Each entry shows RVA, step count, and time; the selected one is marked
- [ ] Click switches the flow and regs panes to that run and resets the step
- [ ] A re-run of the same RVA adds a new entry, never overwrites

**Verification:**
- [ ] Unit: seeding a temp dir with two CSVs yields two ordered entries
- [ ] Manual: restart the GUI → the previous run is still listed and openable

**Dependencies:** 4 · **Files:** `gui/app.py`, `gui/flowtrace.py`, `gui/tests/unit/test_flowtrace.py` · **Scope:** S

---

## Task 6: Per-step registers pane

**Description:** Center pane shows the selected step's registers from the CSV,
reusing the register colouring already in `_populate_regs`.

**Acceptance criteria:**
- [ ] Shows `x0..x29, lr, sp, pc, pstate` for the selected step
- [ ] Values that changed from the previous step are highlighted
- [ ] Run without `--regs`: pane says `(run captured without --regs)` and shows
      live registers instead
- [ ] No run selected: `(no step selected)`

**Verification:**
- [ ] Unit: fixture run, step 2 → pane rows match CSV row 2
- [ ] Manual: click through steps, watch the highlight track real changes

**Dependencies:** 4 · **Files:** `gui/app.py` · **Scope:** S

---

### Checkpoint: Flow slice
- [ ] Device run: flow completes → CSV pulled → history entry → rows → click a
      step → registers update
- [ ] Full suite green

---

### Phase 3: TLS and copy

---

## Task 7: TLS on every stop

**Description:** Ask for `tls` on each hit and make the parse survive a dump split
across poll batches.

**Acceptance criteria:**
- [ ] `_auto_fetch_on_hit` sends `tls`
- [ ] `parse_tls` runs over a rolling window of recent output, not one batch, so a
      header and its slots arriving in different frames still form one dump
- [ ] The window yields the newest dump after a thread switch
- [ ] Error replies (`Not stopped on a thread.`) leave the previous dump alone

**Verification:**
- [ ] Unit: feed a dump two lines at a time → one complete `TlsDump`
      (`gui/tests/unit/test_tls_parse.py`)
- [ ] Manual: hit a breakpoint in step mode → TLS pane fills without clicking a thread

**Dependencies:** None · **Files:** `gui/app.py`, `gui/tests/unit/test_tls_parse.py` · **Scope:** S

---

## Task 8: Selectable text mode per pane

**Description:** A `TEXT` toggle on each pane header swaps the colored rows for a
readonly multiline `input_text` carrying the pane's `textdump` output — real
drag-select, word-select, and Ctrl+C.

**Acceptance criteria:**
- [ ] Every pane with a `_COPY_SOURCES` entry gets the toggle; toggling repaints
      that pane only and survives a mode switch
- [ ] In text mode a mouse drag selects an RVA and Ctrl+C copies exactly it
- [ ] `_COPY_SOURCES` gains flow and flow-history entries so right-click copy and
      "Copy everything" cover the new panes

**Verification:**
- [ ] `python3 -m pytest gui/tests/unit/test_textdump.py gui/tests/smoke -q`
- [ ] Manual: TEXT on disassembly → select `0x1fb078` → Ctrl+C → paste into Flow Addr

**Dependencies:** 3 · **Files:** `gui/app.py`, `gui/textdump.py` · **Scope:** M

---

## Task 9: Click an address to reuse it

**Description:** Clicking a disassembly or flow row copies its address to the
clipboard and drops the RVA into the Flow `Addr` box — the one-click version of
Task 8's general path.

**Acceptance criteria:**
- [ ] Disasm rows are clickable; click copies the full line's address and logs it
- [ ] The Flow `Addr` input is filled with the RVA
- [ ] Nothing about the existing colouring changes

**Verification:**
- [ ] `python3 -m pytest gui/tests/smoke -q`
- [ ] Manual: click a disasm row → `Run Flow` is one click away

**Dependencies:** 4, 8 · **Files:** `gui/app.py` · **Scope:** S

---

### Checkpoint: Complete
- [ ] `python3 -m pytest gui/tests -q` green
- [ ] All five reported gaps demonstrated fixed on device
- [ ] `SPEC_gui_v3.md` updated with the trace-mode layout change

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| A second registers pane duplicates render logic | Med | One shared `_render_regs(tag, rows)`; both painters call it |
| Large flow runs (10k steps) freeze the frame loop | High | Cap rendered rows, page the rest; parse once at pull, not per frame |
| Readonly `input_text` loses per-token colour | Med | Colour is the default; text mode is opt-in per pane |
| Rolling-window TLS re-parse costs per frame | Low | Only re-parse when a new line matches the TLS shapes |
| `tls` on every hit slows the step loop | Med | Measure; if it bites, fetch only when step mode is active |
| Trace-mode column change breaks pane-column invariant tests | Low | Task 2 lands with its own test before any app.py change |

## Open Questions

- Center pane in trace mode: per-step CSV registers (planned) or always-live
  registers? Planned answer assumes per-step with a live fallback.
- Should the history pane offer delete/clear, or is a plain list enough for now?
