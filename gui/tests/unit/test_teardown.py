"""Device-side teardown: eDBG must never survive a session.

`adb shell <cmd>` allocates no PTY, so the device-side process receives no
SIGHUP when the host client disconnects — adbd reparents it and it runs
forever, holding its eBPF programs and hardware debug registers. Killing the
local adb client is not enough; teardown must target the device PID.
"""

from __future__ import annotations

from unittest import mock

from gui import adb_pty
from gui.adb_pty import AdbPty


def _mock_proc(alive: bool = True):
    proc = mock.MagicMock()
    proc.pid = 12345
    proc.stdin = mock.MagicMock()
    proc.stdout = mock.MagicMock()
    proc.stdout.readline = lambda: b""
    proc.poll = mock.MagicMock(return_value=None if alive else 0)
    proc.wait = mock.MagicMock(return_value=0)
    return proc


# ── pid parsing ──────────────────────────────────────────────────────

class TestParseEdbgPid:

    def test_parses_configmap_banner(self):
        line = "ConfigMap{edbg_pid=30695,thread_whitelist=0}"
        assert AdbPty._parse_edbg_pid(line) == 30695

    def test_ignores_debuggee_hit_banner(self):
        """`pid=` in the hit banner is the *target app*, not eDBG.

        A loose `pid=(\\d+)` pattern matches here; killing that pid would
        terminate the user's app instead of the debugger.
        """
        assert AdbPty._parse_edbg_pid("pid=12345 tid=12350") is None

    def test_ignores_unrelated_lines(self):
        for line in ("HW breakpoint set at 0x7be1d28d24",
                     "uid => whitelist:[10398];blacklist:[]",
                     "[Hit #3]"):
            assert AdbPty._parse_edbg_pid(line) is None

    def test_reader_populates_device_pid(self):
        """Regression: the parse result used to be computed and discarded."""
        banner = b"ConfigMap{edbg_pid=777,thread_whitelist=0}\n"
        proc = _mock_proc()
        lines = iter([banner])
        proc.stdout.readline = lambda: next(lines, b"")

        with mock.patch("gui.adb_pty.subprocess.Popen", return_value=proc):
            pty = AdbPty()
            pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
            pty._reader_thread.join(timeout=2)
            assert pty._device_pid == 777


# ── device pid listing ───────────────────────────────────────────────

class TestDeviceEdbgPids:

    def test_parses_ps_output(self):
        out = ("31198 eDBG -pipe -prefer hardware -p com.x -l lib.so\n"
               "31200 eDBG -pipe\n")
        with mock.patch("gui.adb_pty._su", return_value=out):
            assert adb_pty.device_edbg_pids() == [31198, 31200]

    def test_empty_when_none_running(self):
        with mock.patch("gui.adb_pty._su", return_value=""):
            assert adb_pty.device_edbg_pids() == []

    def test_skips_non_numeric_rows(self):
        with mock.patch("gui.adb_pty._su", return_value="PID ARGS\nfoo bar\n"):
            assert adb_pty.device_edbg_pids() == []


# ── teardown ─────────────────────────────────────────────────────────

class TestStopKillsDevice:

    def test_sends_quit_before_killing(self):
        """`quit` runs Go's CleanUp()/StopProbes(); kill -9 skips it and
        leaks the probes at kernel level."""
        proc = _mock_proc(alive=True)
        with mock.patch("gui.adb_pty.subprocess.Popen", return_value=proc), \
             mock.patch("gui.adb_pty._su", return_value="") as su:
            pty = AdbPty()
            pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
            pty._device_pid = 999
            pty.stop(quit_timeout=0.05)

        proc.stdin.write.assert_any_call(b"quit\n")
        # graceful quit first, then escalation
        assert any("kill -TERM 999" in c.args[0] for c in su.call_args_list)

    def test_no_quit_when_process_already_exited(self):
        proc = _mock_proc(alive=False)
        with mock.patch("gui.adb_pty.subprocess.Popen", return_value=proc), \
             mock.patch("gui.adb_pty._su", return_value=""):
            pty = AdbPty()
            pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
            pty._device_pid = 999
            pty.stop(quit_timeout=0.05)

        assert mock.call(b"quit\n") not in proc.stdin.write.call_args_list

    def test_escalates_to_sigkill_when_sigterm_ignored(self):
        proc = _mock_proc(alive=True)
        # still listed after SIGTERM, then gone after SIGKILL
        with mock.patch("gui.adb_pty.subprocess.Popen", return_value=proc), \
             mock.patch("gui.adb_pty._su", return_value="") as su, \
             mock.patch("gui.adb_pty.device_edbg_pids",
                        side_effect=[[999], []]):
            pty = AdbPty()
            pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
            pty._device_pid = 999
            assert pty.stop(quit_timeout=0.05) is True

        sent = [c.args[0] for c in su.call_args_list]
        assert any("kill -TERM 999" in s for s in sent)
        assert any("kill -9 999" in s for s in sent)

    def test_reports_failure_when_process_survives(self):
        proc = _mock_proc(alive=False)
        with mock.patch("gui.adb_pty.subprocess.Popen", return_value=proc), \
             mock.patch("gui.adb_pty._su", return_value=""), \
             mock.patch("gui.adb_pty.device_edbg_pids", return_value=[999]):
            pty = AdbPty()
            pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
            pty._device_pid = 999
            assert pty.stop() is False

    def test_never_sweeps_other_instances(self):
        """Teardown targets only its own pid — a blanket sweep would kill
        eDBG instances the user started independently."""
        proc = _mock_proc(alive=False)
        with mock.patch("gui.adb_pty.subprocess.Popen", return_value=proc), \
             mock.patch("gui.adb_pty._su", return_value="") as su:
            pty = AdbPty()
            pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
            pty._device_pid = None      # banner never seen
            pty.stop()

        for call in su.call_args_list:
            assert "kill" not in call.args[0]

    def test_deregisters_from_live_set(self):
        proc = _mock_proc(alive=False)
        with mock.patch("gui.adb_pty.subprocess.Popen", return_value=proc), \
             mock.patch("gui.adb_pty._su", return_value=""):
            pty = AdbPty()
            pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
            assert pty in adb_pty._LIVE
            pty.stop()
            assert pty not in adb_pty._LIVE

    def test_idempotent(self):
        proc = _mock_proc(alive=False)
        with mock.patch("gui.adb_pty.subprocess.Popen", return_value=proc), \
             mock.patch("gui.adb_pty._su", return_value=""):
            pty = AdbPty()
            pty.start(package="com.a.b", lib="liba.so", breaks=["0x10"])
            pty.stop()
            assert pty.stop() is True


class TestSweep:

    def test_kills_all_stale(self):
        with mock.patch("gui.adb_pty.device_edbg_pids", return_value=[1, 2]), \
             mock.patch("gui.adb_pty._su", return_value="") as su:
            assert adb_pty.sweep_stale_device_processes() == [1, 2]
        assert any("kill -9 1 2" in c.args[0] for c in su.call_args_list)

    def test_noop_when_clean(self):
        with mock.patch("gui.adb_pty.device_edbg_pids", return_value=[]), \
             mock.patch("gui.adb_pty._su", return_value="") as su:
            assert adb_pty.sweep_stale_device_processes() == []
        su.assert_not_called()

    def test_uses_ps_not_pgrep(self):
        """`pgrep -f eDBG` matches its own command line and self-reports."""
        with mock.patch("gui.adb_pty._su", return_value="") as su:
            adb_pty.device_edbg_pids()
        cmd = su.call_args[0][0]
        assert "pgrep" not in cmd
        assert "[e]DBG" in cmd
