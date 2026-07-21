"""TLS dump parsing.

Format is emitted by Client.HandleTls (cli/repl.go:602). Slot rows carry a
trailing annotation only for classes that have one, so the parser must accept
both shapes — a row without an annotation is not a malformed row.
"""

from __future__ import annotations

import pytest

from gui import parse, textdump

# Real shape from cli/repl.go:666-688.
DUMP = """\
tls tid=12350 map=[anon:stack_and_tls:12350] 0x7b80e00000-0x7b80e10000
base=0x7b80e0ff00  len=0x30
0x7b80e0ff00  0x7b80e12340  code  +0x0 (#0)  libloader.so+0x1234
0x7b80e0ff08  0x7b80e50000  heap  +0x8 (#8)  -> 0x0000000100000001 |........|
0x7b80e0ff10  0x4142434445464748  junk  +0x10 (#16)  |HGFEDCBA|
0x7b80e0ff18  0x0  junk  +0x18 (#24)
0x7b80e0ff20  0x7b80e0ff80  stack  +0x20 (#32)
0x7b80e0ff28  0x7b12345678  mapped  +0x28 (#40)  -> "/data/app/lib/libc.so"
0x7b80e0ff30  0x7b80f00000  string  +0x30 (#48)  -> "hello"
"""


def _lines(text: str) -> list[str]:
    return text.splitlines()


def test_header_is_parsed():
    d = parse.parse_tls(_lines(DUMP))
    assert d is not None
    assert d.tid == 12350
    assert d.map_name == "[anon:stack_and_tls:12350]"
    assert (d.map_start, d.map_end) == (0x7B80E00000, 0x7B80E10000)
    assert d.base == 0x7B80E0FF00
    assert d.length == 0x30


def test_all_slots_parsed_in_order():
    d = parse.parse_tls(_lines(DUMP))
    assert len(d.slots) == 7
    assert [s.address for s in d.slots] == [
        0x7B80E0FF00, 0x7B80E0FF08, 0x7B80E0FF10,
        0x7B80E0FF18, 0x7B80E0FF20, 0x7B80E0FF28, 0x7B80E0FF30,
    ]


@pytest.mark.parametrize("idx,cls", [
    (0, "code"), (1, "heap"), (2, "junk"), (3, "junk"),
    (4, "stack"), (5, "mapped"), (6, "string"),
])
def test_all_six_classes_round_trip(idx, cls):
    d = parse.parse_tls(_lines(DUMP))
    assert d.slots[idx].cls == cls


def test_row_without_annotation_parses():
    d = parse.parse_tls(_lines(DUMP))
    s = d.slots[3]
    assert s.value == 0 and s.offset == 0x18 and s.annotation == ""


def test_annotation_kept_verbatim_including_spaces():
    d = parse.parse_tls(_lines(DUMP))
    assert d.slots[1].annotation == "-> 0x0000000100000001 |........|"
    assert d.slots[5].annotation == '-> "/data/app/lib/libc.so"'


def test_offset_and_decimal_agree():
    d = parse.parse_tls(_lines(DUMP))
    for s in d.slots:
        assert s.offset == s.address - d.base


def test_partial_dump_parses_slots_received():
    # A truncated read must yield what arrived, never raise.
    truncated = _lines(DUMP)[:4]
    d = parse.parse_tls(truncated)
    assert len(d.slots) == 2


def test_header_only_yields_empty_slots():
    d = parse.parse_tls(_lines(DUMP)[:2])
    assert d.slots == []


def test_no_tls_output_returns_none():
    assert parse.parse_tls(["(eDBG) info thread", "  [0] 12350: main"]) is None


def test_error_replies_are_not_dumps():
    for msg in ["Not stopped on a thread.", "Usage: tls [reg|addr]",
                "base 0x1 outside [anon:stack_and_tls:1] 0x2-0x3"]:
        assert parse.parse_tls([msg]) is None


def test_ansi_is_stripped():
    d = parse.parse_tls(_lines(DUMP.replace("code", "\x1b[32mcode\x1b[0m")))
    assert d.slots[0].cls == "code"


def test_tls_to_text_round_trips_every_slot():
    d = parse.parse_tls(_lines(DUMP))
    out = textdump.tls_to_text(d)
    assert "tid=12350" in out
    assert out.count("\n") >= len(d.slots)
    for s in d.slots:
        assert f"0x{s.address:x}" in out


def test_tls_to_text_handles_none():
    # Every copy getter yields placeholder text on an empty session.
    assert textdump.tls_to_text(None) == "(no tls)"
