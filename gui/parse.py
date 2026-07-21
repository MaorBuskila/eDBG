"""
Parser for eDBG ARM64 Android debugger REPL output.

Converts the coloured, section-delimited text produced by the Go binary
(cli/repl.go, controller/context.go, module/breakpointmanager.go, etc.)
into structured Python dataclasses suitable for the GUI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ── ANSI handling ───────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove all ANSI SGR escape sequences from *text*."""
    return _ANSI_RE.sub("", text)


# ── Dataclasses ─────────────────────────────────────────────────────────────

@dataclass
class HitInfo:
    hit_number: int
    pid: int
    tid: int


@dataclass
class RegisterInfo:
    name: str          # "X0", "LR", "SP", "PC"
    value: int
    deref: str | None  # dereferenced info string or None
    symbol: str        # "<libfoo.so+0x1234>" or ""


@dataclass
class DisasmLine:
    address: int
    symbol: str        # e.g. "libfoo.so+0x1234" or ""
    mnemonic: str
    operands: str
    is_current: bool   # True if ">>" prefix


@dataclass
class BTFrame:
    index: int
    address: int
    symbol: str        # "libfoo.so + 0x1234" or "?? ()"


@dataclass
class TlsSlot:
    address: int
    value: int
    cls: str           # string|code|stack|heap|mapped|junk (utils.TlsClass)
    offset: int        # slot address minus dump base
    annotation: str    # symbol, "-> ..." peek, or LE ASCII; "" when absent


@dataclass
class TlsDump:
    tid: int
    map_name: str
    map_start: int
    map_end: int
    base: int
    length: int
    slots: list[TlsSlot]


# ── Section splitter ────────────────────────────────────────────────────────

# Matches lines like:
#   ──────[ REGISTERS ]──────
# Captures the section name (stripped).
_SECTION_RE = re.compile(r"^─+\[\s*(\S.*?\S|\S)\s*\]─+$")

# The closing separator has no label.
_CLOSE_RE = re.compile(r"^─+$")


def split_sections(text: str) -> dict[str, list[str]]:
    """Split full REPL output into named sections by ``[ SECTION ]`` markers.

    Returns a dict mapping upper-cased section names (e.g. ``"REGISTERS"``,
    ``"DISASM"``, ``"DISPLAY"``) to the list of content lines between that
    header and the next section header (or closing rule).

    Any lines that appear *before* the first section header are stored under
    the key ``"HEADER"`` (this typically contains the hit line and pid/tid).
    """
    clean = strip_ansi(text)
    lines = clean.splitlines()

    sections: dict[str, list[str]] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    for line in lines:
        sec_match = _SECTION_RE.match(line)
        if sec_match:
            # Flush the previous section
            key = current_name if current_name is not None else "HEADER"
            sections.setdefault(key, []).extend(current_lines)
            current_name = sec_match.group(1).strip().upper()
            current_lines = []
            continue
        if _CLOSE_RE.match(line):
            # Closing separator -- flush current section
            if current_name is not None:
                sections.setdefault(current_name, []).extend(current_lines)
                current_name = None
                current_lines = []
            continue
        current_lines.append(line)

    # Flush any remaining lines
    key = current_name if current_name is not None else "HEADER"
    if current_lines:
        sections.setdefault(key, []).extend(current_lines)

    return sections


# ── Hit parsing ─────────────────────────────────────────────────────────────

_HIT_RE = re.compile(r"\[Hit\s+#(\d+)\]")
_PID_TID_RE = re.compile(r"pid=(\d+)\s+tid=(\d+)")


def sections_complete(lines: list[str]) -> bool | None:
    """Has a whole section block arrived, or is it still streaming in?

    ``OutputInfo`` emits ``[ SECTION ]`` headers and terminates the run with a
    single unlabelled rule. Reading the buffer before that rule lands yields a
    half-parsed hit — e.g. registers X0-X9 with X10 onward still in the pipe.

    Returns ``True`` when the closing rule has arrived, ``False`` while a
    header has been seen but its rule has not, and ``None`` when no section
    header is present at all (the caller must decide how long to keep waiting;
    with registers, disassembly and display all disabled, Go emits no sections
    and therefore no rule).
    """
    clean = [strip_ansi(l) for l in lines]
    header_idx = [i for i, l in enumerate(clean) if _SECTION_RE.match(l)]
    if not header_idx:
        return None
    return any(_CLOSE_RE.match(l) for l in clean[header_idx[-1] + 1:])


def parse_hit(lines: list[str]) -> HitInfo | None:
    """Parse the hit header from a list of (possibly ANSI-coloured) lines.

    Expects lines like::

        [Hit #3]
        pid=12345 tid=67890

    Returns ``None`` if no hit header is found.
    """
    hit_number: int | None = None
    pid: int | None = None
    tid: int | None = None

    for raw_line in lines:
        line = strip_ansi(raw_line)
        m = _HIT_RE.search(line)
        if m:
            hit_number = int(m.group(1))
        m = _PID_TID_RE.search(line)
        if m:
            pid = int(m.group(1))
            tid = int(m.group(2))

    if hit_number is not None and pid is not None and tid is not None:
        return HitInfo(hit_number=hit_number, pid=pid, tid=tid)
    return None


def parse_pid_tid(lines: list[str]) -> tuple[int, int] | None:
    """Extract (pid, tid) from lines, ignoring [Hit #N].

    Used as a fallback when the Go binary omits the hit marker.
    """
    for raw_line in lines:
        line = strip_ansi(raw_line)
        m = _PID_TID_RE.search(line)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


# ── Register parsing ────────────────────────────────────────────────────────

# Matches register lines from PrintContext().  Examples (after ANSI strip):
#   *X0	0x7B80E12340<libfoo.so+0x1234> ◂— 0x00000001
#    X1	0x0
#   *LR	0x7B80E15678<libfoo.so+0x5678>
#   *SP	0x7FFEABC000
_REG_RE = re.compile(
    r"^[* ]"                         # '*' for deref-able pointer, ' ' otherwise
    r"(X\d+|LR|SP|PC)\t"            # register name + tab
    r"(0x[0-9A-Fa-f]+)"             # hex value
    r"(?:<([^>]+)>)?"               # optional <symbol>
    r"(?:\s+◂—\s+(.+))?"           # optional deref
    r"$"
)


def parse_registers(lines: list[str]) -> list[RegisterInfo]:
    """Parse register display lines into ``RegisterInfo`` objects."""
    result: list[RegisterInfo] = []
    for raw_line in lines:
        line = strip_ansi(raw_line)
        m = _REG_RE.match(line)
        if not m:
            continue
        name = m.group(1)
        value = int(m.group(2), 16)
        sym_raw = m.group(3)
        deref_raw = m.group(4)
        symbol = f"<{sym_raw}>" if sym_raw else ""
        deref = deref_raw.strip() if deref_raw else None
        result.append(RegisterInfo(name=name, value=value, deref=deref, symbol=symbol))
    return result


# ── Disassembly parsing ────────────────────────────────────────────────────

# Matches lines from PrintDisassembleInfo().  Examples (after ANSI strip):
#   >>  0x7b80e12340<libfoo.so+0x1234>\tMOV X0, X1
#       0x7b80e12344<libfoo.so+0x1238>\tBL #0x7b80e20000
#       0x7b80e1234c\tNOP
_DISASM_RE = re.compile(
    r"^(>>|  )\s+"                   # ">>" or spaces
    r"(0x[0-9A-Fa-f]+)"             # address
    r"(?:<([^>]+)>)?"               # optional <lib+offset>
    r"\t"                            # tab before instruction
    r"(\S+)"                         # mnemonic
    r"(?:\s+(.*))?"                  # optional operands
    r"$"
)


def parse_disasm(lines: list[str]) -> list[DisasmLine]:
    """Parse disassembly lines into ``DisasmLine`` objects."""
    result: list[DisasmLine] = []
    for raw_line in lines:
        line = strip_ansi(raw_line)
        m = _DISASM_RE.match(line)
        if not m:
            continue
        is_current = m.group(1) == ">>"
        address = int(m.group(2), 16)
        symbol = m.group(3) or ""
        mnemonic = m.group(4)
        operands = (m.group(5) or "").strip()
        result.append(DisasmLine(
            address=address,
            symbol=symbol,
            mnemonic=mnemonic,
            operands=operands,
            is_current=is_current,
        ))
    return result


# ── Backtrace parsing ──────────────────────────────────────────────────────

# Matches lines like:
#   #0  0x00000079ABCDEF00 in libfoo.so + 0x1234
#   #1  0x00000079ABCDEF04 in ?? ()
_BT_RE = re.compile(
    r"^#(\d+)\s+"                    # frame index
    r"(0x[0-9A-Fa-f]+)\s+"          # address
    r"in\s+"                         # "in"
    r"(.+)$"                         # symbol description
)


def parse_backtrace(lines: list[str]) -> list[BTFrame]:
    """Parse backtrace output into ``BTFrame`` objects."""
    result: list[BTFrame] = []
    for raw_line in lines:
        line = strip_ansi(raw_line)
        m = _BT_RE.match(line)
        if not m:
            continue
        index = int(m.group(1))
        address = int(m.group(2), 16)
        symbol = m.group(3).strip()
        result.append(BTFrame(index=index, address=address, symbol=symbol))
    return result


# ── Memory (hex dump) parsing ──────────────────────────────────────────────

# HexDump format (from utils.HexDump):
#   0000abcd  48656c6c6f20576f  726c642100000000  |Hello World!....|
# The hex part is two groups of 8 bytes (16 hex chars each) separated by
# two spaces, left-justified in a 47-char field.
_HEXDUMP_RE = re.compile(
    r"^([0-9A-Fa-f]{8})\s{2}"       # 8-char hex address + 2 spaces
    r"([0-9A-Fa-f ]+?)\s{2}"        # hex bytes region
    r"\|.*\|"                        # ASCII region
)


def parse_memory(lines: list[str]) -> list[tuple[int, bytes]]:
    """Parse hex-dump lines into ``(address, raw_bytes)`` tuples.

    Each tuple contains the line start address and up to 16 raw bytes.
    """
    result: list[tuple[int, bytes]] = []
    for raw_line in lines:
        line = strip_ansi(raw_line)
        m = _HEXDUMP_RE.match(line)
        if not m:
            continue
        address = int(m.group(1), 16)
        hex_part = m.group(2).replace(" ", "")
        raw = bytes.fromhex(hex_part)
        result.append((address, raw))
    return result


# ── Breakpoint parsing ──────────────────────────────────────────────────────

# PrintBreakPoints() output lines:
#   [+] 0: libfoo.so+1234           (software breakpoint)
#   [-] 2: libfoo.so+abcd           (disabled)
#   [+] 3: deadbeef Hardware        (hardware breakpoint)
_BRK_RE = re.compile(
    r"^\[([+-])\]\s+"               # enabled/disabled marker
    r"(\d+):\s+"                     # id
    r"(.+)$"                         # rest (address info)
)

_BRK_HW_RE = re.compile(
    r"^([0-9A-Fa-f]+)\s+Hardware$"
)

_BRK_SW_RE = re.compile(
    r"^(.+?)\+([0-9A-Fa-f]+)$"
)


def parse_breakpoints(lines: list[str]) -> list[dict]:
    """Parse breakpoint list output into dicts.

    Each dict has keys:
        - ``id`` (int): breakpoint index
        - ``enabled`` (bool): True if ``[+]``
        - ``type`` (str): ``"hardware"`` or ``"software"``
        - ``library`` (str): library name (software) or ``""``
        - ``offset`` (int): offset within library (software) or absolute addr (hardware)
        - ``address`` (int): absolute hex address for hardware breakpoints, 0 otherwise
    """
    result: list[dict] = []
    for raw_line in lines:
        line = strip_ansi(raw_line)
        m = _BRK_RE.match(line)
        if not m:
            continue
        enabled = m.group(1) == "+"
        bp_id = int(m.group(2))
        rest = m.group(3).strip()

        hw = _BRK_HW_RE.match(rest)
        if hw:
            result.append({
                "id": bp_id,
                "enabled": enabled,
                "type": "hardware",
                "library": "",
                "offset": int(hw.group(1), 16),
                "address": int(hw.group(1), 16),
            })
            continue

        sw = _BRK_SW_RE.match(rest)
        if sw:
            result.append({
                "id": bp_id,
                "enabled": enabled,
                "type": "software",
                "library": sw.group(1),
                "offset": int(sw.group(2), 16),
                "address": 0,
            })
            continue

    return result


# ── Thread parsing ──────────────────────────────────────────────────────────

# PrintThreads() output lines:
#   >>[0] 12345: main            (current thread)
#     [1] 12346: WorkerThread
_THREAD_RE = re.compile(
    r"^(>>|  )"                      # current marker
    r"\[(\d+)\]\s+"                  # index
    r"(\d+):\s+"                     # tid
    r"(.+)$"                         # thread name
)


def parse_threads(lines: list[str]) -> list[dict]:
    """Parse thread list output into dicts.

    Each dict has keys:
        - ``index`` (int): thread list index
        - ``tid`` (int): thread ID
        - ``name`` (str): thread name
        - ``is_current`` (bool): True if marked with ``>>``
    """
    result: list[dict] = []
    for raw_line in lines:
        line = strip_ansi(raw_line)
        m = _THREAD_RE.match(line)
        if not m:
            continue
        is_current = m.group(1) == ">>"
        index = int(m.group(2))
        tid = int(m.group(3))
        name = m.group(4).strip()
        result.append({
            "index": index,
            "tid": tid,
            "name": name,
            "is_current": is_current,
        })
    return result


# ── ANSI → RGB color mapping ─────────────────────────────────────────────────

# Maps ANSI SGR foreground codes to DPG-friendly RGB tuples.
# Palette mirrors the Go-side config.go constants → gui/theme.py accents.
ANSI_COLORS: dict[str, tuple[int, int, int]] = {
    "31": (255, 92, 92),      # RED    – pointer register names
    "32": (78, 201, 176),     # GREEN  – current-instruction marker
    "33": (229, 192, 123),    # YELLOW – [Hit], mnemonics
    "34": (86, 212, 255),     # BLUE   – section headers
    "36": (86, 212, 255),     # CYAN   – values, operands, pid/tid
}
_DEFAULT_TEXT_COLOR: tuple[int, int, int] = (198, 208, 224)

_ANSI_SEQ_RE = re.compile(r"\x1b\[([0-9;]*)m")


@dataclass
class ColoredSegment:
    """A chunk of text with an associated RGB color."""
    text: str
    color: tuple[int, int, int]


def parse_ansi_line(text: str) -> list[ColoredSegment]:
    """Split *text* on ANSI SGR escapes and return colored segments.

    Non-ANSI text (or text after a reset) gets ``_DEFAULT_TEXT_COLOR``.
    """
    segments: list[ColoredSegment] = []
    color = _DEFAULT_TEXT_COLOR
    pos = 0
    for m in _ANSI_SEQ_RE.finditer(text):
        if m.start() > pos:
            chunk = text[pos:m.start()]
            if chunk:
                segments.append(ColoredSegment(text=chunk, color=color))
        for code in m.group(1).split(";"):
            c = code.strip()
            if c == "" or c == "0":
                color = _DEFAULT_TEXT_COLOR
            elif c in ANSI_COLORS:
                color = ANSI_COLORS[c]
        pos = m.end()
    if pos < len(text):
        tail = text[pos:]
        if tail:
            segments.append(ColoredSegment(text=tail, color=color))
    return segments or [ColoredSegment(text=text, color=_DEFAULT_TEXT_COLOR)]


# ── TLS dump parsing ────────────────────────────────────────────────────────

# Client.HandleTls (cli/repl.go:602) output:
#   tls tid=12350 map=[anon:stack_and_tls:12350] 0x7b80e00000-0x7b80e10000
#   base=0x7b80e0ff00  len=0x30
#   0x7b80e0ff00  0x7b80e12340  code  +0x0 (#0)  libloader.so+0x1234
#   0x7b80e0ff18  0x0  junk  +0x18 (#24)
# The trailing annotation is emitted only for classes that produce one, so a
# row without it is well-formed, not malformed.
_TLS_HDR_RE = re.compile(
    r"^tls\s+tid=(\d+)\s+map=(\S+)\s+"
    r"0x([0-9A-Fa-f]+)-0x([0-9A-Fa-f]+)\s*$"
)

_TLS_BASE_RE = re.compile(
    r"^base=0x([0-9A-Fa-f]+)\s+len=0x([0-9A-Fa-f]+)\s*$"
)

_TLS_SLOT_RE = re.compile(
    r"^0x([0-9A-Fa-f]+)\s+"                             # slot address
    r"0x([0-9A-Fa-f]+)\s+"                              # slot value
    r"(string|code|stack|heap|mapped|junk)\s+"          # utils.TlsClass
    r"\+0x[0-9A-Fa-f]+\s+\(#(\d+)\)"                    # rel offset
    r"(?:\s+(.*\S))?\s*$"                               # optional annotation
)


def is_tls_line(line: str) -> bool:
    """True for any line that could belong to a ``tls`` dump.

    Lets a caller re-parse a rolling window only when TLS output actually
    arrived, instead of on every frame.
    """
    s = strip_ansi(line).strip()
    return bool(_TLS_HDR_RE.match(s) or _TLS_BASE_RE.match(s)
                or _TLS_SLOT_RE.match(s))


def parse_tls(lines: list[str]) -> TlsDump | None:
    """Parse a ``tls`` command response.

    Returns None when `lines` holds no TLS header — error replies
    ("Not stopped on a thread.", "Usage: ...") are not dumps. A truncated dump
    yields the slots that did arrive.
    """
    clean = [strip_ansi(l) for l in lines]

    dump: TlsDump | None = None
    for line in clean:
        m = _TLS_HDR_RE.match(line.strip())
        if m:
            dump = TlsDump(tid=int(m.group(1)), map_name=m.group(2),
                           map_start=int(m.group(3), 16),
                           map_end=int(m.group(4), 16),
                           base=0, length=0, slots=[])
            continue
        if dump is None:
            continue
        m = _TLS_BASE_RE.match(line.strip())
        if m:
            dump.base = int(m.group(1), 16)
            dump.length = int(m.group(2), 16)
            continue
        m = _TLS_SLOT_RE.match(line.strip())
        if m:
            dump.slots.append(TlsSlot(
                address=int(m.group(1), 16),
                value=int(m.group(2), 16),
                cls=m.group(3),
                offset=int(m.group(4)),
                annotation=m.group(5) or "",
            ))
    return dump
