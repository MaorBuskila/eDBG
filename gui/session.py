"""Session state — wires AdbPty + parsers into a single API for the GUI."""

from __future__ import annotations

import enum
import os
import time
from dataclasses import dataclass, field

from gui.adb_pty import AdbPty
from gui import adb_pty
from gui import events
from gui import parse
from gui import protocol


# How long to wait for a section header before concluding none is coming.
# Only reached when registers, disassembly and display are all disabled, so a
# hit still surfaces promptly in that configuration.
_NO_SECTION_GRACE = 0.25


class State(enum.Enum):
    DISCONNECTED = "disconnected"
    STARTING = "starting"
    ATTACHED = "attached"      # process running, no hit yet
    RUNNING = "running"        # continue issued, waiting for hit
    STOPPED = "stopped"        # breakpoint hit, can inspect
    ERROR = "error"


@dataclass
class SessionConfig:
    package: str = ""
    lib: str = ""
    breaks: list[str] = field(default_factory=list)


class EdbgSession:
    """Single source of truth for a debug session."""

    def __init__(self) -> None:
        self._pty: AdbPty | None = None
        self.state: State = State.DISCONNECTED
        self.config = SessionConfig()

        # Parsed state from last output
        self.last_hit: parse.HitInfo | None = None
        self.last_regs: list[parse.RegisterInfo] = []
        self.last_disasm: list[parse.DisasmLine] = []
        self.last_backtrace: list[parse.BTFrame] = []
        self.last_memory: list[tuple[int, bytes]] = []
        self.last_breakpoints: list[dict] = []
        self.last_threads: list[dict] = []
        self.last_tls: parse.TlsDump | None = None

        # Raw transcript (all output lines)
        self.transcript: list[str] = []
        # New lines since last poll (for incremental UI update)
        self._new_lines: list[str] = []
        # Accumulator for section detection
        self._section_buf: list[str] = []
        # Auto-incrementing hit counter (fallback when Go omits [Hit #N])
        self._auto_hit: int = 0
        # Hit seen but held back until its section block closes
        self._pending_hit: parse.HitInfo | None = None
        self._pending_since: float = 0.0

        # Set once any structured event arrives; disables the text scraper.
        self._structured: bool = False
        self.schema: int = 0
        self.flow_progress: events.FlowProgress | None = None
        self.flow_done: events.FlowDone | None = None

    # ── lifecycle ─────────────────────────────────────────────────────

    def start(self, package: str, lib: str, breaks: list[str]) -> None:
        """Push binary (if local path known) and start eDBG on device."""
        self.config = SessionConfig(package=package, lib=lib, breaks=breaks)
        self.state = State.STARTING

        replaying = bool(os.environ.get("EDBG_GUI_REPLAY"))

        if not replaying:
            # A crashed session leaves eDBG running under adbd with its
            # hardware debug registers still armed; it would compete with this
            # one for the target's limited breakpoint slots.
            stale = adb_pty.sweep_stale_device_processes()
            if stale:
                msg = f"[GUI] killed {len(stale)} stale device-side eDBG: {stale}"
                self.transcript.append(msg)
                self._new_lines.append(msg)

        local_bin = os.environ.get("EDBG_GUI_BIN")
        self._pty = AdbPty(local_bin_path=local_bin)

        # Push if we have a local binary
        if local_bin and not replaying:
            self._pty.push_binary()

        try:
            self._pty.start(package, lib, breaks)
            self.state = State.ATTACHED
        except Exception as exc:
            self.state = State.ERROR
            self.transcript.append(f"[GUI] start failed: {exc}")
            self._new_lines.append(self.transcript[-1])

    def stop(self) -> None:
        """Tear down the device session, including the device-side eDBG."""
        if self._pty is not None:
            if not self._pty.stop():
                msg = "[GUI] WARNING: eDBG still running on device after teardown"
                self.transcript.append(msg)
                self._new_lines.append(msg)
            self._pty = None
        self.state = State.DISCONNECTED
        self._clear_parsed()

    def send_command(self, line: str) -> None:
        """Send an arbitrary REPL command."""
        if self._pty is None:
            return
        self._pty.send(line)
        self.transcript.append(f"(eDBG) {line}")
        self._new_lines.append(self.transcript[-1])

    def continue_(self) -> None:
        """Send 'c' and mark running."""
        self.send_command("c")
        self.state = State.RUNNING

    def interrupt(self) -> None:
        """Send Ctrl+C / kill."""
        if self._pty is not None:
            self._pty.interrupt()

    # ── polling (called every frame) ──────────────────────────────────

    def poll_and_parse(self) -> list[str]:
        """Drain PTY output, run parsers, update state.

        Returns new raw lines since last call (for log pane append).
        """
        if self._pty is None:
            return []

        raw_lines = self._pty.poll_output()
        if not raw_lines:
            # Check if process died
            if not self._pty.is_alive and self.state not in (
                State.DISCONNECTED, State.ERROR
            ):
                self.state = State.DISCONNECTED
                msg = "[GUI] eDBG process exited."
                self.transcript.append(msg)
                self._new_lines.append(msg)
            # Fall through: a pending hit may still be waiting on the grace
            # timer, and no further output is coming to trigger it.
            self._try_commit_pending()
            result = list(self._new_lines)
            self._new_lines.clear()
            return result

        # Structured events are interleaved with ordinary output. Split them
        # out: raw JSON frames must not reach the log pane, and once any event
        # has arrived the text scraper is redundant (and would double-apply,
        # since -json still emits the human output alongside).
        text_lines: list[str] = []
        for line in raw_lines:
            if protocol.is_event(line):
                self._structured = True
                self._apply_event(protocol.decode_line(line), text_lines)
            else:
                text_lines.append(line)

        self.transcript.extend(text_lines)
        self._new_lines.extend(text_lines)

        if self._structured:
            result = list(self._new_lines)
            self._new_lines.clear()
            return result

        self._section_buf.extend(text_lines)

        # Detect the hit, but do not commit it until its section block is
        # closed — see _commit_hit. The pid/tid line arrives before the
        # registers, so committing here would publish a partial capture.
        if self._pending_hit is None:
            hit = parse.parse_hit(self._section_buf)

            # Fallback: if no explicit [Hit #N] but we have pid/tid + section
            # data, synthesise a hit so the GUI still updates.
            if hit is None:
                sections = parse.split_sections("\n".join(self._section_buf))
                pid_tid = parse.parse_pid_tid(self._section_buf)
                if pid_tid is not None and ("REGISTERS" in sections or "DISASM" in sections):
                    self._auto_hit += 1
                    hit = parse.HitInfo(
                        hit_number=self._auto_hit,
                        pid=pid_tid[0],
                        tid=pid_tid[1],
                    )

            if hit is not None:
                self._pending_hit = hit
                self._pending_since = time.monotonic()

        self._try_commit_pending()

        # Cap section buffer to prevent unbounded growth
        if len(self._section_buf) > 2000:
            self._section_buf = self._section_buf[-1000:]

        # Check for ConfigMap line (attach confirmation)
        for line in raw_lines:
            clean = parse.strip_ansi(line)
            if clean.startswith("ConfigMap{") and self.state == State.STARTING:
                self.state = State.ATTACHED

        result = list(self._new_lines)
        self._new_lines.clear()
        return result

    def _apply_event(self, ev, text_lines: list[str]) -> None:
        """Fold one structured event into session state.

        A ``stop`` carries every pane's data in a single object, so it is
        atomic by construction — the partial-capture race the text path has to
        guard against cannot occur here.
        """
        if isinstance(ev, events.Stop):
            self.last_hit = parse.HitInfo(hit_number=ev.hit, pid=ev.pid,
                                          tid=ev.tid)
            self.last_regs = ev.regs
            self.last_disasm = ev.disasm
            self.last_backtrace = ev.backtrace
            self.last_threads = ev.threads
            self.last_memory = ev.memory
            if ev.breakpoints:
                self.last_breakpoints = ev.breakpoints
            self.state = State.STOPPED

        elif isinstance(ev, events.BreakpointsEvent):
            self.last_breakpoints = ev.breakpoints

        elif isinstance(ev, events.ThreadsEvent):
            self.last_threads = ev.threads

        elif isinstance(ev, events.MemoryEvent):
            self.last_memory.append((ev.addr, ev.data))

        elif isinstance(ev, events.FlowProgress):
            self.flow_progress = ev
            self.state = State.RUNNING

        elif isinstance(ev, events.FlowDone):
            self.flow_done = ev
            text_lines.append(
                f"[eDBG] flow finished: {ev.steps} steps -> {ev.csv_path}")

        elif isinstance(ev, events.ErrorEvent):
            text_lines.append(f"[eDBG] error: {ev.msg}")

        elif isinstance(ev, events.Hello):
            self.schema = ev.schema
            text_lines.append(
                f"[eDBG] {ev.pkg}/{ev.lib} pid={ev.edbg_pid} schema={ev.schema}")

        elif isinstance(ev, events.Malformed):
            # Surface rather than swallow: a corrupt frame means the transport
            # or the schema is wrong, and silence would hide it.
            text_lines.append(f"[GUI] malformed event: {ev.error}")

        elif isinstance(ev, events.UnknownEvent):
            # Forward compatibility: a newer eDBG must not break this GUI.
            text_lines.append(f"[GUI] ignoring unknown event {ev.type!r}")

    def _try_commit_pending(self) -> None:
        """Publish a held-back hit once its section block is closed."""
        if self._pending_hit is None:
            return
        complete = parse.sections_complete(self._section_buf)
        if complete is None:
            # No section header yet. Either one is still in flight, or the run
            # disabled registers/disasm/display, in which case none is coming.
            complete = time.monotonic() - self._pending_since > _NO_SECTION_GRACE
        if complete:
            self._commit_hit(self._pending_hit)
            self._pending_hit = None

    def _commit_hit(self, hit: parse.HitInfo) -> None:
        """Publish a hit once its whole section block has arrived."""
        self.last_hit = hit
        self.state = State.STOPPED

        # Clear per-hit data so auto-fetch starts fresh
        self.last_threads = []

        sections = parse.split_sections("\n".join(self._section_buf))

        if "REGISTERS" in sections:
            self.last_regs = parse.parse_registers(sections["REGISTERS"])
        if "DISASM" in sections:
            self.last_disasm = parse.parse_disasm(sections["DISASM"])
        if "DISPLAY" in sections:
            self.last_memory = parse.parse_memory(sections["DISPLAY"])

        # Backtrace not in section markers — parse from full buf
        self.last_backtrace = parse.parse_backtrace(self._section_buf)

        self._section_buf.clear()

    def pull_file(self, device_path: str, local_path: str) -> bool:
        """Pull a file from device (for flow CSV, dumps, etc.)."""
        if self._pty is None:
            return False
        return self._pty.pull_file(device_path, local_path)

    @property
    def is_connected(self) -> bool:
        return self.state not in (State.DISCONNECTED, State.ERROR)

    # ── internal ──────────────────────────────────────────────────────

    def _clear_parsed(self) -> None:
        self.last_hit = None
        self.last_regs = []
        self.last_disasm = []
        self.last_backtrace = []
        self.last_memory = []
        self.last_breakpoints = []
        self.last_threads = []
        self._section_buf.clear()
        self._pending_hit = None
