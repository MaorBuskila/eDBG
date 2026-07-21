"""Tests for gui.parse — the eDBG REPL output parser."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gui.parse import (
    BTFrame,
    DisasmLine,
    HitInfo,
    RegisterInfo,
    parse_backtrace,
    parse_breakpoints,
    parse_disasm,
    parse_hit,
    parse_memory,
    parse_registers,
    parse_threads,
    split_sections,
    strip_ansi,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


def _fixture_lines(name: str) -> list[str]:
    return _read_fixture(name).splitlines()


# ── strip_ansi ──────────────────────────────────────────────────────────────


class TestStripAnsi:
    def test_removes_colour_codes(self):
        text = "\x1b[0;33m[Hit #1]\x1b[0m"
        assert strip_ansi(text) == "[Hit #1]"

    def test_preserves_plain_text(self):
        assert strip_ansi("hello world") == "hello world"

    def test_multiple_codes(self):
        text = "\x1b[0;31m*X0\x1b[0m\t\x1b[0;36m0x1234\x1b[0m"
        assert strip_ansi(text) == "*X0\t0x1234"

    def test_empty_string(self):
        assert strip_ansi("") == ""


# ── parse_hit ───────────────────────────────────────────────────────────────


class TestParseHit:
    def test_basic_hit(self):
        lines = _fixture_lines("hit_output.txt")
        hit = parse_hit(lines)
        assert hit is not None
        assert hit == HitInfo(hit_number=3, pid=12345, tid=67890)

    def test_with_ansi(self):
        lines = [
            "\x1b[0;33m[Hit #7]\x1b[0m",
            "\x1b[0;36mpid=999 tid=1001\x1b[0m",
        ]
        hit = parse_hit(lines)
        assert hit is not None
        assert hit.hit_number == 7
        assert hit.pid == 999
        assert hit.tid == 1001

    def test_no_hit(self):
        assert parse_hit(["some random line"]) is None

    def test_partial_no_pid(self):
        assert parse_hit(["[Hit #1]"]) is None


# ── parse_registers ─────────────────────────────────────────────────────────


class TestParseRegisters:
    def test_basic(self):
        lines = _fixture_lines("registers.txt")
        regs = parse_registers(lines)
        assert len(regs) == 8

    def test_x0_deref(self):
        lines = _fixture_lines("registers.txt")
        regs = parse_registers(lines)
        x0 = regs[0]
        assert x0.name == "X0"
        assert x0.value == 0x7B80E12340
        assert x0.symbol == "<libfoo.so+0x1234>"
        assert x0.deref == "0x00000001"

    def test_x1_plain(self):
        lines = _fixture_lines("registers.txt")
        regs = parse_registers(lines)
        x1 = regs[1]
        assert x1.name == "X1"
        assert x1.value == 0x0
        assert x1.symbol == ""
        assert x1.deref is None

    def test_x4_string_deref(self):
        lines = _fixture_lines("registers.txt")
        regs = parse_registers(lines)
        x4 = regs[4]
        assert x4.name == "X4"
        assert x4.deref == "hello world"

    def test_lr(self):
        lines = _fixture_lines("registers.txt")
        regs = parse_registers(lines)
        lr = [r for r in regs if r.name == "LR"][0]
        assert lr.value == 0x7B80E15678
        assert lr.symbol == "<libfoo.so+0x5678>"
        assert lr.deref is None

    def test_sp(self):
        lines = _fixture_lines("registers.txt")
        regs = parse_registers(lines)
        sp = [r for r in regs if r.name == "SP"][0]
        assert sp.value == 0x7FFEABC000
        assert sp.symbol == ""
        assert sp.deref is None

    def test_pc(self):
        lines = _fixture_lines("registers.txt")
        regs = parse_registers(lines)
        pc = [r for r in regs if r.name == "PC"][0]
        assert pc.value == 0x7B80E12340
        assert pc.symbol == "<libfoo.so+0x1234>"

    def test_with_ansi(self):
        lines = [
            "\x1b[0;31m*X0\x1b[0m\t\x1b[0;36m0x7B80E12340\x1b[0m\x1b[0;32m<libfoo.so+0x1234>\x1b[0m ◂— 0x00000001",
        ]
        regs = parse_registers(lines)
        assert len(regs) == 1
        assert regs[0].name == "X0"
        assert regs[0].value == 0x7B80E12340

    def test_empty(self):
        assert parse_registers([]) == []


# ── parse_disasm ────────────────────────────────────────────────────────────


class TestParseDisasm:
    def test_basic(self):
        lines = _fixture_lines("disasm.txt")
        disasm = parse_disasm(lines)
        assert len(disasm) == 4

    def test_current_line(self):
        lines = _fixture_lines("disasm.txt")
        disasm = parse_disasm(lines)
        assert disasm[0].is_current is True
        assert disasm[1].is_current is False

    def test_first_instruction(self):
        lines = _fixture_lines("disasm.txt")
        d = parse_disasm(lines)[0]
        assert d.address == 0x7B80E12340
        assert d.symbol == "libfoo.so+0x1234"
        assert d.mnemonic == "MOV"
        assert d.operands == "X0, X1"

    def test_no_symbol(self):
        lines = _fixture_lines("disasm.txt")
        d = parse_disasm(lines)[3]
        assert d.symbol == ""
        assert d.mnemonic == "NOP"
        assert d.operands == ""

    def test_bl_operand(self):
        lines = _fixture_lines("disasm.txt")
        d = parse_disasm(lines)[1]
        assert d.mnemonic == "BL"
        assert d.operands == "#0x7b80e20000"

    def test_with_ansi(self):
        lines = [
            "\x1b[0;32m>>  0x1000<lib.so+0x100>\x1b[0;32m\t\x1b[0;33mMOV\x1b[0m \x1b[0;36mX0, X1\x1b[0m",
        ]
        disasm = parse_disasm(lines)
        assert len(disasm) == 1
        assert disasm[0].is_current is True
        assert disasm[0].mnemonic == "MOV"

    def test_empty(self):
        assert parse_disasm([]) == []


# ── parse_backtrace ─────────────────────────────────────────────────────────


class TestParseBacktrace:
    def test_basic(self):
        lines = _fixture_lines("backtrace.txt")
        bt = parse_backtrace(lines)
        assert len(bt) == 3

    def test_first_frame(self):
        lines = _fixture_lines("backtrace.txt")
        f = parse_backtrace(lines)[0]
        assert f.index == 0
        assert f.address == 0x79ABCDEF00
        assert f.symbol == "libfoo.so + 0x1234"

    def test_unknown_frame(self):
        lines = _fixture_lines("backtrace.txt")
        f = parse_backtrace(lines)[1]
        assert f.index == 1
        assert f.symbol == "?? ()"

    def test_header_line_skipped(self):
        """The 'Backtrace (most recent call first):' line should not produce a frame."""
        lines = _fixture_lines("backtrace.txt")
        bt = parse_backtrace(lines)
        assert all(isinstance(f, BTFrame) for f in bt)

    def test_empty(self):
        assert parse_backtrace([]) == []


# ── parse_memory ────────────────────────────────────────────────────────────


class TestParseMemory:
    def test_basic(self):
        lines = _fixture_lines("hexdump.txt")
        mem = parse_memory(lines)
        assert len(mem) == 2

    def test_first_line_address(self):
        lines = _fixture_lines("hexdump.txt")
        addr, data = parse_memory(lines)[0]
        assert addr == 0x0000ABCD

    def test_first_line_data(self):
        lines = _fixture_lines("hexdump.txt")
        addr, data = parse_memory(lines)[0]
        assert data == b"Hello World!\x00\x00\x00\x00"

    def test_second_line(self):
        lines = _fixture_lines("hexdump.txt")
        addr, data = parse_memory(lines)[1]
        assert addr == 0x0000ABDD
        assert data == bytes(range(1, 17))

    def test_empty(self):
        assert parse_memory([]) == []


# ── parse_breakpoints ──────────────────────────────────────────────────────


class TestParseBreakpoints:
    def test_basic(self):
        lines = _fixture_lines("breakpoints.txt")
        bps = parse_breakpoints(lines)
        assert len(bps) == 4

    def test_enabled_software(self):
        lines = _fixture_lines("breakpoints.txt")
        bp = parse_breakpoints(lines)[0]
        assert bp["id"] == 0
        assert bp["enabled"] is True
        assert bp["type"] == "software"
        assert bp["library"] == "libfoo.so"
        assert bp["offset"] == 0x1234

    def test_disabled(self):
        lines = _fixture_lines("breakpoints.txt")
        bp = parse_breakpoints(lines)[2]
        assert bp["id"] == 2
        assert bp["enabled"] is False
        assert bp["type"] == "software"

    def test_hardware(self):
        lines = _fixture_lines("breakpoints.txt")
        bp = parse_breakpoints(lines)[3]
        assert bp["id"] == 3
        assert bp["enabled"] is True
        assert bp["type"] == "hardware"
        assert bp["address"] == 0xDEADBEEF

    def test_empty(self):
        assert parse_breakpoints([]) == []


# ── parse_threads ───────────────────────────────────────────────────────────


class TestParseThreads:
    def test_basic(self):
        lines = _fixture_lines("threads.txt")
        threads = parse_threads(lines)
        assert len(threads) == 3

    def test_current_thread(self):
        lines = _fixture_lines("threads.txt")
        threads = parse_threads(lines)
        current = [t for t in threads if t["is_current"]]
        assert len(current) == 1
        assert current[0]["tid"] == 12346
        assert current[0]["name"] == "WorkerThread"

    def test_non_current(self):
        lines = _fixture_lines("threads.txt")
        threads = parse_threads(lines)
        t = threads[0]
        assert t["is_current"] is False
        assert t["index"] == 0
        assert t["tid"] == 12345
        assert t["name"] == "main"

    def test_empty(self):
        assert parse_threads([]) == []


# ── split_sections ──────────────────────────────────────────────────────────


class TestSplitSections:
    def test_full_output(self):
        text = _read_fixture("full_output.txt")
        sections = split_sections(text)
        assert "REGISTERS" in sections
        assert "DISASM" in sections
        assert "DISPLAY" in sections

    def test_header_section(self):
        text = _read_fixture("full_output.txt")
        sections = split_sections(text)
        header = sections.get("HEADER", [])
        # Header should contain hit and pid/tid lines
        header_text = "\n".join(header)
        assert "Hit #1" in header_text
        assert "pid=12345" in header_text

    def test_registers_section_content(self):
        text = _read_fixture("full_output.txt")
        sections = split_sections(text)
        regs = parse_registers(sections["REGISTERS"])
        assert len(regs) >= 3  # X0, X1, X2, LR, SP, PC

    def test_disasm_section_content(self):
        text = _read_fixture("full_output.txt")
        sections = split_sections(text)
        disasm = parse_disasm(sections["DISASM"])
        assert len(disasm) == 2
        assert disasm[0].is_current is True

    def test_display_section_has_hexdump(self):
        text = _read_fixture("full_output.txt")
        sections = split_sections(text)
        mem = parse_memory(sections["DISPLAY"])
        assert len(mem) == 1
        assert mem[0][0] == 0x0000ABCD

    def test_empty_input(self):
        assert split_sections("") == {}

    def test_no_sections(self):
        sections = split_sections("just some text\nno sections here")
        assert "HEADER" in sections
        assert len(sections["HEADER"]) == 2


# ── Integration: round-trip from full output ────────────────────────────────


class TestIntegration:
    def test_full_parse_pipeline(self):
        """Parse a full output blob end-to-end."""
        text = _read_fixture("full_output.txt")
        sections = split_sections(text)

        # Hit
        header = sections.get("HEADER", [])
        hit = parse_hit(header)
        assert hit is not None
        assert hit.hit_number == 1
        assert hit.pid == 12345

        # Registers
        regs = parse_registers(sections["REGISTERS"])
        names = [r.name for r in regs]
        assert "X0" in names
        assert "LR" in names
        assert "PC" in names

        # Disasm
        disasm = parse_disasm(sections["DISASM"])
        assert disasm[0].mnemonic == "MOV"
        assert disasm[0].is_current is True

        # Display (memory)
        mem = parse_memory(sections["DISPLAY"])
        assert len(mem) == 1
