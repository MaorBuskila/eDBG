# SPEC — eDBG GUI v4 (Trace Mode)

> Supersedes the **Trace mode** rows of `SPEC_gui_v3.md` only. The mode shell,
> the pane pool, dirty-gated repaint, and the measured column floors stand
> unchanged and are dependencies of this spec, not alternatives to it.

## Problem Statement

**How might we let an operator read a finished `flow` run the way they read a
directory — pick the run, pick the step, and see everything captured at that
step — without the mode borrowing controls and state from the live step loop?**

## What is wrong today

Trace mode is a live-debugger screen wearing a trace costume:

- The persistent run-control row (Continue / Interrupt / Step / Next / Finish /
  Until) is on screen while the operator reads a run that finished minutes ago.
  Every one of those buttons acts on the live process, not on the trace.
- `_populate_flow_regs` falls back to `_session.last_regs` when the run carried
  no `--regs` ([app.py:741](gui/app.py:741)). That renders live-process state
  inside a pane labelled with a step index from a file on disk — two different
  moments in time in one pane.
- The step list is a flat `add_selectable` run in `pane_flow`
  ([app.py:706](gui/app.py:706)) that is disconnected from the run list in
  `pane_flow_history`. Which run a step belongs to is knowable only by reading
  another pane.

## Recommended Direction

**Trace mode is a file browser over finished runs.** One tree on the left:
runs are folders, steps are files. Everything else on screen is the selected
step's captured data, and which of those detail panes are on screen is chosen by
the operator with toggles that sit exactly where the run controls sit in the
other modes.

```
┌──────────────────────────────────────────────────────────────────────┐
│ HUD: ● state │ pkg / lib / breaks │ connect                          │
│ [x] Registers  [x] Memory  [ ] TLS  [ ] Trace rows                   │ ← swapped row
├──────────────────────────────────────────────────────────────────────┤
│ [Step] [▸ Trace] [Inspect] [Log]                                     │
├────────────────────┬──────────────────────┬──────────────────────────┤
│ FLOW HISTORY       │ STEP REGISTERS       │ STEP MEMORY              │
│ Addr[    ] Run     │  step #12 of 46      │  [0x7b1c40] = 0x1        │
│                    │  x0   0x1  *         │                          │
│ ▾ libloader        │  x1   0x7b1c40       ├──────────────────────────┤
│   +0x1c6f68        │  ...                 │ STEP TLS                 │
│   <Java_com_…>     │                      │  +0x0  0x7b… stack       │
│   46 steps         │                      │  +0x8  0x7a… code  <sym> │
│    0 +0x1c6f68     │                      │                          │
│    1 +0x1c6f70     │                      │                          │
│   ▸ …              │                      │                          │
│ ▸ libloader        │                      │                          │
│   +0x1a2000        │                      │                          │
│   120 steps        │                      │                          │
└────────────────────┴──────────────────────┴──────────────────────────┘
```

## Core Acceptance Criteria

Inherits `SPEC_gui_v3.md` §"Core Acceptance Criteria" 1–7. Adds:

1. **Trace mode shows no live-process state.** No pane visible in Trace reads
   `_session.last_*`.
   - AC: a run captured without `--regs` renders `(run captured without --regs)`
     and nothing else — never live registers.
   - AC: grep proves no `_session.last_` reference on any `_populate_flow_*` path.

2. **Runs are folders, steps are files.** One tree owns both selections.
   - AC: selecting a run root selects the run and step 0; selecting a step child
     selects only the step.
   - AC: a 10 000-step run renders `_FLOW_MAX_ROWS` children, not 10 000 — the
     window is centred on the selected step and states what it clipped.

3. **The control row is per mode.** Run controls in Step / Inspect / Log; data
   toggles in Trace. They are the same screen row, never both at once.
   - AC: `_set_mode("trace")` hides the run-control group and shows the toggle
     group; `_set_mode("step")` reverses it. Assertable headless from the mode
     registry — the row a mode wants is data, not a branch in `_set_mode`.

4. **Toggles compose with the mode, they do not fight it.** A pane is on screen
   iff its mode lists it AND its toggle is on. Heights renormalise over what is
   actually visible, so turning TLS off gives its pixels to Memory.
   - AC: `row_weights(mode, col, hidden={...})` sums to 1.0 over survivors.
   - AC: toggling a pane off then on repaints it with the *current* selection,
     never stale rows (the v3 reveal contract, applied to toggles).

5. **A step's TLS is captured, not reconstructed.** `flow --tls[=N]` writes the
   per-step stack_and_tls slots to a sidecar CSV next to the run's CSV.
   - AC: the sidecar is `<lib>_0x<rva>_flow_tls.csv` with columns
     `step,slot,addr,value,class,annot`; `glob("*_flow.csv")` does not match it.
   - AC: a run pulled without the sidecar loads exactly as before — the TLS pane
     says the run was captured without `--tls`.
   - AC: classification uses one maps snapshot taken at flow entry, and each
     distinct slot value is annotated at most once per run.

6. **Every captured value is clickable.** Register values, the memory probe
   address and value, and TLS slot values copy to the clipboard on click; an
   address additionally lands in the flow address box, as
   `_cb_use_address` already does for disassembly.
   - AC: clicking a register value in Trace mode issues no REPL command — a
     finished run has no process to talk to.

7. **The run's entry symbol is in the tree.** The root row reads
   `<lib>+0x<rva>  <symbol>  N steps`, symbol omitted when unknown.
   - AC: Go writes a leading `#` metadata row carrying `symbol=`; a CSV without
     one parses with `symbol == ""` and no error (every already-pulled CSV).

## Key Assumptions to Validate

- [ ] **`--tls` is affordable.** Test: time a 500-step flow with `--tls=32`
      against the same run without it on a real device. 32 slots per step is one
      256-byte read plus at most 32 memoised annotations; if the wall clock more
      than doubles, the default slot count drops to 8 or annotation becomes
      opt-in behind a second flag.
- [ ] **Steps-as-tree-children stays usable at `_FLOW_MAX_ROWS`.** Test: open a
      10 000-step run and scrub. If the windowed child list reads as a broken
      list rather than a clipped one, the tree needs paging controls, not a
      bigger window.
- [ ] **Four toggles is the right set.** Test: instrument toggle state across
      real sessions. If Registers is on 100% of the time it is not a toggle, it
      is the pane.

## MVP Scope

**In** — the run/step tree with the flow launch controls in its header; the
control-row swap and the four toggles; step registers with the live fallback
removed; a step-memory pane; a step-TLS pane; `flow --tls[=N]` and its sidecar;
the metadata row carrying the entry symbol; clickable values in every flow pane.

**Out of MVP** — diffing two runs against each other; per-step disassembly (the
CSV has no bytes and the process is gone); a step-history pane in Step mode;
persisting toggle state across launches.

## Not Doing (and Why)

- **A step-history pane in Step mode** — asked for and deliberately deferred:
  the point of this change is that Trace stops mirroring Step, which is achieved
  by deletion, not by building a second thing.
- **Re-parenting flow panes between modes** — `_derive_pane_column` rejects it,
  and never re-parenting is the invariant the shell is built on. New detail
  panes are new pane pool entries in a fixed column.
- **Reconstructing TLS in the GUI from a maps file** — the classification needs
  the live process's mappings and reads through its pointers. It is capture-time
  work or it is nothing.
- **Streaming the whole trace into the tree** — every DPG row is a widget laid
  out each frame; `_FLOW_MAX_ROWS` exists for that reason and is unchanged.
- **Leaving Run Flow in the run-control row** — it launches a trace, so it lives
  with the trace history it appends to.

## Open Questions

- Does the memory probe deserve a pane, or is it two cells that belong under the
  register list? Split for now because `--mem` is per-run optional and a pane can
  be toggled off when the run did not use it.
- Should selecting a step scroll the trace-rows pane, when both are visible?
