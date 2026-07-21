"""Sentinel framing + event decoding. Must run with dearpygui absent."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest

from gui import events, protocol

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("module", [
    "gui.protocol", "gui.events", "gui.parse", "gui.session", "gui.adb_pty",
])
def test_imports_without_dearpygui(module):
    """The event path must stay headless-testable.

    DPG segfaults on import where there is no window server, which would take
    CI down with it. Checked in a clean subprocess with dearpygui poisoned —
    asserting on this process's ``sys.modules`` would be order-dependent,
    since other tests import ``gui.app`` (and therefore DPG).
    """
    repo = str(pathlib.Path(__file__).resolve().parents[3])
    code = (
        "import sys;"
        "sys.path.insert(0, %r);"
        "sys.modules['dearpygui'] = None;"
        "sys.modules['dearpygui.dearpygui'] = None;"
        "import %s;"
        "print('ok')" % (repo, module)
    )
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True)
    assert out.returncode == 0, (
        f"{module} pulls in dearpygui:\n{out.stderr}")


# ── framing ──────────────────────────────────────────────────────────

class TestFraming:

    def test_unframed_line_is_log(self):
        out = protocol.decode_line("pid=12345 tid=12350")
        assert isinstance(out, events.LogLine)
        assert out.text == "pid=12345 tid=12350"

    def test_framed_line_is_event(self):
        line = protocol.encode({"t": "hello", "schema": 1})
        assert protocol.is_event(line)
        assert isinstance(protocol.decode_line(line), events.Hello)

    def test_sentinel_is_non_printable(self):
        """Must not collide with real REPL output."""
        assert protocol.SENTINEL.startswith("\x01")

    def test_empty_line_is_log(self):
        assert isinstance(protocol.decode_line(""), events.LogLine)

    def test_round_trip(self):
        obj = {"t": "flow_done", "steps": 12, "csv_path": "/tmp/x.csv"}
        ev = protocol.decode_line(protocol.encode(obj))
        assert isinstance(ev, events.FlowDone)
        assert ev.steps == 12 and ev.csv_path == "/tmp/x.csv"


# ── tolerance ────────────────────────────────────────────────────────

class TestMalformed:

    @pytest.mark.parametrize("payload", [
        '{"t":"stop","regs":[',      # truncated
        "not json at all",
        '["array","not","object"]',  # valid JSON, wrong shape
        "",
    ])
    def test_never_raises(self, payload):
        out = protocol.decode_line(protocol.SENTINEL + payload)
        assert isinstance(out, events.Malformed)
        assert out.error

    def test_unknown_type_is_not_fatal(self):
        """A newer eDBG must not break an older GUI."""
        out = protocol.decode_line(
            protocol.encode({"t": "invented_later", "x": 1}))
        assert isinstance(out, events.UnknownEvent)
        assert out.type == "invented_later"


# ── hex-string decoding ──────────────────────────────────────────────

class TestHexValues:

    def test_uint64_survives(self):
        """Values cross the wire as hex strings precisely because a uint64
        does not round-trip safely as a JSON number."""
        big = 0xFFFFFFFFFFFFFFFF
        ev = protocol.decode_line(protocol.encode({
            "t": "stop", "regs": [{"n": "X0", "v": hex(big)}]}))
        assert ev.regs[0].value == big

    def test_decimal_and_int_forms_accepted(self):
        ev = protocol.decode_line(protocol.encode({
            "t": "stop", "pid": 42, "tid": "17"}))
        assert ev.pid == 42 and ev.tid == 17

    def test_bad_hex_does_not_raise(self):
        ev = protocol.decode_line(protocol.encode({
            "t": "stop", "regs": [{"n": "X0", "v": "0xZZZ"}]}))
        assert ev.regs[0].value == 0


# ── stop payload shape ───────────────────────────────────────────────

class TestStopPayload:

    @pytest.fixture
    def stop(self):
        raw = (FIXTURES / "session_basic.ndjson").read_text().splitlines()
        evs = [protocol.decode_line(l) for l in raw]
        return next(e for e in evs if isinstance(e, events.Stop))

    def test_full_register_file(self, stop):
        names = [r.name for r in stop.regs]
        assert names[:3] == ["X0", "X1", "X2"]
        assert names[-3:] == ["LR", "SP", "PC"]
        assert len(names) == 33

    def test_symbol_matches_text_parser_form(self, stop):
        """parse_registers wraps symbols in angle brackets; the event path
        must match so panes render both sources identically."""
        x1 = next(r for r in stop.regs if r.name == "X1")
        assert x1.symbol.startswith("<") and x1.symbol.endswith(">")

    def test_disasm_current_marker(self, stop):
        assert sum(1 for d in stop.disasm if d.is_current) == 1

    def test_disasm_without_symbol_ok(self, stop):
        assert any(d.symbol == "" for d in stop.disasm)

    def test_backtrace(self, stop):
        assert [f.index for f in stop.backtrace] == [0, 1]

    def test_threads_current_flag(self, stop):
        assert [t["is_current"] for t in stop.threads] == [True, False]

    def test_breakpoint_shape_matches_text_parser(self, stop):
        bp = stop.breakpoints[0]
        assert set(bp) == {"id", "enabled", "type", "library", "offset",
                           "address"}

    def test_hardware_bp_carries_address(self, stop):
        hw = next(b for b in stop.breakpoints if b["type"] == "hardware")
        assert hw["address"] == hw["offset"] != 0

    def test_display_and_pointers_both_reach_memory(self, stop):
        assert len(stop.memory) == 2
        assert stop.memory[0][1].startswith(b"Hello World!")


def test_all_fixtures_decode():
    """Every fixture must parse; only the adversarial one may contain
    Malformed entries."""
    for path in sorted(FIXTURES.glob("*.ndjson")):
        decoded = [protocol.decode_line(l)
                   for l in path.read_text().splitlines()]
        assert decoded, f"{path.name} is empty"
        bad = [d for d in decoded if isinstance(d, events.Malformed)]
        if path.name != "session_adversarial.ndjson":
            assert not bad, f"{path.name} has malformed frames"


def test_fixture_json_is_single_line_per_event():
    """One event per line — a wrapped frame would break the reader."""
    for path in sorted(FIXTURES.glob("*.ndjson")):
        for line in path.read_text().splitlines():
            if line.startswith(protocol.SENTINEL):
                payload = line[len(protocol.SENTINEL):]
                if payload.startswith("{") and payload.rstrip().endswith("}"):
                    json.loads(payload)   # must not need any other line
