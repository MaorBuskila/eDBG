"""Flow CSV parsing.

Format is written by Client.HandleFlow (cli/repl.go:1141-1158). The column set
is not fixed: `step,va,rva` always, then 34 register columns only under
``--regs``, then two memory columns only under ``--mem``. A parser that assumes
one shape silently mis-reads the other two.
"""

from __future__ import annotations

import os

import pytest

from gui import flowtrace

BARE = """\
step,va,rva
0,0x7ab60abf68,0x1c6f68
1,0x7ab60abf6c,0x1c6f6c
2,0x7ab621cca4,0x337ca4
"""

_REG_COLS = ",".join(f"x{i}" for i in range(30)) + ",lr,sp,pc,pstate"
_REG_VALS_0 = ",".join(["0x1388"] + [f"0x{i:x}" for i in range(1, 30)]) + \
              ",0x7ab60e0078,0x7b4070d750,0x7ab60abf68,0xffffffff"
_REG_VALS_1 = ",".join(["0x9999"] + [f"0x{i:x}" for i in range(1, 30)]) + \
              ",0x7ab60e0078,0x7b4070d750,0x7ab60abf6c,0xffffffff"

WITH_REGS = (f"step,va,rva,{_REG_COLS}\n"
             f"0,0x7ab60abf68,0x1c6f68,{_REG_VALS_0}\n"
             f"1,0x7ab60abf6c,0x1c6f6c,{_REG_VALS_1}\n")

WITH_MEM = """\
step,va,rva,mem_addr,mem_value
0,0x7ab60abf68,0x1c6f68,0x7b4070d728,0x4142434445464748
1,0x7ab60abf6c,0x1c6f6c,0x7b4070d728,ERR
"""


def _write(tmp_path, name: str, text: str) -> str:
    p = tmp_path / name
    p.write_text(text)
    return str(p)


# ── shape: bare ──────────────────────────────────────────────────────

def test_bare_run_has_steps_but_no_regs(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    assert run.steps == 3
    assert not run.has_regs and not run.has_mem


def test_step_fields_parse_as_ints(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    assert [s.index for s in run.rows] == [0, 1, 2]
    assert run.rows[2].va == 0x7AB621CCA4
    assert run.rows[2].rva == 0x337CA4


# ── identity from the filename ───────────────────────────────────────

def test_lib_and_rva_come_from_the_filename(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    assert run.lib == "libloader"
    assert run.rva == 0x1C6F68
    assert "0x1c6f68" in run.label


def test_unparsable_filename_falls_back_to_first_row(tmp_path):
    run = flowtrace.parse_flow_csv(_write(tmp_path, "dump.csv", BARE))
    assert run.rva == 0x1C6F68        # first row's rva column
    assert run.lib == "dump"


def test_empty_run_with_unparsable_name_has_zero_rva(tmp_path):
    run = flowtrace.parse_flow_csv(_write(tmp_path, "dump.csv", "step,va,rva\n"))
    assert run.steps == 0 and run.rva == 0


# ── shape: --regs ────────────────────────────────────────────────────

def test_regs_run_names_every_register(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "lib_0x10_flow.csv", WITH_REGS))
    assert run.has_regs
    assert run.reg_names[:3] == ["x0", "x1", "x2"]
    assert run.reg_names[-4:] == ["lr", "sp", "pc", "pstate"]
    assert len(run.reg_names) == 34


def test_regs_at_pairs_names_with_values(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "lib_0x10_flow.csv", WITH_REGS))
    pairs = run.regs_at(0)
    assert pairs[0] == ("x0", 0x1388)
    assert pairs[-1] == ("pstate", 0xFFFFFFFF)
    assert dict(pairs)["pc"] == 0x7AB60ABF68


def test_changed_regs_are_the_diff_against_the_previous_step(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "lib_0x10_flow.csv", WITH_REGS))
    assert run.changed_at(0) == set()          # nothing to diff against
    assert run.changed_at(1) == {"x0", "pc"}


def test_regs_at_on_a_bare_run_is_empty(tmp_path):
    run = flowtrace.parse_flow_csv(_write(tmp_path, "lib_0x10_flow.csv", BARE))
    assert run.regs_at(0) == []
    assert run.changed_at(1) == set()


def test_out_of_range_step_is_empty_not_an_error(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "lib_0x10_flow.csv", WITH_REGS))
    assert run.regs_at(99) == []
    assert run.changed_at(99) == set()


# ── shape: --mem ─────────────────────────────────────────────────────

def test_mem_columns_parse(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "lib_0x10_flow.csv", WITH_MEM))
    assert run.has_mem
    assert run.rows[0].mem_addr == 0x7B4070D728
    assert run.rows[0].mem_value == 0x4142434445464748


def test_mem_read_error_is_none_not_a_dropped_row(tmp_path):
    # Go writes the literal "ERR" when ReadProcessMemory fails; the step still
    # happened and must stay in the trace.
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "lib_0x10_flow.csv", WITH_MEM))
    assert run.steps == 2
    assert run.rows[1].mem_addr == 0x7B4070D728
    assert run.rows[1].mem_value is None


# ── robustness ───────────────────────────────────────────────────────

def test_short_and_garbage_rows_are_skipped(tmp_path):
    text = BARE + "3,0x1\n4,notahex,0x5\n5,0x7ab60abf70,0x1c6f70\n"
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "lib_0x10_flow.csv", text))
    assert [s.index for s in run.rows] == [0, 1, 2, 5]


def test_missing_file_returns_none(tmp_path):
    assert flowtrace.parse_flow_csv(str(tmp_path / "nope.csv")) is None


def test_headerless_file_returns_none(tmp_path):
    assert flowtrace.parse_flow_csv(_write(tmp_path, "x.csv", "")) is None


# ── discovery ────────────────────────────────────────────────────────

def test_discover_runs_is_newest_first(tmp_path):
    old = _write(tmp_path, "a_0x1_flow.csv", BARE)
    new = _write(tmp_path, "b_0x2_flow.csv", BARE)
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    runs = flowtrace.discover_runs(str(tmp_path))
    assert [r.lib for r in runs] == ["b", "a"]


def test_discover_ignores_non_flow_csvs(tmp_path):
    _write(tmp_path, "a_0x1_flow.csv", BARE)
    _write(tmp_path, "notes.csv", BARE)
    assert len(flowtrace.discover_runs(str(tmp_path))) == 1


def test_discover_on_a_missing_dir_is_empty(tmp_path):
    assert flowtrace.discover_runs(str(tmp_path / "gone")) == []


# ── text dump ────────────────────────────────────────────────────────

def test_flow_to_text_has_one_line_per_step_plus_header(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    lines = flowtrace.flow_to_text(run).splitlines()
    assert len(lines) == 1 + run.steps
    assert "0x337ca4" in lines[-1]


def test_flow_to_text_handles_none():
    assert flowtrace.flow_to_text(None) == "(no flow trace)"


# ── run metadata (the leading "#" row) ───────────────────────────────

META = '"# lib=libloader,rva=0x1c6f68,symbol=Java_com_foo_bar"\n'


def test_metadata_row_carries_the_entry_symbol(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "libloader_0x1c6f68_flow.csv", META + BARE))
    assert run.symbol == "Java_com_foo_bar"
    assert run.steps == 3          # the metadata row is not a step


def test_a_symbol_holding_commas_and_equals_survives(tmp_path):
    text = '"# lib=l,rva=0x10,symbol=f(int, char*)=x"\n' + BARE
    run = flowtrace.parse_flow_csv(_write(tmp_path, "l_0x10_flow.csv", text))
    assert run.symbol == "f(int, char*)=x"


def test_a_csv_without_metadata_has_no_symbol(tmp_path):
    # Every CSV pulled before this change. It must parse exactly as before.
    run = flowtrace.parse_flow_csv(_write(tmp_path, "l_0x10_flow.csv", BARE))
    assert run.symbol == ""
    assert run.steps == 3


def test_an_unresolved_symbol_is_empty_not_missing(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "l_0x10_flow.csv", '"# lib=l,rva=0x10,symbol="\n' + BARE))
    assert run.symbol == ""


# ── the --mem probe, per step ────────────────────────────────────────

def test_mem_at_returns_the_probed_pair(tmp_path):
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "l_0x10_flow.csv", WITH_MEM))
    assert run.mem_at(0) == (0x7B4070D728, 0x4142434445464748)
    assert run.mem_at(1) == (0x7B4070D728, None)      # Go wrote ERR


def test_mem_at_on_a_run_without_the_flag_is_none(tmp_path):
    run = flowtrace.parse_flow_csv(_write(tmp_path, "l_0x10_flow.csv", BARE))
    assert run.mem_at(0) is None
    assert run.mem_at(99) is None


# ── the --tls sidecar ────────────────────────────────────────────────

TLS_SIDECAR = """\
step,slot,addr,value,class,annot
0,0,0x7b4070d750,0x7b4070d800,stack,
0,1,0x7b4070d758,0x7ab60e0078,code,libloader.so+0x1fb078
1,0,0x7b4070d750,0x0,junk,........
"""


def test_tls_sidecar_is_picked_up_next_to_the_run(tmp_path):
    _write(tmp_path, "libloader_0x1c6f68_flow_tls.csv", TLS_SIDECAR)
    run = flowtrace.parse_flow_csv(
        _write(tmp_path, "libloader_0x1c6f68_flow.csv", BARE))
    assert run.has_tls
    slots = run.tls_at(0)
    assert [s.slot for s in slots] == [0, 1]
    assert slots[1].value == 0x7AB60E0078
    assert slots[1].cls == "code"
    assert slots[1].annot == "libloader.so+0x1fb078"
    assert slots[0].annot == ""


def test_a_step_the_sidecar_never_reached_has_no_slots(tmp_path):
    _write(tmp_path, "l_0x10_flow_tls.csv", TLS_SIDECAR)
    run = flowtrace.parse_flow_csv(_write(tmp_path, "l_0x10_flow.csv", BARE))
    assert run.tls_at(2) == []


def test_a_run_without_the_sidecar_says_so(tmp_path):
    run = flowtrace.parse_flow_csv(_write(tmp_path, "l_0x10_flow.csv", BARE))
    assert not run.has_tls
    assert run.tls_at(0) == []


def test_a_garbage_sidecar_row_drops_only_itself(tmp_path):
    text = TLS_SIDECAR + "2,0,notahex,0x1,stack,\n2,1,0x7b4070d758,0x2,heap,\n"
    _write(tmp_path, "l_0x10_flow_tls.csv", text)
    run = flowtrace.parse_flow_csv(_write(tmp_path, "l_0x10_flow.csv", BARE))
    assert [s.slot for s in run.tls_at(2)] == [1]


def test_the_sidecar_is_never_a_run_of_its_own(tmp_path):
    _write(tmp_path, "l_0x10_flow.csv", BARE)
    _write(tmp_path, "l_0x10_flow_tls.csv", TLS_SIDECAR)
    runs = flowtrace.discover_runs(str(tmp_path))
    assert len(runs) == 1 and runs[0].has_tls


# ── the real artifact, when it is on disk ────────────────────────────

_SAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "data",
                       "libloader_0x1c6f68_flow.csv")


@pytest.mark.skipif(not os.path.exists(_SAMPLE), reason="no pulled sample CSV")
def test_real_pulled_csv_parses_with_regs():
    run = flowtrace.parse_flow_csv(_SAMPLE)
    assert run.has_regs and run.steps > 0
    assert run.rva == 0x1C6F68
    assert dict(run.regs_at(0))["pc"] == run.rows[0].va
