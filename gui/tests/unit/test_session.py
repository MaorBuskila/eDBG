"""Unit tests for gui.session — mock AdbPty, feed sample output."""

from unittest.mock import MagicMock, patch
import pytest

from gui.session import EdbgSession, State


@pytest.fixture
def session():
    return EdbgSession()


class TestLifecycle:
    @patch("gui.session.AdbPty")
    def test_start_transitions_to_attached(self, MockPty, session):
        session.start("com.test", "lib.so", ["0x1234"])
        assert session.state == State.ATTACHED
        assert session.config.package == "com.test"

    @patch("gui.session.AdbPty")
    def test_start_passes_correct_args(self, MockPty, session):
        session.start("com.pkg", "libfoo.so", ["0x100", "0x200"])
        MockPty.return_value.start.assert_called_once_with(
            "com.pkg", "libfoo.so", ["0x100", "0x200"]
        )

    @patch("gui.session.AdbPty")
    def test_stop_transitions_to_disconnected(self, MockPty, session):
        session.start("com.test", "lib.so", ["0x1234"])
        session.stop()
        assert session.state == State.DISCONNECTED

    @patch("gui.session.AdbPty")
    def test_start_error_transitions_to_error(self, MockPty, session):
        MockPty.return_value.start.side_effect = RuntimeError("no adb")
        session.start("com.test", "lib.so", ["0x1234"])
        assert session.state == State.ERROR


class TestSendCommand:
    @patch("gui.session.AdbPty")
    def test_send_delegates_to_pty(self, MockPty, session):
        session.start("com.test", "lib.so", ["0x1234"])
        session.send_command("info reg")
        MockPty.return_value.send.assert_called_once_with("info reg")

    @patch("gui.session.AdbPty")
    def test_send_appends_transcript(self, MockPty, session):
        session.start("com.test", "lib.so", ["0x1234"])
        session.send_command("c")
        assert "(eDBG) c" in session.transcript

    @patch("gui.session.AdbPty")
    def test_continue_sends_c(self, MockPty, session):
        session.start("com.test", "lib.so", ["0x1234"])
        session.continue_()
        MockPty.return_value.send.assert_called_with("c")
        assert session.state == State.RUNNING


class TestPollAndParse:
    @patch("gui.session.AdbPty")
    def test_empty_poll_returns_empty(self, MockPty, session):
        MockPty.return_value.poll_output.return_value = []
        MockPty.return_value.is_alive = True
        session.start("com.test", "lib.so", ["0x1234"])
        result = session.poll_and_parse()
        assert result == []

    @patch("gui.session.AdbPty")
    def test_hit_detection(self, MockPty, session):
        hit_lines = [
            "\x1b[33m[Hit #1]\x1b[0m",
            "\x1b[36mpid=1234 tid=5678\x1b[0m",
            "──────────────────────────────────────[ REGISTERS ]──────────────────────────────────────",
            " X0\t0x0",
            " X1\t0x1",
            "*PC\t0x7B80001234",
            "──────────────────────────────────────[  DISASM  ]────────────────────────────────────────",
            ">>  0x7b80001234<libfoo.so+0x1234>\tMOV X0, X1",
            "    0x7b80001238<libfoo.so+0x1238>\tRET",
            "─────────────────────────────────────────────────────────────────────────────────────────",
        ]
        MockPty.return_value.poll_output.return_value = hit_lines
        MockPty.return_value.is_alive = True
        session.start("com.test", "lib.so", ["0x1234"])
        session.state = State.RUNNING

        new_lines = session.poll_and_parse()

        assert session.state == State.STOPPED
        assert session.last_hit is not None
        assert session.last_hit.hit_number == 1
        assert session.last_hit.pid == 1234
        assert session.last_hit.tid == 5678
        assert len(session.last_regs) >= 2  # at least X0 and PC
        assert len(session.last_disasm) == 2
        assert session.last_disasm[0].is_current is True
        assert len(new_lines) == len(hit_lines)

    @patch("gui.session.AdbPty")
    def test_process_exit_detected(self, MockPty, session):
        MockPty.return_value.poll_output.return_value = []
        MockPty.return_value.is_alive = False
        session.start("com.test", "lib.so", ["0x1234"])

        session.poll_and_parse()
        assert session.state == State.DISCONNECTED

    @patch("gui.session.AdbPty")
    def test_transcript_accumulates(self, MockPty, session):
        MockPty.return_value.poll_output.return_value = ["line1", "line2"]
        MockPty.return_value.is_alive = True
        session.start("com.test", "lib.so", ["0x1234"])
        session.poll_and_parse()
        assert "line1" in session.transcript
        assert "line2" in session.transcript


class TestInterrupt:
    @patch("gui.session.AdbPty")
    def test_interrupt_delegates(self, MockPty, session):
        session.start("com.test", "lib.so", ["0x1234"])
        session.interrupt()
        MockPty.return_value.interrupt.assert_called_once()
