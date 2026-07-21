"""A hit must publish all of its data, or none — never a partial capture.

The pid/tid line arrives before the register block, so committing a hit the
moment pid/tid is seen publishes whatever registers happened to be in that
poll batch (observed live: X0-X9 rendered, X10 through PC dropped). The
buffer was then cleared and the hit number unchanged, so the remainder could
never be recovered.

Go terminates the block with an unlabelled rule; these tests pin the rule as
the commit signal by replaying the same output at every possible split.
"""

from __future__ import annotations

from unittest import mock

from gui.session import EdbgSession, State

REG_NAMES = [f"X{i}" for i in range(30)] + ["LR", "SP", "PC"]

HIT_OUTPUT = (
    ["", "[Hit #1]", "pid=12345 tid=12350",
     "──────────────────[ REGISTERS ]──────────────────"]
    + [f" {n}\t0x{0x7BE3F81000 + i * 8:X}" for i, n in enumerate(REG_NAMES)]
    + ["──────────────────[  DISASM  ]──────────────────",
       ">>  0x7be3f81428<libloader.so+0x54a428>\tMOV X0, X1",
       "    0x7be3f8142c<libloader.so+0x54a42c>\tRET",
       "─────────────────────────────────────────────────"]
)


def _session_fed(batches: list[list[str]]) -> EdbgSession:
    """Drive a session by feeding poll_output() one batch per call."""
    s = EdbgSession()
    pty = mock.MagicMock()
    pty.is_alive = True
    pty.poll_output.side_effect = list(batches) + [[]] * 8
    s._pty = pty
    for _ in range(len(batches) + 8):
        s.poll_and_parse()
    return s


def test_all_registers_captured_when_delivered_at_once():
    s = _session_fed([HIT_OUTPUT])
    assert [r.name for r in s.last_regs] == REG_NAMES
    assert s.state is State.STOPPED


def test_no_partial_capture_at_any_split():
    """The live failure: split mid-register-block.

    Every split point must yield the identical, complete capture.
    """
    for cut in range(1, len(HIT_OUTPUT)):
        s = _session_fed([HIT_OUTPUT[:cut], HIT_OUTPUT[cut:]])
        names = [r.name for r in s.last_regs]
        assert names == REG_NAMES, (
            f"split after line {cut} ({HIT_OUTPUT[cut - 1]!r}) captured "
            f"{len(names)}/{len(REG_NAMES)} registers"
        )
        assert len(s.last_disasm) == 2, f"split after {cut} lost disassembly"


def test_line_at_a_time_delivery():
    """Worst case: one line per poll, as a slow adb pipe would deliver."""
    s = _session_fed([[line] for line in HIT_OUTPUT])
    assert [r.name for r in s.last_regs] == REG_NAMES
    assert len(s.last_disasm) == 2


def test_hit_not_published_before_block_closes():
    """State must not flip to STOPPED on a half-arrived block."""
    s = EdbgSession()
    pty = mock.MagicMock()
    pty.is_alive = True
    # everything except the closing rule
    pty.poll_output.side_effect = [HIT_OUTPUT[:-1], []]
    s._pty = pty
    s.poll_and_parse()
    assert s.state is not State.STOPPED
    assert s.last_hit is None
    assert s._pending_hit is not None


def test_second_hit_does_not_inherit_first(self=None):
    """Buffer must be cleared on commit so hit #2 is not polluted."""
    second = [l.replace("[Hit #1]", "[Hit #2]") for l in HIT_OUTPUT]
    s = _session_fed([HIT_OUTPUT, second])
    assert s.last_hit is not None
    assert s.last_hit.hit_number == 2
    assert [r.name for r in s.last_regs] == REG_NAMES


def test_commits_without_sections_after_grace():
    """With registers/disasm/display disabled Go emits no sections and no
    closing rule; the hit must still surface."""
    import gui.session as session_mod

    s = EdbgSession()
    pty = mock.MagicMock()
    pty.is_alive = True
    pty.poll_output.side_effect = [["", "[Hit #7]", "pid=1 tid=2"], [], []]
    s._pty = pty

    s.poll_and_parse()
    assert s.last_hit is None, "must not commit instantly"

    with mock.patch.object(session_mod.time, "monotonic",
                           return_value=s._pending_since + 10):
        s.poll_and_parse()
    assert s.last_hit is not None
    assert s.last_hit.hit_number == 7
