"""ADB PTY — manages the adb subprocess lifecycle for driving eDBG.

Pushes the eDBG binary to device, starts it via ``adb shell su``,
and provides a non-blocking REPL interface (reader thread + writer
thread) so the host GUI never stalls on I/O.
"""

from __future__ import annotations

import atexit
import collections
import logging
import os
import queue
import re
import subprocess
import threading
import time
from typing import Sequence

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_DEVICE_PATH = "/data/local/tmp/eDBG"
_SENTINEL = object()  # poison pill for writer thread

# `adb shell <cmd>` allocates no PTY, so the device-side process gets no SIGHUP
# when the host client disconnects — adbd reparents it and it runs forever,
# holding its eBPF programs and hardware debug registers. Every teardown path
# must kill it explicitly by PID.
#
# Must match `edbg_pid` specifically: a bare `pid=` pattern also matches the
# `pid=<n> tid=<n>` hit banner, which is the *debuggee's* pid. Killing that
# would terminate the user's app.
_EDBG_PID_RE = re.compile(r"ConfigMap\{edbg_pid=(\d+)")

# Every live AdbPty, so a crash or interpreter exit still tears the device down.
_LIVE: "set[AdbPty]" = set()


def _su(command: str, timeout: float = 5.0) -> str:
    """Run *command* as root on the device. Returns stdout ('' on failure)."""
    try:
        return subprocess.run(
            ["adb", "shell", "su", "-c", command],
            capture_output=True, text=True, timeout=timeout,
        ).stdout
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("device command failed (%s): %s", command, exc)
        return ""


def device_edbg_pids() -> list[int]:
    """PIDs of every eDBG running on the device.

    Uses ``ps`` with a bracketed grep rather than ``pgrep -f eDBG`` — the latter
    matches its own command line and always reports a false positive.
    """
    out = _su("ps -A -o PID,ARGS | grep '[e]DBG'")
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if parts and parts[0].isdigit():
            pids.append(int(parts[0]))
    return pids


def sweep_stale_device_processes() -> list[int]:
    """Kill eDBG processes left over from an earlier crashed session.

    A stale instance keeps its hardware debug registers armed and competes with
    the new session for the target's limited breakpoint slots.
    """
    stale = device_edbg_pids()
    if stale:
        log.warning("killing %d stale device-side eDBG process(es): %s",
                    len(stale), stale)
        _su(f"kill -9 {' '.join(str(p) for p in stale)}")
    return stale


@atexit.register
def _teardown_all() -> None:
    for pty in list(_LIVE):
        try:
            pty.stop()
        except Exception:
            pass


class AdbPty:
    """Non-blocking wrapper around an ``adb shell`` subprocess running eDBG."""

    # ── construction ──────────────────────────────────────────────────

    def __init__(
        self,
        device_bin_path: str | None = None,
        local_bin_path: str | None = None,
    ) -> None:
        # Device-side path for the eDBG binary.
        if device_bin_path is not None:
            self._device_bin_path = device_bin_path
        else:
            self._device_bin_path = os.environ.get(
                "EDBG_DEVICE_PATH", _DEFAULT_DEVICE_PATH
            )

        # Host-side path used by push_binary().
        if local_bin_path is not None:
            self._local_bin_path: str | None = local_bin_path
        else:
            self._local_bin_path = os.environ.get("EDBG_GUI_BIN")

        # Replay: feed a recorded NDJSON transcript instead of spawning adb, so
        # the GUI can be developed and tested with no device attached.
        self._replay_path: str | None = os.environ.get("EDBG_GUI_REPLAY")
        self._replay_done = threading.Event()

        # Runtime state
        self._proc: subprocess.Popen | None = None
        self._device_pid: int | None = None
        self._output_buf: collections.deque[str] = collections.deque()
        self._write_q: queue.Queue[str | object] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self._writer_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ── push / pull helpers ───────────────────────────────────────────

    def push_binary(self) -> bool:
        """Push the local eDBG binary to the device and make it executable.

        Returns ``True`` on success, ``False`` if *adb push* or *chmod*
        fails.  Raises ``ValueError`` when no local binary path is known.
        """
        local = self._local_bin_path
        if local is None:
            raise ValueError(
                "No local binary path — set local_bin_path or EDBG_GUI_BIN"
            )

        device = self._device_bin_path
        ret = subprocess.run(
            ["adb", "push", local, device],
            capture_output=True,
        )
        if ret.returncode != 0:
            log.error("adb push failed: %s", ret.stderr)
            return False

        ret = subprocess.run(
            ["adb", "shell", "chmod", "755", device],
            capture_output=True,
        )
        if ret.returncode != 0:
            log.error("chmod failed: %s", ret.stderr)
            return False

        return True

    def pull_file(self, device_path: str, local_path: str) -> bool:
        """Pull *device_path* to *local_path* via ``adb pull``.

        Returns ``True`` on success.
        """
        ret = subprocess.run(
            ["adb", "pull", device_path, local_path],
            capture_output=True,
        )
        if ret.returncode != 0:
            log.error("adb pull failed: %s", ret.stderr)
            return False
        return True

    # ── start / stop ──────────────────────────────────────────────────

    def start(
        self,
        package: str,
        lib: str,
        breaks: list[str],
        extra_flags: list[str] | None = None,
    ) -> None:
        """Launch eDBG on the device.

        Parameters
        ----------
        package:
            Android package name (``-n``).
        lib:
            Shared-library name (``-l``).
        breaks:
            Hex offset strings (e.g. ``['0x1234', '0x5678']``) joined with
            commas and passed as ``-b``.
        extra_flags:
            Any additional CLI flags to append.
        """
        offsets = ",".join(breaks)

        # Build the on-device command as a single string for su -c '...'
        edbg_cmd_parts: list[str] = [
            self._device_bin_path,
            "-pipe",
            "-prefer", "hardware",
            "-n", package,
            "-l", lib,
            "-b", offsets,
        ]
        if extra_flags:
            edbg_cmd_parts.extend(extra_flags)

        edbg_cmd = " ".join(edbg_cmd_parts)
        cmd: list[str] = [
            "adb", "shell", "su", "-c", edbg_cmd,
        ]

        if self._replay_path:
            log.info("Replaying %s (no device)", self._replay_path)
            self._start_replay()
            return

        log.info("Starting eDBG: %s", " ".join(cmd))

        _LIVE.add(self)
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        # Spin up reader + writer threads
        self._reader_thread = threading.Thread(
            target=self._reader_loop, daemon=True, name="adb-reader"
        )
        self._writer_thread = threading.Thread(
            target=self._writer_loop, daemon=True, name="adb-writer"
        )
        self._reader_thread.start()
        self._writer_thread.start()

    # ── replay ────────────────────────────────────────────────────────

    def _start_replay(self) -> None:
        """Feed a recorded transcript into the output buffer.

        Lines are delivered one poll batch at a time so consumers see the same
        arrival-boundary behaviour as a real pipe. ``EDBG_GUI_REPLAY_DELAY``
        (seconds per line, default 0) paces it for manual inspection.
        """
        try:
            with open(self._replay_path, "r", encoding="utf-8") as fh:
                lines = [l.rstrip("\n\r") for l in fh]
        except OSError as exc:
            log.error("replay file unreadable: %s", exc)
            self._replay_done.set()
            return

        delay = float(os.environ.get("EDBG_GUI_REPLAY_DELAY", "0") or 0)

        def feed() -> None:
            for line in lines:
                with self._lock:
                    self._output_buf.append(line)
                if delay:
                    time.sleep(delay)
            self._replay_done.set()

        self._replay_done.clear()
        self._reader_thread = threading.Thread(
            target=feed, daemon=True, name="replay-feeder")
        self._reader_thread.start()

    @property
    def is_replay(self) -> bool:
        return self._replay_path is not None

    @property
    def replay_finished(self) -> bool:
        """True once the transcript is fully fed *and* drained."""
        if self._replay_path is None:
            return False
        with self._lock:
            pending = bool(self._output_buf)
        return self._replay_done.is_set() and not pending

    def stop(self, quit_timeout: float = 3.0) -> bool:
        """Tear down the device-side eDBG, then the local adb client.

        Terminating the adb client alone leaves eDBG running under adbd (see
        ``_EDBG_PID_RE``), so the device side is shut down first and verified.

        Order matters: ``quit`` reaches the REPL and runs Go's ``CleanUp()`` ->
        ``StopProbes()``, detaching probes cleanly. ``kill -9`` skips that and
        leaks them at kernel level, so it is only ever the fallback.

        Returns ``True`` when no eDBG remains on the device.
        """
        proc = self._proc
        if proc is None:
            _LIVE.discard(self)
            return True

        # 1. Ask the REPL to exit so Go detaches its probes. Written straight to
        #    stdin rather than via the write queue, which is being torn down.
        try:
            if proc.stdin is not None and proc.poll() is None:
                proc.stdin.write(b"quit\n")
                proc.stdin.flush()
        except OSError:
            pass

        # 2. Give it a bounded chance to exit on its own — but only when the
        #    ConfigMap banner proved a REPL actually came up. Without it there
        #    is nothing on the far end to consume `quit`, so waiting is dead
        #    time.
        if self._device_pid is not None:
            deadline = time.monotonic() + quit_timeout
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)

        # 3. Escalate on the device. The local client is irrelevant here.
        #    Only ever target our own pid — a blanket sweep would also kill
        #    eDBG instances the user started independently.
        device_pid = self._device_pid
        if device_pid is not None:
            _su(f"kill -TERM {device_pid}")
            time.sleep(0.3)
            if device_pid in device_edbg_pids():
                log.warning("eDBG %d ignored SIGTERM, forcing", device_pid)
                _su(f"kill -9 {device_pid}")
        else:
            # No ConfigMap banner seen — the session never got far enough to
            # spawn a device-side process worth chasing.
            log.debug("no device pid parsed; nothing to kill on device")

        # 4. Now the local side.
        self._write_q.put(_SENTINEL)
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

        if self._writer_thread is not None and self._writer_thread.is_alive():
            self._writer_thread.join(timeout=2)
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=2)

        self._proc = None
        self._device_pid = None
        _LIVE.discard(self)

        if device_pid is None:
            return True
        if device_pid in device_edbg_pids():
            log.error("eDBG %d survived teardown on device", device_pid)
            return False
        return True

    # ── REPL I/O ──────────────────────────────────────────────────────

    def send(self, line: str) -> None:
        """Enqueue a REPL command.  Thread-safe and non-blocking."""
        self._write_q.put(line)

    def poll_output(self) -> list[str]:
        """Drain all buffered output lines.  Non-blocking.

        Returns an empty list when there is nothing new.
        """
        lines: list[str] = []
        with self._lock:
            while self._output_buf:
                lines.append(self._output_buf.popleft())
        return lines

    def interrupt(self, kill_timeout: float = 2.0) -> None:
        """Send Ctrl+C to the subprocess.

        If the process is still alive after *kill_timeout* seconds **and**
        we know its device PID, attempt ``adb shell su -c 'kill <pid>'``.
        """
        proc = self._proc
        if proc is None:
            return

        # Send ETX (Ctrl+C)
        try:
            proc.stdin.write(b"\x03")
            proc.stdin.flush()
        except OSError:
            pass

        # Wait then escalate
        deadline = time.monotonic() + kill_timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.05)

        # Still alive — try killing via adb
        if self._device_pid is not None:
            log.warning("Ctrl+C ineffective, killing PID %d on device",
                        self._device_pid)
            subprocess.run(
                ["adb", "shell", "su", "-c",
                 f"kill -9 {self._device_pid}"],
                capture_output=True,
            )

    # ── properties ────────────────────────────────────────────────────

    @property
    def is_alive(self) -> bool:
        """``True`` while the underlying subprocess is running."""
        if self._replay_path is not None:
            # A finished transcript means "recording over", not "debugger
            # died" — reporting a disconnect here would overwrite the final
            # stop state and disable the run controls. Replay ends only via
            # stop(). Use `replay_finished` to detect the end of the feed.
            return True
        proc = self._proc
        if proc is None:
            return False
        return proc.poll() is None

    # ── internal threads ──────────────────────────────────────────────

    def _reader_loop(self) -> None:
        """Read lines from stdout in a background thread."""
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        while True:
            try:
                raw = proc.stdout.readline()
            except (OSError, ValueError):
                break
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\n\r")
            with self._lock:
                self._output_buf.append(line)

            if self._device_pid is None:
                self._device_pid = self._parse_edbg_pid(line)

    def _writer_loop(self) -> None:
        """Drain the write queue and push lines to stdin."""
        proc = self._proc
        assert proc is not None and proc.stdin is not None
        while True:
            try:
                item = self._write_q.get(timeout=0.1)
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue

            if item is _SENTINEL:
                break

            payload = (item + "\n").encode("utf-8")
            try:
                proc.stdin.write(payload)
                proc.stdin.flush()
            except OSError:
                break

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_edbg_pid(line: str) -> int | None:
        """Parse eDBG's own device PID from its ``ConfigMap{...}`` banner.

        Deliberately narrow: the hit banner ``pid=<n> tid=<n>`` carries the
        *debuggee's* pid, and killing that would terminate the target app.
        """
        m = _EDBG_PID_RE.search(line)
        return int(m.group(1)) if m else None
