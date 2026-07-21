"""Typed events from eDBG's ``-json`` stream.

Mirrors the schema in ``SPEC_gui_v2.md``. Deliberately imports no DPG, so the
whole event path stays testable headless.

Payloads are decoded into the *same* shapes ``gui.parse`` already produces —
``RegisterInfo``, ``DisasmLine``, ``BTFrame``, and the breakpoint/thread dicts.
The existing panes therefore render structured events with no changes, and the
text parser stays a drop-in fallback for non-``-json`` sessions.

All addresses and register values cross the wire as hex *strings*: a uint64
does not survive a JSON number round-trip safely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gui import parse

SCHEMA = 1


def _int(value: Any, default: int = 0) -> int:
    """Parse a wire value that may be a hex string, an int, or missing."""
    if value is None:
        return default
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 16 if str(value).lower().startswith("0x") else 10)
    except ValueError:
        return default


def _bytes(hexstr: Any) -> bytes:
    if not hexstr:
        return b""
    try:
        return bytes.fromhex(str(hexstr))
    except ValueError:
        return b""


# ── events ───────────────────────────────────────────────────────────

@dataclass
class Hello:
    schema: int = 0
    edbg_version: str = ""
    pkg: str = ""
    lib: str = ""
    edbg_pid: int = 0

    @classmethod
    def from_obj(cls, o: dict) -> "Hello":
        return cls(schema=_int(o.get("schema")),
                   edbg_version=o.get("edbg_version", ""),
                   pkg=o.get("pkg", ""), lib=o.get("lib", ""),
                   edbg_pid=_int(o.get("edbg_pid")))


@dataclass
class Stop:
    """One breakpoint/step stop, carrying every pane's data atomically."""
    reason: str = ""
    detail: str = "full"
    hit: int = 0
    pid: int = 0
    tid: int = 0
    pc: int = 0
    rva: int = 0
    lib: str = ""
    regs: list[parse.RegisterInfo] = field(default_factory=list)
    disasm: list[parse.DisasmLine] = field(default_factory=list)
    backtrace: list[parse.BTFrame] = field(default_factory=list)
    threads: list[dict] = field(default_factory=list)
    breakpoints: list[dict] = field(default_factory=list)
    memory: list[tuple[int, bytes]] = field(default_factory=list)

    @classmethod
    def from_obj(cls, o: dict) -> "Stop":
        regs = [
            parse.RegisterInfo(
                name=r.get("n", ""),
                value=_int(r.get("v")),
                deref=r.get("deref") or None,
                # parse_registers wraps the symbol in angle brackets; keep the
                # same form so panes render both sources identically.
                symbol=f"<{r['sym']}>" if r.get("sym") else "",
            )
            for r in o.get("regs", [])
        ]
        disasm = [
            parse.DisasmLine(
                address=_int(d.get("a")),
                symbol=d.get("sym", "") or "",
                mnemonic=d.get("m", ""),
                operands=d.get("o", "") or "",
                is_current=bool(d.get("cur")),
            )
            for d in o.get("disasm", [])
        ]
        bt = [
            parse.BTFrame(index=_int(f.get("i")),
                          address=_int(f.get("a")),
                          symbol=f.get("sym", ""))
            for f in o.get("bt", [])
        ]
        threads = [
            {"index": _int(t.get("i")), "tid": _int(t.get("tid")),
             "name": t.get("name", ""), "is_current": bool(t.get("cur"))}
            for t in o.get("threads", [])
        ]
        bps = [_bp_from_obj(b) for b in o.get("bps", [])]

        # `display` (user watches) and `pointers` (auto-read pointer registers)
        # both land in the memory pane.
        memory: list[tuple[int, bytes]] = []
        for d in o.get("display", []):
            memory.append((_int(d.get("addr")), _bytes(d.get("hex"))))
        for p in o.get("pointers", []):
            memory.append((_int(p.get("addr")), _bytes(p.get("hex"))))

        return cls(
            reason=o.get("reason", ""), detail=o.get("detail", "full"),
            hit=_int(o.get("hit")), pid=_int(o.get("pid")),
            tid=_int(o.get("tid")), pc=_int(o.get("pc")),
            rva=_int(o.get("rva")), lib=o.get("lib", ""),
            regs=regs, disasm=disasm, backtrace=bt, threads=threads,
            breakpoints=bps, memory=memory,
        )


def _bp_from_obj(b: dict) -> dict:
    addr = _int(b.get("off"))
    return {
        "id": _int(b.get("id")),
        "enabled": bool(b.get("on")),
        "type": b.get("type", "software"),
        "library": b.get("lib", "") or "",
        "offset": addr,
        "address": addr if b.get("type") == "hardware" else 0,
    }


@dataclass
class FlowProgress:
    step: int = 0
    max_steps: int = 0
    pc: int = 0
    rva: int = 0

    @classmethod
    def from_obj(cls, o: dict) -> "FlowProgress":
        return cls(step=_int(o.get("step")), max_steps=_int(o.get("max")),
                   pc=_int(o.get("pc")), rva=_int(o.get("rva")))


@dataclass
class FlowDone:
    steps: int = 0
    csv_path: str = ""
    reason: str = ""

    @classmethod
    def from_obj(cls, o: dict) -> "FlowDone":
        return cls(steps=_int(o.get("steps")), csv_path=o.get("csv_path", ""),
                   reason=o.get("reason", ""))


@dataclass
class BreakpointsEvent:
    breakpoints: list[dict] = field(default_factory=list)

    @classmethod
    def from_obj(cls, o: dict) -> "BreakpointsEvent":
        return cls(breakpoints=[_bp_from_obj(b) for b in o.get("bps", [])])


@dataclass
class ThreadsEvent:
    threads: list[dict] = field(default_factory=list)

    @classmethod
    def from_obj(cls, o: dict) -> "ThreadsEvent":
        return cls(threads=[
            {"index": _int(t.get("i")), "tid": _int(t.get("tid")),
             "name": t.get("name", ""), "is_current": bool(t.get("cur"))}
            for t in o.get("threads", [])
        ])


@dataclass
class MemoryEvent:
    addr: int = 0
    data: bytes = b""
    src: str = ""

    @classmethod
    def from_obj(cls, o: dict) -> "MemoryEvent":
        return cls(addr=_int(o.get("addr")), data=_bytes(o.get("hex")),
                   src=o.get("src", ""))


@dataclass
class ErrorEvent:
    msg: str = ""
    cmd: str = ""

    @classmethod
    def from_obj(cls, o: dict) -> "ErrorEvent":
        return cls(msg=o.get("msg", ""), cmd=o.get("cmd", ""))


@dataclass
class UnknownEvent:
    """A well-formed event of a type this build does not know.

    Forward compatibility: a newer eDBG must not break an older GUI.
    """
    type: str = ""
    payload: dict = field(default_factory=dict)


@dataclass
class Malformed:
    """Sentinel present but the payload would not decode."""
    raw: str = ""
    error: str = ""


@dataclass
class LogLine:
    """An unframed line — ordinary human-readable REPL output."""
    text: str = ""


_BUILDERS = {
    "hello": Hello,
    "stop": Stop,
    "flow_progress": FlowProgress,
    "flow_done": FlowDone,
    "breakpoints": BreakpointsEvent,
    "threads": ThreadsEvent,
    "memory": MemoryEvent,
    "error": ErrorEvent,
}


def build(obj: dict):
    """Turn a decoded JSON object into its event dataclass."""
    kind = obj.get("t", "")
    builder = _BUILDERS.get(kind)
    if builder is None:
        return UnknownEvent(type=kind, payload=obj)
    return builder.from_obj(obj)
