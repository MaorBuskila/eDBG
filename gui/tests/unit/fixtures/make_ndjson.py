"""Generate the golden NDJSON fixtures from the schema in SPEC_gui_v2.md.

These are hand-authored stand-ins until Go's `-json` mode lands; Checkpoint 1
replaces them with a real device capture. Keeping them generated (rather than
hand-typed) means the schema lives in one place and regenerating is trivial.

    python3 gui/tests/unit/fixtures/make_ndjson.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../..")))

from gui.protocol import SENTINEL          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

REG_NAMES = [f"X{i}" for i in range(30)] + ["LR", "SP", "PC"]
BASE = 0x7BE3F81000


def regs():
    out = []
    for i, n in enumerate(REG_NAMES):
        r = {"n": n, "v": hex(BASE + i * 8)}
        if n in ("X1", "LR", "PC"):
            r["sym"] = f"libloader.so+0x{0x54A428 + i:x}"
            r["ptr"] = True
        if n == "X1":
            r["deref"] = '"hello"'
        out.append(r)
    return out


def stop_event(seq, hit, reason="breakpoint"):
    return {
        "t": "stop", "seq": seq, "ts_ns": 1690000000000 + seq,
        "reason": reason, "detail": "full",
        "hit": hit, "pid": 12345, "tid": 12350,
        "pc": hex(0x7BE3F81428), "rva": "0x12a40c", "lib": "libloader.so",
        "regs": regs(),
        "disasm": [
            {"a": hex(0x7BE3F81428), "sym": "libloader.so+0x54a428",
             "m": "MOV", "o": "X0, X1", "cur": True},
            {"a": hex(0x7BE3F8142C), "sym": "libloader.so+0x54a42c",
             "m": "BL", "o": "#0x7be3f90000"},
            {"a": hex(0x7BE3F81430), "m": "RET"},
        ],
        "bt": [
            {"i": 0, "a": hex(0x7BE3F81428), "sym": "libloader.so + 0x54a428"},
            {"i": 1, "a": hex(0x7BE3F90000), "sym": "?? ()"},
        ],
        "threads": [
            {"i": 0, "tid": 12350, "name": "main", "cur": True},
            {"i": 1, "tid": 12351, "name": "Worker"},
        ],
        "bps": [
            {"id": 0, "on": True, "type": "software",
             "lib": "libloader.so", "off": "0x12a40c"},
            {"id": 1, "on": False, "type": "hardware", "off": "0x7be3f81428"},
        ],
        "display": [
            {"id": 0, "addr": hex(0x7BE3F90000), "name": "buf",
             "hex": "48656c6c6f20576f726c642100000000"},
        ],
        "pointers": [
            {"reg": "X1", "addr": hex(BASE + 8),
             "hex": "0100000000000000feedfacecafebeef"},
        ],
    }


def write(name, lines):
    path = os.path.join(HERE, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"  wrote {name} ({len(lines)} lines)")


def ev(obj):
    return SENTINEL + json.dumps(obj, separators=(",", ":"))


def main():
    hello = {"t": "hello", "seq": 0, "ts_ns": 1690000000000, "schema": 1,
             "edbg_version": "dev", "pkg": "com.shlomi.RollABall2019",
             "lib": "libloader.so", "edbg_pid": 30695}

    # 1. one full stop, interleaved with human log lines
    write("session_basic.ndjson", [
        ev(hello),
        "ConfigMap{edbg_pid=30695,thread_whitelist=0}",
        "HW breakpoint set at 0x7be3f81428 (libloader.so+0x12a40c)",
        ev(stop_event(1, 1)),
        "(eDBG) ",
    ])

    # 2. several stops — timeline / ring-buffer material
    lines = [ev(hello)]
    for i in range(1, 6):
        lines.append(ev(stop_event(i, i,
                                   "breakpoint" if i == 1 else "step")))
    write("session_multi_stop.ndjson", lines)

    # 3. flow: throttled progress, then completion
    flow = [ev(hello), ev(stop_event(1, 1))]
    for step in range(0, 10000, 1000):
        flow.append(ev({"t": "flow_progress", "seq": 100 + step // 1000,
                        "ts_ns": 1690000001000 + step, "step": step,
                        "max": 10000, "pc": hex(0x7BE3F81428 + step * 4),
                        "rva": hex(0x12A40C + step * 4)}))
    flow.append(ev({"t": "flow_done", "seq": 200, "ts_ns": 1690000002000,
                    "steps": 9999, "reason": "reached_LR",
                    "csv_path": "/data/local/tmp/libloader_0x12a40c_flow.csv"}))
    write("session_flow.ndjson", flow)

    # 4. standalone command responses
    write("session_commands.ndjson", [
        ev(hello),
        ev({"t": "breakpoints", "seq": 10, "ts_ns": 1, "bps": [
            {"id": 0, "on": True, "type": "software",
             "lib": "libloader.so", "off": "0x12a40c"}]}),
        ev({"t": "threads", "seq": 11, "ts_ns": 2, "threads": [
            {"i": 0, "tid": 12350, "name": "main", "cur": True}]}),
        ev({"t": "memory", "seq": 12, "ts_ns": 3, "addr": hex(0x7BE3F90000),
            "hex": "48656c6c6f20576f726c642100000000", "src": "examine"}),
        ev({"t": "error", "seq": 13, "ts_ns": 4,
            "msg": "ReadMemory failed: no such process", "cmd": "x 0x0 64"}),
    ])

    # 5. adversarial: things a tolerant decoder must survive
    write("session_adversarial.ndjson", [
        ev(hello),
        SENTINEL + '{"t":"stop","regs":[',          # truncated JSON
        SENTINEL + "not json at all",
        SENTINEL + '["array","not","object"]',      # valid JSON, wrong shape
        ev({"t": "future_event_type", "seq": 99, "ts_ns": 5, "wat": True}),
        "plain log line that must survive",
        ev(stop_event(2, 2)),                        # recovery
    ])


if __name__ == "__main__":
    print("generating NDJSON fixtures:")
    main()
