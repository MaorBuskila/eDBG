# Implementation Plan: eDBG GUI v4 — Trace Mode

Implements [SPEC_gui_v4.md](SPEC_gui_v4.md).

## Overview

Turn Trace mode into a browser over finished `flow` runs: one tree where runs are
folders and steps are files, three toggleable detail panes showing what that step
captured, and data toggles occupying the screen row the run controls own in every
other mode. Trace stops reading live-process state entirely. Per-step TLS becomes
real captured data via a new `flow --tls[=N]` sidecar CSV.

## Architecture Decisions

- **Optional panes belong in `gui/modes.py`, not in `_set_mode`.** A mode already
  declares its panes; it grows an `optional` set and a `controls` row name. The
  app holds only the on/off state. Visibility stays `mode ∧ toggle`, both halves
  assertable with `dearpygui` uninstalled.
- **Height renormalisation is the existing `row_weights`, taking a `hidden` set.**
  Turning TLS off must give its pixels to Memory, and that is arithmetic already
  living in a pure module — no new mechanism.
- **The control row swaps by show/hide of two prebuilt groups**, the same trick
  the pane pool uses. Nothing is built or re-parented on a mode switch (v3 AC 1).
- **Steps become children of the run's `tree_node`, and `_FLOW_MAX_ROWS`
  windowing moves with them.** The window is what makes a 10k-step run renderable
  at all; it is not dropped because the container changed.
- **TLS is a sidecar CSV, not new columns.** Slot count is per-run and per-step
  row width would stop being fixed; `glob("*_flow.csv")` must also keep ignoring
  it, which `<stem>_flow_tls.csv` does for free.
- **Classification uses one maps snapshot from flow entry, annotations are
  memoised per value.** A flow traces one function; its mappings do not move.
  Re-reading maps or re-annotating per step is what would make `--tls` unusable.
- **Run metadata rides a leading `#` row.** Old CSVs have none and must keep
  parsing — the reader skips a leading `#`, so absence means `symbol == ""`.
- **The flow launch controls move into the FLOW HISTORY pane header.** They
  create the entries that pane lists, and the run-control row they used to sit
  beside is gone in this mode.

## Dependency Graph

```
T1 modes registry ──┬─→ T5 history tree ──┬─→ T7 control row + toggles
                    │                     │
T2 flowtrace reader ┴─→ T6 detail panes ──┘
                    │
T3 Go --tls sidecar ┴─→ T8 pull + launch flag
T4 Go metadata row ─┘
```

## Task List

### Phase 1: Pure foundations (headless, no `dearpygui`, no device)

- [x] **Task 1: Mode registry grows optional panes and a control row.** DONE.
  Add `pane_flow_mem` and `pane_flow_tls` to `PANES`, `MIN_COL_FRAC`, and
  `PANE_ROW_WEIGHT`. Give `Mode` two fields: `optional: frozenset[str]` and
  `controls: str` (`"run"` | `"trace"`). Rewrite the `trace` entry to
  LEFT `pane_flow_history` / CENTER `pane_flow_regs`, `pane_flow` /
  RIGHT `pane_flow_mem`, `pane_flow_tls`. `row_weights(mode, col, hidden=())`
  renormalises over survivors.
  - AC: `import gui.modes` still succeeds with `dearpygui` uninstalled.
  - AC: `row_weights("trace", "right", hidden={"pane_flow_tls"})` sums to 1.0 and
    contains only `pane_flow_mem`; hiding every pane in a column returns `{}`.
  - AC: every name in any mode's `optional` is also in that mode's `panes`.
  - AC: `_derive_pane_column` still passes — no pane changes column.
  - Verify: `pytest gui/tests/unit/test_modes.py`.
  - Files: `gui/modes.py`, `gui/tests/unit/test_modes.py`.
  - Deps: None. Scope: S.

- [x] **Task 2: Flow reader learns metadata, memory, and the TLS sidecar.** DONE.
  `parse_flow_csv` skips a leading `#` row and reads `symbol=` from it into
  `FlowRun.symbol`. New `parse_flow_tls_csv(path)` returns
  `{step: [FlowTlsSlot(slot, addr, value, cls, annot)]}`; `parse_flow_csv` picks
  up `<stem>_flow_tls.csv` when it exists. `FlowRun.tls_at(step)`,
  `FlowRun.mem_at(step)`, `FlowRun.has_tls`.
  - AC: an existing CSV with no `#` row and no sidecar parses byte-identically to
    today — `symbol == ""`, `has_tls is False`, `tls_at(n) == []`.
  - AC: a sidecar whose rows reference steps absent from the main CSV is ignored
    for those steps and never raises; an unparseable cell drops that row only.
  - AC: `discover_runs` still returns one entry per run — the sidecar never
    appears as a run of its own.
  - Verify: `pytest gui/tests/unit/test_flowtrace.py` with new fixtures under
    `gui/tests/fixtures/`.
  - Files: `gui/flowtrace.py`, `gui/tests/unit/test_flowtrace.py`,
    `gui/tests/fixtures/*.csv`.
  - Deps: None. Scope: S.

### Checkpoint: Foundations
- [x] `pytest gui/tests` green (404 passed).
- [x] `gui.modes` and `gui.flowtrace` both import with `dearpygui` uninstalled.
- [x] No GUI behaviour has changed yet — the two new panes are empty shells so
      the pane pool still builds.

### Phase 2: Capture side (Go)

- [x] **Task 3: `flow --tls [N]` writes a per-step TLS sidecar.** DONE.
  Parse `--tls` (bare = 32 slots, space-separated value like the existing
  `--over`, not `--tls=N`) in `HandleFlow`. At flow entry take one maps snapshot
  plus the thread's stack_and_tls range. Per step, read `N*8` bytes from SP,
  classify each slot against the snapshot, and write
  `step,slot,addr,value,class,annot` to `<lib>_0x<rva>_flow_tls.csv`. Class and
  annotation come from `utils.MemoResolver` so a repeated pointer is resolved
  once. Print the sidecar path on completion alongside the CSV path.
  - AC: `flow 0x1000` without `--tls` writes no sidecar and is byte-identical to
    today's output.
  - AC: a slot read that fails costs that step its slots and nothing more.
    **Amended from the first draft**, which had it write a fabricated
    `class=junk` row: inventing data to satisfy a row count is worse than a gap
    the reader can see. The run continues either way, which was the point.
  - AC: `N` is clamped to `[1, 256]`; `--tls 0` is rejected with the usage line.
  - AC: the row-building helper is pure over `(step, base, buf, resolve)` and is
    unit-tested off the hot path.
  - Verify: `make build`. The Go unit tests need cgo and Linux syscalls, so they
    cross-compile and run on the device:
    `GOOS=android GOARCH=arm64 CGO_ENABLED=1 CC=<ndk>/aarch64-linux-android29-clang go test -c -vet=off -o /tmp/x.test ./utils/`
    then `adb push` and run. **Nothing runs them on the Mac** — which is how
    `TestTlsClipDumpRange` came to be failing before this change touched it.
  - Files: `cli/repl.go`, `utils/flow_tls.go`, `utils/flow_tls_test.go`,
    `cli/repl_flow_test.go`.
  - Deps: None. Scope: M.

- [x] **Task 4: `flow` writes the run metadata row.** DONE.
  Before the column header, write one row `# lib=<lib>,rva=0x<rva>,symbol=<sym>`
  using `Process.GetSymbol(absolute)`. Empty `symbol=` when unresolved.
  - AC: the row is the first line and the column header still follows it.
  - AC: a symbol containing a comma or `=` does not corrupt the row (it is
    written as a single CSV field, not hand-formatted).
  - Verify: `go build ./...`; the fixture in Task 2 is generated from real output.
  - Files: `cli/repl.go`.
  - Deps: None (pairs with Task 2's reader). Scope: XS.

### Checkpoint: Capture
- [x] `make build` clean. (`go build ./...` on the Mac cannot work: the tree
      needs cgo and an NDK, so the host has no way to compile `utils.ParseStack`.)
- [ ] On a device: `flow <rva> --regs --mem X0 --tls` produces both CSVs; the
      main CSV opens in the *current* GUI unchanged (backward compatibility).
- [ ] Timing recorded for the same run with and without `--tls` — the assumption
      in SPEC_gui_v4 "Key Assumptions" is answered before the GUI depends on it.

### Phase 3: Trace mode (GUI)

- [ ] **Task 5: FLOW HISTORY becomes a run/step tree.**
  `_populate_flow_history` renders one `dpg.tree_node` per run
  (`<lib>+0x<rva>  <symbol>  N steps  HH:MM:SS`, selected run default-open) with
  windowed step children. Root click selects run + step 0; child click selects
  the step. Move the flow launch controls from `_build_pane_flow` into
  `_build_pane_flow_history`.
  - AC: selecting a step marks only `pane_flow`, `pane_flow_regs`,
    `pane_flow_mem`, `pane_flow_tls` dirty — never a live pane.
  - AC: a 10 000-step run creates `_FLOW_MAX_ROWS` children and one
    "showing steps X-Y of N" line.
  - AC: the tree repaints only when dirty and visible (v3 AC 2 unregressed).
  - Verify: `pytest gui/tests/unit/test_flow_panes.py gui/tests/unit/test_repaint.py`.
  - Files: `gui/app.py`, `gui/tests/unit/test_flow_panes.py`.
  - Deps: 1, 2. Scope: M.

- [ ] **Task 6: Step detail panes.**
  Delete the live-register fallback from `_populate_flow_regs` and
  `_flow_regs_to_text`. Build `pane_flow_mem` (probe address and value for the
  step, "(run captured without --mem)" otherwise) and `pane_flow_tls` (slots
  coloured by class, reusing `_TLS_CLASS_COLOR`). Every value in all three panes
  is clickable: copy to clipboard, addresses also into the flow address box.
  Register all three in `_COPY_SOURCES`, `_PANE_PAINTERS`, `_PANE_BUILDERS`,
  `_PANE_CHROME`, `TAG`, `_FLOW_PANES`.
  - AC: no `_session.last_` reference remains on any `_populate_flow_*` path.
  - AC: a run without `--regs` / `--mem` / `--tls` renders the matching
    "captured without" line in that pane and nothing else.
  - AC: clicking any value sends zero REPL commands
    (`test_command_contract` extended).
  - Verify: `pytest gui/tests/unit` — `test_tags_are_unique` and
    `test_all_tag_widgets_exist` pass unmodified.
  - Files: `gui/app.py`, `gui/tests/unit/test_flow_panes.py`,
    `gui/tests/unit/test_command_contract.py`.
  - Deps: 1, 2. Scope: M.

- [ ] **Task 7: Control-row swap and data toggles.**
  `_build_hud` builds the run-control group and a trace-toggle group as siblings.
  `_set_mode` shows the group named by `MODES[name].controls` and applies
  `mode ∧ toggle` visibility plus `row_weights(..., hidden=…)` heights. A toggle
  marks its pane dirty so revealing it repaints with the current selection.
  - AC: Trace shows no run-control button; Step / Inspect / Log show no toggle.
  - AC: toggling a pane off and on repaints it with the current step, not stale
    rows; toggling issues zero row-level mutations in other panes.
  - AC: F5 / F10 / F11 remain bound and unaffected in every mode.
  - Verify: `pytest gui/tests/unit/test_mode_switch.py gui/tests/unit/test_layout.py`.
  - Files: `gui/app.py`, `gui/tests/unit/test_mode_switch.py`,
    `gui/tests/unit/test_layout.py`.
  - Deps: 1, 5, 6. Scope: M.

- [ ] **Task 8: Launch and pull the TLS sidecar.**
  Add a `TLS` checkbox plus slot-count box to the launch controls; `_cb_flow`
  appends `--tls=<n>`. `_handle_flow_csv` pulls `<stem>_flow_tls.csv` alongside
  the main CSV, tolerating its absence.
  - AC: the emitted command matches Go's parser exactly
    (`test_command_contract`), including the bare `--tls` case.
  - AC: a failed sidecar pull logs and still enters the run into history.
  - Verify: `pytest gui/tests/unit/test_command_contract.py`; end-to-end on a
    device.
  - Files: `gui/app.py`, `gui/tests/unit/test_command_contract.py`.
  - Deps: 3, 6. Scope: S.

### Checkpoint: Complete
- [ ] Full `pytest gui/tests` green; `go build ./...` clean.
- [ ] SPEC_gui_v4 AC 1–7 each demonstrated.
- [ ] On a device: run `flow --regs --mem X0 --tls`, browse the tree, toggle each
      pane, confirm no pane in Trace ever shows live state.
- [ ] `adb shell ps -A | grep '[e]DBG'` empty after quit (v2 AC 7b, unregressed).

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| `--tls` makes flow unusably slow | High | Task 3 memoises annotations and snapshots maps once; Checkpoint "Capture" times it before any GUI work depends on it |
| Sidecar breaks existing pulled CSVs | High | Task 2 AC 1 is exactly the no-sidecar, no-metadata path; fixtures are today's real files |
| Toggle × mode visibility drifts into two sources of truth | Med | Visibility is one expression, `mode ∧ toggle`, computed from pure data; Task 7 asserts it headless |
| Tree children re-render the whole run | High | `_FLOW_MAX_ROWS` windowing moves with the children; Task 5 AC 2 counts widgets |
| Metadata `#` row confuses `csv.reader` consumers | Med | Written as a single-field row; the reader skips only a leading `#` |
| Deleting the live fallback leaves a blank pane operators read as a bug | Low | Every "captured without" case renders an explicit line naming the missing flag |

## Open Questions

- Should the toggles persist across launches? Currently no — v3 refused saved
  layouts for the same reason, and a toggle is a layout.
- Does `pane_flow` (raw trace rows) still earn a place once the tree lists the
  same steps? Kept as a default-off toggle so the question gets answered by use.
- Default TLS slot count: 32 is a guess pending the Checkpoint "Capture" timing.
