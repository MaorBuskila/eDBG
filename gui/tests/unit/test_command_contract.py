"""Every command the GUI emits must be one the Go REPL actually accepts.

Three shipped panes (Memory, Watch, Flow) were dead because the GUI emitted
strings `cli/repl.go` rejects — `x/64x`, swapped `display` args, single-dash
flow flags. Nothing caught it: the resulting `Unknown command` went to a log
nobody diffs.

Two layers here:

* ``test_*_matches_go_dispatch`` — static, no device. Parses the command names
  out of ``cli/repl.go``'s switch and asserts every verb the GUI emits exists.
  Runs in CI.
* ``test_commands_accepted_by_live_repl`` — opt-in, needs a rooted device.
  Feeds each command to a real ``-pipe`` session and asserts eDBG answers
  none of them with a rejection. Enable with ``EDBG_CONTRACT_DEVICE=1``.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]
REPL_GO = REPO / "cli" / "repl.go"

# Every command string the GUI can emit, with placeholders filled in.
# Keep in sync with gui/app.py — a new emitter without an entry here is a bug.
GUI_COMMANDS = [
    "c",
    "s",
    "n",
    "fi",
    "u 0x1000",
    "bt",
    "info thread",
    "info b",                             # was `info breakpoints` — rejected
    "x 0x7b80e12340 64",                  # was `x/64x 0x…` — rejected by Go
    "w 0x7b80e12340 41424344",
    "b 0x1234",
    "vb 0x1234",
    "hb 0x1234",
    "watch 0x1234",
    "rwatch 0x1234",
    "enable 0",
    "disable 0",
    "delete 0",
    "display 0x7b80e12340 64 myvar",      # was `<addr> <name> <len>` — swapped
    "thread 12345",
    "flow 0x1234 --over --max 500 --regs --mem X0 --tls 32 --quiet",  # was single-dash
    "tls",                                # HandleTls: no args = dump from SP
]

# Rejections eDBG prints when it does not understand a command.
REJECTION_RE = re.compile(r"Unknown command:|^Usage:|Invalid type or length",
                          re.MULTILINE)


def _go_dispatch_verbs() -> set[str]:
    """Command names from the `switch cmd` in Client.executeCommand."""
    src = REPL_GO.read_text(encoding="utf-8")
    start = src.index("func (this *Client) executeCommand(")
    end = src.index("\nfunc ", start + 1)
    body = src[start:end]
    verbs: set[str] = set()
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("case "):
            continue
        verbs.update(re.findall(r'"([^"]+)"', line))
    return verbs


def test_go_dispatch_parsed():
    """Guard the parser itself — a rename upstream must not silently pass."""
    verbs = _go_dispatch_verbs()
    assert {"break", "continue", "examine", "x", "display", "flow"} <= verbs, (
        f"executeCommand switch not parsed as expected; got {sorted(verbs)}"
    )


@pytest.mark.parametrize("cmd", GUI_COMMANDS)
def test_gui_command_verb_matches_go_dispatch(cmd):
    """The first token must be a real case in Go's switch.

    `strings.Fields(line)[0]` is what Go dispatches on, so `x/64x 0x…` yields
    the verb `x/64x`, which matches nothing.
    """
    verb = cmd.split()[0]
    assert verb in _go_dispatch_verbs(), (
        f"GUI emits {cmd!r}; verb {verb!r} is not a case in executeCommand"
    )


def test_flow_flags_are_double_dash():
    """Go's flow parser matches `--over` etc. exactly; `-over` is rejected."""
    flow = next(c for c in GUI_COMMANDS if c.startswith("flow "))
    for tok in flow.split()[2:]:
        if tok.startswith("-"):
            assert tok.startswith("--"), f"flow flag {tok!r} must be double-dash"


def _go_flow_flags() -> set[str]:
    """Flag names from the `switch args[i]` in Client.HandleFlow."""
    src = REPL_GO.read_text(encoding="utf-8")
    start = src.index("func (this *Client) HandleFlow(")
    end = src.index("\nfunc ", start + 1)
    return set(re.findall(r'case "(--[a-z]+)"', src[start:end]))


def test_every_flow_flag_the_gui_emits_is_a_case_in_go():
    flow = next(c for c in GUI_COMMANDS if c.startswith("flow "))
    emitted = {t for t in flow.split()[2:] if t.startswith("-")}
    assert emitted <= _go_flow_flags(), (
        f"GUI emits flow flags Go rejects: {sorted(emitted - _go_flow_flags())}")


def test_the_tls_slot_count_is_space_separated_like_every_other_flow_value():
    # Go's parser reads the value as the next argv entry; `--tls=32` would reach
    # it as one unknown flag and abort the run.
    flow = next(c for c in GUI_COMMANDS if c.startswith("flow "))
    assert "--tls 32" in flow
    assert "--tls=" not in flow


def _go_info_subcommands() -> set[str]:
    """Subcommand names from the `switch args[0]` in Client.HandleInfo."""
    src = REPL_GO.read_text(encoding="utf-8")
    start = src.index("func (this *Client) HandleInfo(")
    end = src.index("\nfunc ", start + 1)
    subs: set[str] = set()
    for line in src[start:end].splitlines():
        line = line.strip()
        if line.startswith("case "):
            subs.update(re.findall(r'"([^"]+)"', line))
    return subs


@pytest.mark.parametrize(
    "cmd", [c for c in GUI_COMMANDS if c.startswith("info ")])
def test_info_subcommand_matches_go(cmd):
    """`info` dispatches on args[0]; `breakpoints` is not a case — only
    `break`/`b`. The plural silently fell through to the usage text."""
    sub = cmd.split()[1]
    subs = _go_info_subcommands()
    assert subs, "HandleInfo switch not parsed"
    assert sub in subs, (
        f"GUI emits {cmd!r}; {sub!r} is not a case in HandleInfo {sorted(subs)}"
    )


def test_display_arg_order_matches_go():
    """Go signature is `display <address> <len> <name>`."""
    src = REPL_GO.read_text(encoding="utf-8")
    assert "display <address> <len> <name>" in src, (
        "Go's display usage string changed — re-check the GUI's argument order"
    )
    disp = next(c for c in GUI_COMMANDS if c.startswith("display "))
    _, addr, length, name = disp.split()
    assert addr.startswith("0x") and length.isdigit() and not name.isdigit()


@pytest.mark.skipif(
    os.environ.get("EDBG_CONTRACT_DEVICE") != "1",
    reason="needs a rooted device; set EDBG_CONTRACT_DEVICE=1",
)
def test_commands_accepted_by_live_repl():
    """Feed every command to a real eDBG and assert none is rejected.

    Requires EDBG_CONTRACT_PKG / EDBG_CONTRACT_LIB. Commands that need a live
    stop will report their own errors; we only assert eDBG *understood* them.
    """
    import subprocess
    import threading
    import time
    from collections import deque

    pkg = os.environ.get("EDBG_CONTRACT_PKG")
    lib = os.environ.get("EDBG_CONTRACT_LIB")
    assert pkg and lib, "set EDBG_CONTRACT_PKG and EDBG_CONTRACT_LIB"

    from gui import adb_pty

    adb_pty.sweep_stale_device_processes()
    proc = subprocess.Popen(
        ["adb", "shell", "su", "-c",
         f"/data/local/tmp/eDBG -pipe -p {pkg} -l {lib}"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    out: deque[str] = deque()

    def reader():
        for raw in iter(proc.stdout.readline, b""):
            out.append(raw.decode("utf-8", errors="replace"))

    threading.Thread(target=reader, daemon=True).start()
    try:
        time.sleep(3)
        for cmd in GUI_COMMANDS:
            proc.stdin.write((cmd + "\n").encode())
        proc.stdin.flush()
        time.sleep(5)
        text = "".join(out)
        rejected = REJECTION_RE.findall(text)
        assert not rejected, f"eDBG rejected GUI commands: {rejected}\n{text}"
    finally:
        try:
            proc.stdin.write(b"quit\n")
            proc.stdin.flush()
            time.sleep(1)
        except OSError:
            pass
        proc.kill()
        adb_pty.sweep_stale_device_processes()
