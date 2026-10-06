package utils

import "testing"

func testRegions() []MapRegion {
	return []MapRegion{
		{Start: 0x7be2400000, End: 0x7be2500000, Perm: "r-xp", Path: "/apex/com.android.runtime/lib64/bionic/libc.so"},
		{Start: 0x7bc7505000, End: 0x7bc7601000, Perm: "rw-p", Path: "[anon:stack_and_tls:8994]"},
		{Start: 0x7bc9000000, End: 0x7bc9100000, Perm: "rw-p", Path: "[anon:libc_malloc]"},
		{Start: 0x7be3000000, End: 0x7be3100000, Perm: "r--p", Path: "/data/app/base.apk"},
	}
}

func TestTlsClipDumpRange(t *testing.T) {
	start, length := ClipDumpRange(0x7bc75fd580, 0x7bc7601000, TlsDumpLen)
	if start != 0x7bc75fd580 {
		t.Fatalf("start = 0x%x", start)
	}
	want := uint64(0x7bc7601000 - 0x7bc75fd580)
	if length != want {
		t.Fatalf("length = 0x%x want 0x%x", length, want)
	}

	_, length = ClipDumpRange(0x7bc75fd580, 0x7bc75fd600, TlsDumpLen)
	if length != 0x80 {
		t.Fatalf("clipped length = 0x%x", length)
	}

	_, length = ClipDumpRange(0x7bc7601000, 0x7bc7601000, TlsDumpLen)
	if length != 0 {
		t.Fatalf("past end length = 0x%x", length)
	}
}

func TestTlsClassify(t *testing.T) {
	regions := testRegions()
	stackStart := uint64(0x7bc7505000)
	stackEnd := uint64(0x7bc7601000)
	stringFn := func(addr uint64) bool { return addr == 0x7be3000100 }

	cases := []struct {
		value uint64
		want  TlsClass
	}{
		{0x42, TlsClassJunk},
		{0x7be3000100, TlsClassString},
		{0x7be2445000, TlsClassCode},
		{0x7bc75fd6a0, TlsClassStack},
		{0x7bc9001000, TlsClassHeap},
		{0x7be3000050, TlsClassMapped},
		{0xdeadbeefcafe, TlsClassJunk},
	}
	for _, tc := range cases {
		got := Classify(tc.value, regions, stackStart, stackEnd, stringFn)
		if got != tc.want {
			t.Fatalf("Classify(0x%x) = %q want %q", tc.value, got, tc.want)
		}
	}
}

func TestTlsClassifyNoStringFn(t *testing.T) {
	regions := testRegions()
	got := Classify(0x7be2445000, regions, 0x7bc7505000, 0x7bc7601000, nil)
	if got != TlsClassCode {
		t.Fatalf("got %q want code", got)
	}
}

func TestFormatLEAscii(t *testing.T) {
	// LE bytes of "ABCD\0\0\0\0"
	got := FormatLEAscii(0x44434241)
	if got != "|ABCD....|" {
		t.Fatalf("got %q", got)
	}
	got = FormatLEAscii(0)
	if got != "|........|" {
		t.Fatalf("zero got %q", got)
	}
	got = AsciiPreview([]byte{0x00, 0x7f, 'x', 0x0a})
	if got != "|..x.|" {
		t.Fatalf("preview got %q", got)
	}
}
