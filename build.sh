#!/bin/bash
set -e

NDK_VERSION="${NDK_VERSION:-29.0.13599879}"
NDK_ROOT="$HOME/Library/Android/sdk/ndk/$NDK_VERSION"
CMD_CLANG="/opt/homebrew/opt/llvm/bin/clang"

if [ ! -d "$NDK_ROOT" ]; then
    echo "ERROR: NDK not found at $NDK_ROOT"
    echo "Available versions:"
    ls ~/Library/Android/sdk/ndk/
    exit 1
fi

if [ ! -x "$CMD_CLANG" ]; then
    echo "ERROR: Homebrew LLVM clang not found at $CMD_CLANG"
    echo "Install with: brew install llvm"
    exit 1
fi

export NDK_ROOT
export PATH="$NDK_ROOT/toolchains/llvm/prebuilt/darwin-arm64/bin:$PATH"

echo "==> Building eBPF module..."
make ebpf_module CMD_CLANG="$CMD_CLANG"

echo "==> Generating assets..."
make assets

echo "==> Building eDBG (arm64 android)..."
make build

echo "==> Building MCP installer (host native)..."
make -f Makefile_installer current

echo ""
echo "Done. Outputs:"
ls -lh bin/eDBG_arm64 bin/edbg-mcp-install 2>/dev/null
