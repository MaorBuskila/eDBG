package utils

import (
	"testing"

	"golang.org/x/arch/arm64/arm64asm"
)

func TestShouldOverFallthrough(t *testing.T) {
	inLib := func(addr uint64) bool { return addr < 0x1000 }
	cases := []struct {
		name       string
		level      int
		op         arm64asm.Op
		isRet      bool
		target     uint64
		inLib      func(uint64) bool
		wantSkip   bool
	}{
		{"L0 BL into", 0, arm64asm.BL, false, 0x2000, nil, false},
		{"L1 BL over", 1, arm64asm.BL, false, 0x2000, nil, true},
		{"L1 B follow", 1, arm64asm.B, false, 0x2000, nil, false},
		{"L1 RET follow", 1, arm64asm.RET, true, 0x2000, nil, false},
		{"L2 B over", 2, arm64asm.B, false, 0x2000, nil, true},
		{"L2 BL over", 2, arm64asm.BL, false, 0x2000, nil, true},
		{"L2 RET follow", 2, arm64asm.RET, true, 0x2000, nil, false},
		{"L3 BL in-lib", 3, arm64asm.BL, false, 0x100, inLib, false},
		{"L3 BL out-lib", 3, arm64asm.BL, false, 0x2000, inLib, true},
		{"L3 B out-lib", 3, arm64asm.B, false, 0x2000, inLib, true},
		{"L3 RET always", 3, arm64asm.RET, true, 0x2000, inLib, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got := ShouldOverFallthrough(c.level, c.op, c.isRet, c.target, c.inLib)
			if got != c.wantSkip {
				t.Fatalf("got %v want %v", got, c.wantSkip)
			}
		})
	}
}
