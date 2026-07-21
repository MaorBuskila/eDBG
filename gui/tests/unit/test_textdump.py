"""Clipboard text rendering — pure functions over session state, no DPG."""

from __future__ import annotations

import pathlib

from gui import events, parse, protocol, textdump
from gui.session import EdbgSession

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _stop():
    raw = (FIXTURES / "session_basic.ndjson").read_text().splitlines()
    return next(e for e in (protocol.decode_line(l) for l in raw)
                if isinstance(e, events.Stop))


class TestEmptyPanes:

    def test_placeholders(self):
        assert textdump.regs_to_text([]) == "(no registers)"
        assert textdump.disasm_to_text([]) == "(no disassembly)"
        assert textdump.backtrace_to_text([]) == "(no backtrace)"
        assert textdump.memory_to_text([]) == "(no memory)"
        assert textdump.breakpoints_to_text([]) == "(no breakpoints)"
        assert textdump.threads_to_text([]) == "(no threads)"


class TestRegisters:

    def test_every_register_on_its_own_line(self):
        text = textdump.regs_to_text(_stop().regs)
        assert len(text.splitlines()) == 33
        assert text.splitlines()[0].startswith("X0 ")
        assert text.splitlines()[-1].startswith("PC ")

    def test_symbol_and_deref_included(self):
        line = next(l for l in textdump.regs_to_text(_stop().regs).splitlines()
                    if l.startswith("X1 "))
        assert "libloader.so" in line
        assert "<-" in line


class TestDisasm:

    def test_current_marker(self):
        text = textdump.disasm_to_text(_stop().disasm)
        assert text.splitlines()[0].startswith(">>")
        assert text.splitlines()[1].startswith("  ")

    def test_line_without_symbol(self):
        text = textdump.disasm_to_text(_stop().disasm)
        assert "RET" in text


class TestMemory:

    def test_hexdump_shape(self):
        line = textdump.memory_to_text(
            [(0x7BE3F90000, b"Hello World!\x00\x00\x00\x00")]).splitlines()[0]
        assert line.startswith("0x7be3f90000")
        assert "48 65 6c 6c 6f" in line
        assert "|Hello World!....|" in line

    def test_non_printable_becomes_dot(self):
        line = textdump.memory_to_text([(0, bytes([0x00, 0xFF]))])
        assert line.endswith("|..|")


class TestLog:

    def test_ansi_stripped(self):
        text = textdump.log_to_text(["\x1b[31mred\x1b[0m plain"])
        assert text == "red plain"
        assert "\x1b" not in text

    def test_limit_takes_the_tail(self):
        assert textdump.log_to_text([str(i) for i in range(100)],
                                    limit=3) == "97\n98\n99"


class TestSessionReport:

    def test_contains_every_section(self):
        s = EdbgSession()
        stop = _stop()
        s.last_hit = parse.HitInfo(hit_number=1, pid=12345, tid=12350)
        s.last_regs = stop.regs
        s.last_disasm = stop.disasm
        s.last_backtrace = stop.backtrace
        s.last_threads = stop.threads
        s.last_breakpoints = stop.breakpoints
        s.last_memory = stop.memory

        text = textdump.session_to_text(s)
        for section in ("REGISTERS", "DISASM", "BACKTRACE", "MEMORY",
                        "BREAKPOINTS", "THREADS"):
            assert f"--- {section} ---" in text
        assert "[Hit #1] pid=12345 tid=12350" in text

    def test_handles_no_hit(self):
        assert "(no hit)" in textdump.session_to_text(EdbgSession())
