"""Sentinel framing for eDBG's ``-json`` stream.

eDBG has ~230 ``fmt.Print`` sites. Rerouting them all through a JSON writer
would be a large, risky change for no benefit, so structured events are simply
*interleaved* with the existing human output and marked with a prefix:

    \\x01EDBG{"t":"stop", ...}      <- structured event
    pid=12345 tid=12350            <- ordinary log text, unchanged

``\\x01`` is non-printable and does not occur in normal REPL output. Verified
on-device: the prefix, JSON escapes, and lines up to 256 KB all survive
``adb shell su -c`` intact, and no PTY is allocated so there is no CRLF
translation.

Imports no DPG — this module stays testable headless.
"""

from __future__ import annotations

import json

from gui import events

SENTINEL = "\x01EDBG"


def is_event(line: str) -> bool:
    return line.startswith(SENTINEL)


def decode_line(line: str):
    """Decode one output line into an event, or a :class:`LogLine`.

    Never raises: a malformed payload becomes :class:`events.Malformed` so a
    corrupt frame degrades to a visible diagnostic instead of killing the
    reader thread.
    """
    if not is_event(line):
        return events.LogLine(text=line)

    payload = line[len(SENTINEL):]
    try:
        obj = json.loads(payload)
    except (ValueError, TypeError) as exc:
        return events.Malformed(raw=payload, error=str(exc))

    if not isinstance(obj, dict):
        return events.Malformed(raw=payload, error="event is not an object")

    return events.build(obj)


def decode_lines(lines):
    """Decode an iterable of raw lines."""
    return [decode_line(l) for l in lines]


def encode(obj: dict) -> str:
    """Frame an event object. Used by tests and fixture generation."""
    return SENTINEL + json.dumps(obj, separators=(",", ":"))
