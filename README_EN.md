<div align="center">
  <img src="logo.png"/>

  [![GitHub Release](https://img.shields.io/github/v/release/ShinoLeah/eDBG?style=flat-square)](https://github.com/ShinoLeah/eDBG/releases)
  [![License](https://img.shields.io/github/license/ShinoLeah/eDBG?style=flat-square)](LICENSE)
  [![Platform](https://img.shields.io/badge/platform-Android%20ARM64-red.svg?style=flat-square)](https://www.android.com/)
  ![GitHub Repo stars](https://img.shields.io/github/stars/ShinoLeah/eDBG)

  [简体中文](README.md) | English

</div>

> eDBG is a lightweight CLI debugger based on eBPF.<br />
>
> Compared to traditional ptrace-based debuggers, eDBG doesn't directly intrude or attach to processes, offering stronger resistance to interference and anti-detection capabilities.

## ✨ Features

- eBPF implementation introduces minimal footprint, making it almost impossible to be detected by target programs
- Supports common debugging functionalities (see "Command Details")
- Uses pwndbg-like CLI interface with GDB-style interactions for ease of use
- File+offset based breakpoint registration enables quick startup and supports multi-thread/process debugging
- **Flow trace**: record a linear instruction path from an RVA into a CSV (CLI `-flow` or REPL `flow`), stepping with hardware breakpoints until control returns to the saved LR (or max steps)
- **Hit context**: each stop prints the **pid** and **tid** of the thread that hit the probe (when output is not suppressed)
- Supports MCP mode, giving LLMs stable dynamic-analysis capabilities with little to no need to bypass anti-debugging.

## 💕 Demo

![](demo.png)

## 🚀 Requirements

- Currently only supports ARM64 Android devices with ROOT access (Recommended with [KernelSU](https://github.com/tiann/KernelSU))
- Kernel version 5.10+ (Check via `uname -r`)

## ⚙️ Usage

1. Download prebuilt binaries from [Releases](https://github.com/ShinoLeah/eDBG/releases)

2. Push to device and grant permissions:

   ```shell
   adb push eDBG /data/local/tmp
   adb shell
   su
   chmod +x /data/local/tmp/eDBG
   ```

3. Start debugger:

   ```shell
   ./eDBG -n com.package.name -l libname.so -b 0x123456
   ```

   | Option | Description |
   | :----: | :---------- |
   |   -n   | Target app package name |
   |   -p   | Attach to an existing process by PID |
   |   -l   | Target shared library name |
   |   -b   | Initial file-offset breakpoints/watchpoints (e.g. `0x1234,0x5678:rw`) |
   |   -vb  | Initial **virtual** (IDA-style) RVA breakpoints/watchpoints (do not combine with `-b`) |
   
4. Launch target app:

   > eDBG can attach to running processes but won't auto-launch apps.

## ⚠️ Notes

- Debugging system libraries (e.g., `libc.so`, `libart.so`) may cause lag due to file+offset mechanism
- Program pause isn't supported without active breakpoints
- **Command works only when program is suspended**  
- Thread ID specification during startup isn't supported
- Maximum 20 active breakpoints

## 💡 Commands

- **Breakpoints** `break/b`

  - Offset: `b 0x1234` (relative to debugger's initial library)
  - Memory: `b 0x6e9bfe214c` (requires running process)
  - Library+Offset: `b library.so+0x1234`
  - Relative: `b $+1` (current position +1 instruction)
- **Vertual Breakpoints** `vbreak/vb` Set Breakpoints on vertual offsets
- **Continue** `continue/c`: Resume execution
- **Stepping**

  - `step/s`: Step into functions
  - `next/n`: Step over functions
- **Memory Examination** `examine/x`

  - Address: `x 0x12345678` (default 16 bytes)
  - Address+Length: `x 0x12345678 128`
  - Address+Type: `x X0 ptr/int/str`
  - Address can be expressions including register names. e.g. `x SP+128 X1+0x58`
- **Exit** `quit/q`: Exit debugger (won't affect target process)
- **Information** `info/i`

  - `info b/break`: List breakpoints (`[+]`=enabled, `[-]`=disabled)
  - `info register/reg/r`: Show registers
  - `info thread/t`: List threads & filters
- **Breakpoint Management**

  - `enable <id>`: Enable breakpoint
  - `disable <id>`: Disable breakpoint
  - `delete <id>`: Remove breakpoint
- **Repeat Command**: Press Enter with empty input
- **Flow trace** `flow`: Log PCs to `/data/local/tmp/` CSV (see **Flow trace** under Advanced Usage); CLI shortcut: `-flow` with `-b` / `-vb`

More commands in "Advanced Usage".

## 🛫 Compilation

1. **Environment Setup** (x86 Linux cross-compilation)

   ```shell
   sudo apt-get update
   sudo apt-get install golang-1.18
   sudo apt-get install clang-14
   export GOPROXY=https://goproxy.cn,direct
   export GO111MODULE=on
   ```

2. **NDK Setup** Install Android NDK `29.0.13599879`, or set `NDK_VERSION` to an installed version.

3. **Build**

   ```shell
   git clone --recursive https://github.com/ShinoLeah/eDBG.git
   cd eDBG
   ./build.sh
   ```

   `build.sh` uses Homebrew LLVM for eBPF, generates embedded assets, and builds
   `bin/eDBG_arm64`. Override the NDK when needed:

   ```shell
   NDK_VERSION=30.0.15729638 ./build.sh
   ```

## 🧑‍💻 Advanced Usage

### Extra CLI flags

| Option | Description |
| :----- | :---------- |
| `-t` | Thread name filter for eBPF (comma-separated, e.g. `[Binder,Main]`) |
| `-u` | Target app UID for process filtering |
| `-i` | Load config from a saved `.edbg` JSON file |
| `-s` | Save progress to the same path as `-i` |
| `-o` | Save progress to a specific output file |
| `-hide-register` | Do not print registers on each stop |
| `-hide-disassemble` | Do not print disassembly on each stop |
| `-bt` | Automatically print an unwind backtrace on each stop |
| `-prefer` | Breakpoint backend: `uprobe` or `hardware` |
| `-disable-color` | Plain text output |
| `-show-vertual` | Show virtual (IDA-style) addresses by default |
| `-disable-package-check` | Skip verifying that `-n` is installed |
| `-hit-only` / `-ho` | Log breakpoint hits without stopping the target (signal-based trace) |
| `-script` / `-sc` | Path to a script file: non-control commands run automatically on each hit |
| `-v` | Verbose debug logging |
| `-global-hwbrk` | Use system-wide hardware breakpoints (`pid=-1`) instead of per-thread HW breaks; try if stepping or flow misbehaves on some kernels |
| `-mcp` | Start the HTTP MCP server on device |
| `-mcp-port` | MCP port (default `19810`) |

### Breakpoint access modes

`-b` and `-vb` accept an optional access suffix on each comma-separated
address:

| Suffix | Trigger |
| :----- | :------ |
| `:x` | Execute (same behavior as a bare address; backend follows `-prefer`) |
| `:r` | Read (hardware watchpoint) |
| `:w` | Write (hardware watchpoint) |
| `:rw` | Read or write (hardware watchpoint) |

```shell
./eDBG -n com.example.app -l libfoo.so \
  -b 0x1234:x,0x8000:rw -prefer hardware -bt
```

Data watchpoints are four bytes wide and require four-byte-aligned addresses.
The REPL also accepts the suffix with `hbreak`, for example `hb 0x8000:rw`;
the existing `rwatch` and `watch` commands remain available.

When the target is `linker64`, eDBG waits for the target process rather than
waiting for `linker64` to appear as a loaded soname, then resolves the offset
from the process maps and arms the hardware breakpoint.

### Flow trace (CSV export)

Single-pass **control-flow** logging: after the process hits your **first** breakpoint from `-b` or `-vb`, eDBG steps with the same engine as `step` / `next`, records each PC (virtual address + RVA when possible), and writes **`/data/local/tmp/<lib>_0x<rva>_flow.csv`** on the device. The trace stops when **PC equals the LR saved at the entry step** (function return) or when **`--max`** steps is reached.

**CLI** (non-interactive: REPL does not start; flow runs then eDBG exits):

```shell
./eDBG -n com.example.app -l libfoo.so -b 0x1234 -flow
./eDBG -n com.example.app -l libfoo.so -vb 0x1a2b3c -flow -flow-over -flow-max 5000 -v
```

| Flag | Meaning |
| :--- | :------ |
| `-flow` | Enable flow mode (**requires** `-b` or `-vb` with at least one address; the first address defines the trace RVA) |
| `-flow-over` | Step **over** calls (`next`) instead of into (`step`) |
| `-flow-max N` | Maximum steps (default `10000`) |
| `-flow-regs` | Add `x0`–`x29`, `lr`, `sp`, `pc`, `pstate` columns to the CSV |
| `-flow-mem X0` | Each row also logs the pointer in that register and the 8-byte value at that address (`ERR` if unreadable) |
| `-flow-quiet` | Suppress per-step banner lines (CSV is still written) |

If the library is not loaded yet, eDBG waits (up to 5 minutes) before starting the trace.

**REPL** (same behaviour, manual RVA):

```text
flow 0x1234 [--over] [--max 5000] [--regs] [--mem X0] [--quiet]
```

More commands:

- **Hardware breakpoints** `hbreak`: Usage is similar to `break`, but limited to a maximum of 4.

- **Write watch** `watch`: Usage is similar to `break`, triggers when a specified address is written to (hardware breakpoint).

- **Read watch** `rwatch`: Same as above, triggers when a specified address is read (also a hardware breakpoint).

- **Function Finish** `finish/fi`: Execute until function return

- **Run Until** `until/u <address>`: Execute to specified address

- **Memory Display** `display/disp`

  - Address: `disp 0x123456` (auto-print on breaks/steps)
  - Address+Length: `disp 0x123456 128`
  - Named: `disp 0x123456 128 name`

  > ⚠️ Memory address changes (e.g., app restart) may invalidate displays

- **Undisplay** `undisplay/undisp <id>`: Remove auto-display

- **Write Memory** `write address hexstring` Target address must be writable

- **Memory Dump** `dump address length filename`

- **Backtrace** `backtrace/bt` or `backtrace1/bt1`

- **Code Listing** `list/l/disassemble/dis`

  - Current: `l` (10 instructions from PC)
  - Specific: `l 0x1234` (10 instructions)
  - Custom: `l 0x1234 20` (20 instructions)

- **Thread Control** `thread/t`

  - `t`: List threads
  - `t + 0`: Add thread filter (use `info t` for IDs)
  - `t - 0`: Remove filter
  - `t all`: Clear all filters
  - `t +n threadname`: Filter by thread name

- **Set Symbol** `set address name`：Name specified address

## 🤖 MCP Mode

[README_mcp_en.md](README_mcp_en.md)

## 💭 Implementation

- Uprobe breakpoints are the default for your explicit breakpoints; it is recommended to place them on jump instructions (B-series/RET/CBZ/TBNZ) where possible to reduce noise in `/proc/maps`.
- The `step` / `next` / `finish` / `until` / **flow** paths rely on **hardware breakpoints** (not visible to ordinary user-mode inspection of the target). If stepping or flow is unreliable on a device, try **`-global-hwbrk`** or tune **`-prefer`** (`uprobe` vs `hardware`) for your main breakpoints.

## 🤝 References

- [SeeFlowerX/stackplz](https://github.com/SeeFlowerX/stackplz/tree/dev)
- [pwndbg](https://github.com/pwndbg/pwndbg)

## ❤️ Support

- Star this repo 🌟 if you find it useful
- Issues and PRs are welcome!
