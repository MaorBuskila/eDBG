# tasks/todo.md — GUI v3.1 (see tasks/plan.md)

## Phase 1: Foundation
- [x] Task 1: `gui/flowtrace.py` — `FlowRun`, `parse_flow_csv`, `flow_to_text` + tests
- [x] Task 2: `modes.py` trace reshape — history/flow_regs panes, `pane_flow` → right, drop watch
- [x] Task 3: `app.py` builders + chrome + painter stubs for the two new panes

### Checkpoint A
- [x] `pytest gui/tests -q` green (341 → 361 passing)
- [x] `pytest gui/tests/smoke -q` builds
- [x] F2 shows three placeholder panes, no stranded controls

## Phase 2: Flow slice
- [x] Task 4: render the selected run in `pane_flow`; row click selects a step
- [x] Task 5: `pane_flow_history` — session runs + `gui/data/*_flow.csv` seeding
- [x] Task 6: `pane_flow_regs` — per-step registers, change highlight, live fallback

### Checkpoint B
- [ ] Device: flow → CSV pulled → history entry → rows → step click → regs update

## Phase 3: TLS and copy
- [x] Task 7: send `tls` on hit; rolling-window `parse_tls` across poll batches
- [x] Task 8: per-pane TEXT toggle (readonly multiline input) + flow copy sources
- [x] Task 9: disasm/flow row click copies address and fills Flow `Addr`

### Checkpoint C
- [x] Full suite green
- [ ] All five reported gaps demonstrated on device
- [x] `SPEC_gui_v3.md` trace-mode layout updated

## Verify

```bash
# Only python3.9 here has pytest, and test_adb_pty.py needs 3.10+ syntax.
/opt/homebrew/bin/pytest gui/tests -q --ignore=gui/tests/unit/test_adb_pty.py
```

---

Previous list (eDBG `tls` Go work, tasks 1–4 all landed) is in git history at cb640b8.
