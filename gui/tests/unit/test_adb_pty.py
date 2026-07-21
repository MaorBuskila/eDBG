"""Unit tests for gui.adb_pty — AdbPty subprocess lifecycle.

Every test mocks subprocess.Popen so no real device is needed.
"""
import queue
import subprocess
import time
import threading
from unittest import mock

import pytest

from gui.adb_pty import AdbPty


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_proc(stdout_lines: list[bytes] | None = None,
                    returncode: int | None = None):
    """Build a mock Popen that behaves enough for AdbPty."""
    proc = mock.MagicMock()  # no spec — we need free attribute access
    proc.pid = 12345
    proc.returncode = returncode

    # stdout is a file-like that readline() reads from
    if stdout_lines is None:
        stdout_lines = []
    line_iter = iter(stdout_lines)

    def _readline():
        try:
            return next(line_iter)
        except StopIteration:
            return b""

    proc.stdout = mock.MagicMock()
    proc.stdout.readline = _readline

    # stdin is a file-like with write() and flush()
    proc.stdin = mock.MagicMock()

    # poll() returns None while alive, returncode when dead
    proc.poll = mock.MagicMock(return_value=returncode)

    # terminate / kill
    proc.terminate = mock.MagicMock()
    proc.kill = mock.MagicMock()
    proc.wait = mock.MagicMock(return_value=0)

    return proc


# ---------------------------------------------------------------------------
# 1) start() constructs the correct command line
# ---------------------------------------------------------------------------

class TestStart:
    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_command_has_prefer_hardware(self, mock_popen):
        mock_popen.return_value = _make_mock_proc()
        pty = AdbPty()
        pty.start(package="com.example.app", lib="libnative.so",
                  breaks=["0x1234", "0x5678"])

        args = mock_popen.call_args
        cmd = args[0][0] if args[0] else args[1].get("args")
        cmd_str = " ".join(cmd)
        assert "-prefer hardware" in cmd_str
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_command_has_b_offsets(self, mock_popen):
        mock_popen.return_value = _make_mock_proc()
        pty = AdbPty()
        pty.start(package="com.example.app", lib="libnative.so",
                  breaks=["0x1234", "0x5678"])

        args = mock_popen.call_args
        cmd = args[0][0] if args[0] else args[1].get("args")
        cmd_str = " ".join(cmd)
        # Must use -b, not -vb
        assert "-b 0x1234,0x5678" in cmd_str
        assert "-vb" not in cmd_str
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_command_has_package_and_lib(self, mock_popen):
        mock_popen.return_value = _make_mock_proc()
        pty = AdbPty()
        pty.start(package="com.foo.bar", lib="libfoo.so",
                  breaks=["0xABC"])

        args = mock_popen.call_args
        cmd = args[0][0] if args[0] else args[1].get("args")
        cmd_str = " ".join(cmd)
        assert "-p com.foo.bar" in cmd_str
        assert "-l libfoo.so" in cmd_str
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_command_uses_su(self, mock_popen):
        mock_popen.return_value = _make_mock_proc()
        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        args = mock_popen.call_args
        cmd = args[0][0] if args[0] else args[1].get("args")
        cmd_str = " ".join(cmd)
        assert "su -c" in cmd_str or "su" in cmd
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_command_includes_device_path(self, mock_popen):
        mock_popen.return_value = _make_mock_proc()
        pty = AdbPty(device_bin_path="/data/local/tmp/eDBG_custom")
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        args = mock_popen.call_args
        cmd = args[0][0] if args[0] else args[1].get("args")
        cmd_str = " ".join(cmd)
        assert "/data/local/tmp/eDBG_custom" in cmd_str
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_extra_flags_appended(self, mock_popen):
        mock_popen.return_value = _make_mock_proc()
        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"],
                  extra_flags=["--verbose", "--no-color"])

        args = mock_popen.call_args
        cmd = args[0][0] if args[0] else args[1].get("args")
        cmd_str = " ".join(cmd)
        assert "--verbose" in cmd_str
        assert "--no-color" in cmd_str
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_start_sets_is_alive(self, mock_popen):
        proc = _make_mock_proc()
        proc.poll.return_value = None  # still running
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
        assert pty.is_alive is True
        pty.stop()


# ---------------------------------------------------------------------------
# 2) send() queues commands and they reach stdin
# ---------------------------------------------------------------------------

class TestSend:
    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_send_writes_to_stdin(self, mock_popen):
        proc = _make_mock_proc()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        pty.send("registers")
        # Give writer thread time to drain the queue
        time.sleep(0.2)

        # Check that stdin.write was called with the line + newline
        calls = proc.stdin.write.call_args_list
        written = b"".join(c[0][0] for c in calls)
        assert b"registers\n" in written
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_send_multiple_commands(self, mock_popen):
        proc = _make_mock_proc()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        pty.send("cmd1")
        pty.send("cmd2")
        pty.send("cmd3")
        time.sleep(0.3)

        calls = proc.stdin.write.call_args_list
        written = b"".join(c[0][0] for c in calls)
        assert b"cmd1\n" in written
        assert b"cmd2\n" in written
        assert b"cmd3\n" in written
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_send_is_nonblocking(self, mock_popen):
        proc = _make_mock_proc()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        t0 = time.monotonic()
        pty.send("fast")
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1, "send() should be non-blocking"
        pty.stop()


# ---------------------------------------------------------------------------
# 3) poll_output() returns buffered lines without blocking
# ---------------------------------------------------------------------------

class TestPollOutput:
    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_poll_returns_lines(self, mock_popen):
        lines = [b"line1\n", b"line2\n", b""]
        proc = _make_mock_proc(stdout_lines=lines)
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        # Give reader thread time to buffer
        time.sleep(0.3)
        output = pty.poll_output()
        assert "line1" in output
        assert "line2" in output
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_poll_returns_empty_when_nothing(self, mock_popen):
        proc = _make_mock_proc(stdout_lines=[b""])
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        time.sleep(0.1)
        output = pty.poll_output()
        assert output == []
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_poll_is_nonblocking(self, mock_popen):
        proc = _make_mock_proc(stdout_lines=[b""])
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        t0 = time.monotonic()
        pty.poll_output()
        elapsed = time.monotonic() - t0
        assert elapsed < 0.1, "poll_output() should be non-blocking"
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_poll_drains_buffer(self, mock_popen):
        """Second poll should return empty after first drained everything."""
        lines = [b"a\n", b"b\n", b""]
        proc = _make_mock_proc(stdout_lines=lines)
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        time.sleep(0.3)
        first = pty.poll_output()
        second = pty.poll_output()
        assert len(first) >= 1
        assert second == []
        pty.stop()


# ---------------------------------------------------------------------------
# 4) interrupt() sends Ctrl+C then falls back to kill
# ---------------------------------------------------------------------------

class TestInterrupt:
    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_interrupt_sends_ctrl_c(self, mock_popen):
        proc = _make_mock_proc()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        pty.interrupt(kill_timeout=0.1)

        # Ctrl+C byte should have been written to stdin
        calls = proc.stdin.write.call_args_list
        written_bytes = [c[0][0] for c in calls]
        assert b"\x03" in written_bytes
        pty.stop()

    @mock.patch("gui.adb_pty.subprocess.run")
    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_interrupt_kills_after_timeout(self, mock_popen, mock_run):
        """If process still alive after Ctrl+C, kill via adb shell."""
        proc = _make_mock_proc()
        # Always returns None => still alive
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
        pty._device_pid = 9999  # simulate known PID

        pty.interrupt(kill_timeout=0.1)

        # Should have attempted kill via adb shell
        kill_calls = [c for c in mock_run.call_args_list
                      if any("kill" in str(a) for a in c[0])]
        assert len(kill_calls) >= 1
        pty.stop()


# ---------------------------------------------------------------------------
# 5) stop() terminates and joins cleanly
# ---------------------------------------------------------------------------

class TestStop:
    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_stop_terminates_process(self, mock_popen):
        proc = _make_mock_proc()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
        pty.stop()

        proc.terminate.assert_called()

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_stop_sets_not_alive(self, mock_popen):
        proc = _make_mock_proc()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
        # After stop, the proc's poll returns non-None
        proc.poll.return_value = -15
        pty.stop()
        assert pty.is_alive is False

    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_stop_idempotent(self, mock_popen):
        proc = _make_mock_proc()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
        proc.poll.return_value = -15
        pty.stop()
        pty.stop()  # Should not raise


# ---------------------------------------------------------------------------
# 6) push_binary() calls correct adb push + chmod
# ---------------------------------------------------------------------------

class TestPushBinary:
    @mock.patch("gui.adb_pty.subprocess.run")
    def test_push_calls_adb_push_and_chmod(self, mock_run):
        mock_run.return_value = mock.MagicMock(returncode=0)

        pty = AdbPty(local_bin_path="/host/path/eDBG",
                     device_bin_path="/data/local/tmp/eDBG")
        result = pty.push_binary()

        assert result is True
        assert mock_run.call_count == 2

        # First call: adb push
        push_cmd = mock_run.call_args_list[0][0][0]
        assert "adb" in push_cmd
        assert "push" in push_cmd
        assert "/host/path/eDBG" in push_cmd
        assert "/data/local/tmp/eDBG" in push_cmd

        # Second call: adb shell chmod
        chmod_cmd = mock_run.call_args_list[1][0][0]
        chmod_str = " ".join(chmod_cmd)
        assert "chmod" in chmod_str
        assert "755" in chmod_str
        assert "/data/local/tmp/eDBG" in chmod_str

    @mock.patch("gui.adb_pty.subprocess.run")
    def test_push_returns_false_on_failure(self, mock_run):
        mock_run.return_value = mock.MagicMock(returncode=1)

        pty = AdbPty(local_bin_path="/host/path/eDBG")
        result = pty.push_binary()

        assert result is False

    def test_push_raises_without_local_path(self):
        pty = AdbPty(local_bin_path=None)
        with pytest.raises(ValueError):
            pty.push_binary()

    @mock.patch("gui.adb_pty.subprocess.run")
    def test_push_uses_env_var(self, mock_run, monkeypatch):
        monkeypatch.setenv("EDBG_GUI_BIN", "/env/bin/eDBG")
        mock_run.return_value = mock.MagicMock(returncode=0)

        pty = AdbPty()  # no local_bin_path, should pick up env var
        result = pty.push_binary()
        assert result is True

        push_cmd = mock_run.call_args_list[0][0][0]
        assert "/env/bin/eDBG" in push_cmd


# ---------------------------------------------------------------------------
# 7) pull_file() calls correct adb pull
# ---------------------------------------------------------------------------

class TestPullFile:
    @mock.patch("gui.adb_pty.subprocess.run")
    def test_pull_calls_adb_pull(self, mock_run):
        mock_run.return_value = mock.MagicMock(returncode=0)

        pty = AdbPty()
        result = pty.pull_file("/data/local/tmp/flow.csv",
                               "/host/downloads/flow.csv")

        assert result is True
        cmd = mock_run.call_args[0][0]
        cmd_str = " ".join(cmd)
        assert "adb" in cmd_str
        assert "pull" in cmd_str
        assert "/data/local/tmp/flow.csv" in cmd_str
        assert "/host/downloads/flow.csv" in cmd_str

    @mock.patch("gui.adb_pty.subprocess.run")
    def test_pull_returns_false_on_failure(self, mock_run):
        mock_run.return_value = mock.MagicMock(returncode=1)

        pty = AdbPty()
        result = pty.pull_file("/dev/null", "/tmp/out")
        assert result is False


# ---------------------------------------------------------------------------
# Environment variable tests
# ---------------------------------------------------------------------------

class TestEnvVars:
    @mock.patch("gui.adb_pty.subprocess.Popen")
    def test_edbg_device_path_env(self, mock_popen, monkeypatch):
        monkeypatch.setenv("EDBG_DEVICE_PATH", "/custom/device/path")
        proc = _make_mock_proc()
        proc.poll.return_value = None
        mock_popen.return_value = proc

        pty = AdbPty()  # no explicit device_bin_path
        assert pty._device_bin_path == "/custom/device/path"
        pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])

        args = mock_popen.call_args
        cmd = args[0][0] if args[0] else args[1].get("args")
        cmd_str = " ".join(cmd)
        assert "/custom/device/path" in cmd_str
        pty.stop()
