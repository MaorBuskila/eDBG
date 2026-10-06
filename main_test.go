package main

import (
	"eDBG/config"
	"testing"
)

func TestParseBreakPointsAccess(t *testing.T) {
	got, err := ParseBreakPoints("[0x10,0x20:x,0x30:r,0x40:W,0x50:rw,0x60:WR]")
	if err != nil {
		t.Fatal(err)
	}
	want := []breakSpec{
		{0x10, 0},
		{0x20, config.HW_BREAKPOINT_X},
		{0x30, config.HW_BREAKPOINT_R},
		{0x40, config.HW_BREAKPOINT_W},
		{0x50, config.HW_BREAKPOINT_RW},
		{0x60, config.HW_BREAKPOINT_RW},
	}
	if len(got) != len(want) {
		t.Fatalf("len %d, want %d", len(got), len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("[%d] got %+v want %+v", i, got[i], want[i])
		}
	}
	if _, err := ParseBreakPoints("0x10:rx"); err == nil {
		t.Fatal("expected error for :rx")
	}
	if _, err := ParseBreakPoints("nope"); err == nil {
		t.Fatal("expected error for bad address")
	}
}
