"""Flow trace CSV model — pure, no ``dearpygui`` import.

``flow`` writes one CSV per run on the device (cli/repl.go:1141) and the GUI
pulls it into ``gui/data``. The column set depends on the flags the run was
launched with: ``step,va,rva`` always, 34 register columns only under
``--regs``, ``mem_addr,mem_value`` only under ``--mem`` — so the shape is read
from the header row, never assumed.

Runs are identified by filename (``<lib>_0x<rva>_flow.csv``), which is what
makes a pulled CSV from an earlier session a first-class history entry.

``--tls`` writes its slots to a sidecar, ``<lib>_0x<rva>_flow_tls.csv``, because
the slot count is a per-run choice and the main CSV's row width is not. The
sidecar is optional in both directions: a run without one loads exactly as it
did before it existed.
"""

from __future__ import annotations

import csv
import glob
import os
import re
from dataclasses import dataclass, field

#: Fixed prefix every row carries, regardless of flags.
BASE_COLUMNS = ("step", "va", "rva")

_NAME_RE = re.compile(r"^(?P<lib>.+)_0x(?P<rva>[0-9a-fA-F]+)_flow\.csv$")


@dataclass(frozen=True)
class FlowStep:
    index: int
    va: int
    rva: int
    #: Positionally aligned with ``FlowRun.reg_names``; empty on a bare run.
    regs: tuple = ()
    mem_addr: int | None = None
    #: None when Go wrote "ERR" — the read failed but the step still happened.
    mem_value: int | None = None


@dataclass(frozen=True)
class FlowTlsSlot:
    slot: int
    addr: int
    value: int
    #: utils.TlsClass — string|code|stack|heap|mapped|junk.
    cls: str
    #: Symbol, "-> …" peek, or LE ASCII, resolved at capture time; "" when none.
    annot: str = ""


@dataclass
class FlowRun:
    path: str
    lib: str
    rva: int
    mtime: float
    reg_names: list = field(default_factory=list)
    has_mem: bool = False
    rows: list = field(default_factory=list)
    #: Entry symbol from the metadata row; "" on a pre-metadata CSV.
    symbol: str = ""
    #: step index -> slots, from the sidecar. Absent steps simply have none.
    tls: dict = field(default_factory=dict)

    @property
    def steps(self) -> int:
        return len(self.rows)

    @property
    def has_regs(self) -> bool:
        return bool(self.reg_names)

    @property
    def has_tls(self) -> bool:
        return bool(self.tls)

    def tls_at(self, step: int) -> list:
        return self.tls.get(step, [])

    def mem_at(self, step: int):
        """``(addr, value)`` for `step`, value None when Go wrote "ERR".

        None means the run carried no ``--mem`` probe at all.
        """
        if not self.has_mem or not 0 <= step < len(self.rows):
            return None
        row = self.rows[step]
        return row.mem_addr, row.mem_value

    @property
    def label(self) -> str:
        return f"{self.lib}+0x{self.rva:x}"

    def regs_at(self, step: int) -> list:
        """``[(name, value)]`` for `step`, or ``[]`` on a bare/absent step."""
        if not self.has_regs or not 0 <= step < len(self.rows):
            return []
        return list(zip(self.reg_names, self.rows[step].regs))

    def changed_at(self, step: int) -> set:
        """Register names whose value differs from the previous step."""
        if not self.has_regs or not 0 < step < len(self.rows):
            return set()
        prev = self.rows[step - 1].regs
        cur = self.rows[step].regs
        return {name for name, a, b in zip(self.reg_names, prev, cur) if a != b}


def _parse_row(raw: list, reg_names: list, has_mem: bool) -> FlowStep | None:
    if len(raw) < len(BASE_COLUMNS) + len(reg_names) + (2 if has_mem else 0):
        return None
    try:
        index = int(raw[0])
        va = int(raw[1], 16)
        rva = int(raw[2], 16)
        cursor = len(BASE_COLUMNS)
        regs = tuple(int(raw[cursor + i], 16) for i in range(len(reg_names)))
        cursor += len(reg_names)
        mem_addr = mem_value = None
        if has_mem:
            mem_addr = int(raw[cursor], 16)
            cell = raw[cursor + 1]
            mem_value = None if cell == "ERR" else int(cell, 16)
    except ValueError:
        return None
    return FlowStep(index=index, va=va, rva=rva, regs=regs,
                    mem_addr=mem_addr, mem_value=mem_value)


def _identity(path: str, rows: list) -> tuple:
    """(lib, rva) from the filename, falling back to the first row's RVA."""
    m = _NAME_RE.match(os.path.basename(path))
    if m:
        return m.group("lib"), int(m.group("rva"), 16)
    stem = os.path.splitext(os.path.basename(path))[0]
    return stem, rows[0].rva if rows else 0


def _symbol_from_meta(row: list) -> str:
    """The entry symbol out of a ``# lib=…,rva=…,symbol=…`` row.

    ``symbol=`` is read to end of field rather than split on commas, because a
    demangled C++ name carries both commas and equals signs.
    """
    if not row or not row[0].startswith("#"):
        return ""
    _, sep, tail = row[0].partition("symbol=")
    return tail if sep else ""


def _sidecar_path(path: str) -> str:
    stem = path[:-len("_flow.csv")] if path.endswith("_flow.csv") \
        else os.path.splitext(path)[0]
    return f"{stem}_flow_tls.csv"


def parse_flow_tls_csv(path: str) -> dict:
    """``{step: [FlowTlsSlot]}`` from a ``--tls`` sidecar; ``{}`` when absent.

    A row that will not parse is dropped alone — a trace that captured 900 good
    steps and one bad slot is still worth reading.
    """
    try:
        with open(path, newline="") as fh:
            raw_rows = list(csv.reader(fh))
    except OSError:
        return {}
    out: dict = {}
    for raw in raw_rows[1:]:
        if len(raw) < 5:
            continue
        try:
            step = int(raw[0])
            slot = FlowTlsSlot(slot=int(raw[1]), addr=int(raw[2], 16),
                               value=int(raw[3], 16), cls=raw[4],
                               annot=raw[5] if len(raw) > 5 else "")
        except ValueError:
            continue
        out.setdefault(step, []).append(slot)
    return out


def parse_flow_csv(path: str) -> FlowRun | None:
    """Read one pulled flow CSV. None when the file is missing or headerless."""
    try:
        with open(path, newline="") as fh:
            raw_rows = list(csv.reader(fh))
    except OSError:
        return None
    if not raw_rows:
        return None

    symbol = _symbol_from_meta(raw_rows[0])
    if raw_rows[0] and raw_rows[0][0].startswith("#"):
        raw_rows = raw_rows[1:]
    if not raw_rows:
        return None

    header = raw_rows[0]
    if tuple(header[:3]) != BASE_COLUMNS:
        return None
    tail = header[len(BASE_COLUMNS):]
    has_mem = "mem_addr" in tail
    reg_names = [c for c in tail if c not in ("mem_addr", "mem_value")]

    rows = []
    for raw in raw_rows[1:]:
        step = _parse_row(raw, reg_names, has_mem)
        if step is not None:
            rows.append(step)

    lib, rva = _identity(path, rows)
    return FlowRun(path=path, lib=lib, rva=rva, mtime=os.path.getmtime(path),
                   reg_names=reg_names, has_mem=has_mem, rows=rows,
                   symbol=symbol, tls=parse_flow_tls_csv(_sidecar_path(path)))


def discover_runs(directory: str) -> list:
    """Every ``*_flow.csv`` already pulled into `directory`, newest first."""
    runs = []
    for path in glob.glob(os.path.join(directory, "*_flow.csv")):
        run = parse_flow_csv(path)
        if run is not None:
            runs.append(run)
    runs.sort(key=lambda r: r.mtime, reverse=True)
    return runs


def step_to_text(step: FlowStep) -> str:
    line = f"#{step.index:<5} 0x{step.va:x}  +0x{step.rva:x}"
    if step.mem_addr is not None:
        value = "ERR" if step.mem_value is None else f"0x{step.mem_value:x}"
        line += f"  [0x{step.mem_addr:x}] = {value}"
    return line


def flow_to_text(run: FlowRun | None) -> str:
    if run is None:
        return "(no flow trace)"
    out = [f"flow {run.label}  {run.steps} steps  {run.path}"]
    out.extend(step_to_text(s) for s in run.rows)
    return "\n".join(out)
