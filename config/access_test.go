package config

import "testing"

func TestSplitAccessSuffix(t *testing.T) {
	cases := []struct {
		in       string
		addr     string
		typ      int
		explicit bool
	}{
		{"0x1234", "0x1234", 0, false},
		{"0x20:x", "0x20", HW_BREAKPOINT_X, true},
		{"0x30:r", "0x30", HW_BREAKPOINT_R, true},
		{"0x40:W", "0x40", HW_BREAKPOINT_W, true},
		{"0x50:rw", "0x50", HW_BREAKPOINT_RW, true},
		{"0x60:WR", "0x60", HW_BREAKPOINT_RW, true},
	}
	for _, c := range cases {
		addr, typ, explicit, err := SplitAccessSuffix(c.in)
		if err != nil {
			t.Fatalf("%s: %v", c.in, err)
		}
		if addr != c.addr || typ != c.typ || explicit != c.explicit {
			t.Fatalf("%s: got %s %d %v", c.in, addr, typ, explicit)
		}
	}
	if _, _, _, err := SplitAccessSuffix("0x10:rx"); err == nil {
		t.Fatal("expected error for :rx")
	}
}
