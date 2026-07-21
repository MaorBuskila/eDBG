"""Replay mode: drive a whole session from a recorded transcript.

Without this the remaining v2 work needs a rooted device physically attached
and none of it can run in CI. The tests below deliberately blank PATH for the
no-adb case, proving replay never shells out.
"""

from __future__ import annotations

import pathlib

import pytest

from gui import events
from gui.session import EdbgSession, State

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _run(monkeypatch, fixture: str, polls: int = 200) -> EdbgSession:
    monkeypatch.setenv("EDBG_GUI_REPLAY", str(FIXTURES / fixture))
    monkeypatch.delenv("EDBG_GUI_BIN", raising=False)
    s = EdbgSession()
    s.start("com.example.app", "libloader.so", ["0x12a40c"])
    for _ in range(polls):
        s.poll_and_parse()
        if s._pty is not None and s._pty.replay_finished:
            break
    s.poll_and_parse()
    return s


class TestReplayBasic:

    def test_reaches_stopped_state(self, monkeypatch):
        s = _run(monkeypatch, "session_basic.ndjson")
        assert s.state is State.STOPPED

    def test_populates_every_pane(self, monkeypatch):
        s = _run(monkeypatch, "session_basic.ndjson")
        assert len(s.last_regs) == 33
        assert len(s.last_disasm) == 3
        assert len(s.last_backtrace) == 2
        assert len(s.last_threads) == 2
        assert len(s.last_breakpoints) == 2
        assert len(s.last_memory) == 2

    def test_hit_metadata(self, monkeypatch):
        s = _run(monkeypatch, "session_basic.ndjson")
        assert s.last_hit is not None
        assert (s.last_hit.hit_number, s.last_hit.pid, s.last_hit.tid) == \
            (1, 12345, 12350)

    def test_raw_json_never_reaches_the_log(self, monkeypatch):
        """Event frames are data, not transcript text."""
        s = _run(monkeypatch, "session_basic.ndjson")
        assert not any('{"t":' in line for line in s.transcript)

    def test_human_lines_still_reach_the_log(self, monkeypatch):
        s = _run(monkeypatch, "session_basic.ndjson")
        assert any("ConfigMap{" in line for line in s.transcript)

    def test_no_adb_binary_required(self, monkeypatch):
        """Blank PATH: replay must never shell out."""
        monkeypatch.setenv("PATH", "")
        s = _run(monkeypatch, "session_basic.ndjson")
        assert s.state is State.STOPPED
        s.stop()


class TestReplayMultiStop:

    def test_last_stop_wins(self, monkeypatch):
        s = _run(monkeypatch, "session_multi_stop.ndjson")
        assert s.last_hit.hit_number == 5

    def test_state_not_polluted_across_stops(self, monkeypatch):
        """Each stop replaces pane state wholesale — the text path's
        `.extend()` duplicate accumulation must not reappear."""
        s = _run(monkeypatch, "session_multi_stop.ndjson")
        assert len(s.last_regs) == 33
        assert len(s.last_backtrace) == 2
        assert len(s.last_threads) == 2


class TestReplayFlow:

    def test_progress_and_completion(self, monkeypatch):
        s = _run(monkeypatch, "session_flow.ndjson")
        assert s.flow_done is not None
        assert s.flow_done.steps == 9999
        assert s.flow_done.csv_path.endswith(".csv")

    def test_completion_announced_in_log(self, monkeypatch):
        s = _run(monkeypatch, "session_flow.ndjson")
        assert any("flow finished" in l for l in s.transcript)

    def test_steps_do_not_flood_the_log(self, monkeypatch):
        """10k flow steps must not become 10k log lines — that firehose is
        what made the GUI unusable during a trace."""
        s = _run(monkeypatch, "session_flow.ndjson")
        assert len(s.transcript) < 50


class TestReplayCommands:

    def test_standalone_responses(self, monkeypatch):
        s = _run(monkeypatch, "session_commands.ndjson")
        assert len(s.last_breakpoints) == 1
        assert len(s.last_threads) == 1
        assert len(s.last_memory) == 1

    def test_error_surfaces_in_log(self, monkeypatch):
        s = _run(monkeypatch, "session_commands.ndjson")
        assert any("error:" in l for l in s.transcript)


class TestReplayAdversarial:

    def test_survives_corrupt_frames(self, monkeypatch):
        """A bad frame must degrade to a diagnostic, not kill the session."""
        s = _run(monkeypatch, "session_adversarial.ndjson")
        assert s.state is State.STOPPED
        assert s.last_hit.hit_number == 2

    def test_reports_malformed(self, monkeypatch):
        s = _run(monkeypatch, "session_adversarial.ndjson")
        assert any("malformed event" in l for l in s.transcript)

    def test_reports_unknown_type(self, monkeypatch):
        s = _run(monkeypatch, "session_adversarial.ndjson")
        assert any("unknown event" in l for l in s.transcript)

    def test_plain_lines_survive(self, monkeypatch):
        s = _run(monkeypatch, "session_adversarial.ndjson")
        assert any("plain log line" in l for l in s.transcript)


class TestReplayLifecycle:

    def test_missing_file_does_not_raise(self, monkeypatch):
        monkeypatch.setenv("EDBG_GUI_REPLAY", "/nonexistent/nope.ndjson")
        s = EdbgSession()
        s.start("com.example.app", "libloader.so", [])
        for _ in range(5):
            s.poll_and_parse()
        s.stop()

    def test_stop_is_clean(self, monkeypatch):
        monkeypatch.setenv("PATH", "")
        s = _run(monkeypatch, "session_basic.ndjson")
        assert s.stop() is None
        assert s.state is State.DISCONNECTED

    def test_text_scraper_disabled_once_structured(self, monkeypatch):
        s = _run(monkeypatch, "session_basic.ndjson")
        assert s._structured is True
        assert s._pending_hit is None
