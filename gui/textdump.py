"""Render session state as plain text for the clipboard.

DPG's ``add_text`` is a draw-only label with no selection model, so nothing in
the GUI can be selected or copied. Rather than bolt selection onto widgets,
these functions reproduce each pane from the *session data*, which means they
are pure, testable headless, and unaffected by the v2 renderer rewrite.

Output mirrors what the panes display, minus colour.
"""

from __future__ import annotations

from gui import parse


def regs_to_text(regs: list[parse.RegisterInfo]) -> str:
    if not regs:
        return "(no registers)"
    out = []
    for r in regs:
        line = f"{r.name:<4} 0x{r.value:X}"
        if r.symbol:
            line += f"  {r.symbol}"
        if r.deref:
            line += f"  <- {r.deref}"
        out.append(line)
    return "\n".join(out)


def disasm_to_text(lines: list[parse.DisasmLine]) -> str:
    if not lines:
        return "(no disassembly)"
    out = []
    for d in lines:
        marker = ">>" if d.is_current else "  "
        sym = f"<{d.symbol}>" if d.symbol else ""
        ops = f" {d.operands}" if d.operands else ""
        out.append(f"{marker} 0x{d.address:x}{sym}\t{d.mnemonic}{ops}")
    return "\n".join(out)


def backtrace_to_text(frames: list[parse.BTFrame]) -> str:
    if not frames:
        return "(no backtrace)"
    return "\n".join(
        f"#{f.index:<3} 0x{f.address:016x} in {f.symbol}" for f in frames)


def memory_to_text(mem: list[tuple[int, bytes]]) -> str:
    if not mem:
        return "(no memory)"
    out = []
    for addr, raw in mem:
        hex_fmt = " ".join(f"{b:02x}" for b in raw)
        ascii_str = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in raw)
        out.append(f"0x{addr:08x}  {hex_fmt:<48s}  |{ascii_str}|")
    return "\n".join(out)


def breakpoints_to_text(bps: list[dict]) -> str:
    if not bps:
        return "(no breakpoints)"
    out = []
    for bp in bps:
        status = "[+]" if bp["enabled"] else "[-]"
        if bp["type"] == "hardware":
            where = f"0x{bp['offset']:x} Hardware"
        else:
            where = f"{bp['library']}+0x{bp['offset']:x}"
        out.append(f"{status} {bp['id']}: {where}")
    return "\n".join(out)


def threads_to_text(threads: list[dict]) -> str:
    if not threads:
        return "(no threads)"
    out = []
    for t in threads:
        marker = ">>" if t["is_current"] else "  "
        out.append(f"{marker} [{t['index']}] {t['tid']}: {t['name']}")
    return "\n".join(out)


def tls_to_text(dump: parse.TlsDump | None) -> str:
    if dump is None or not dump.slots:
        return "(no tls)"
    out = [f"tls tid={dump.tid} map={dump.map_name} "
           f"0x{dump.map_start:x}-0x{dump.map_end:x}",
           f"base=0x{dump.base:x}  len=0x{dump.length:x}"]
    for s in dump.slots:
        row = f"0x{s.address:x}  0x{s.value:x}  {s.cls}  +0x{s.offset:x}"
        out.append(f"{row}  {s.annotation}" if s.annotation else row)
    return "\n".join(out)


def log_to_text(transcript: list[str], limit: int | None = None) -> str:
    if not transcript:
        return "(empty log)"
    lines = transcript[-limit:] if limit else transcript
    return "\n".join(parse.strip_ansi(l) for l in lines)


def session_to_text(session) -> str:
    """Everything, as one paste-ready report."""
    hit = session.last_hit
    header = (f"[Hit #{hit.hit_number}] pid={hit.pid} tid={hit.tid}"
              if hit else "(no hit)")
    blocks = [
        f"=== eDBG {header} ===",
        "\n--- REGISTERS ---\n" + regs_to_text(session.last_regs),
        "\n--- DISASM ---\n" + disasm_to_text(session.last_disasm),
        "\n--- BACKTRACE ---\n" + backtrace_to_text(session.last_backtrace),
        "\n--- MEMORY ---\n" + memory_to_text(session.last_memory),
        "\n--- BREAKPOINTS ---\n" + breakpoints_to_text(
            session.last_breakpoints),
        "\n--- THREADS ---\n" + threads_to_text(session.last_threads),
    ]
    return "\n".join(blocks)
