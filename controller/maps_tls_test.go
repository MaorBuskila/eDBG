package controller

import "testing"

func TestFindStackAndTls(t *testing.T) {
	maps := &ProcMaps{pid: 8933}
	content := `7bc720e000-7bc7504000 ---p 00000000 00:00 0
7bc7504000-7bc7505000 ---p 00000000 00:00 0
7bc7505000-7bc7601000 rw-p 00000000 00:00 0 [anon:stack_and_tls:8994]
7be2400000-7be2500000 r-xp 00000000 103:2f 123 /apex/com.android.runtime/lib64/bionic/libc.so
`
	if err := maps.ParseMapsContent([]byte(content)); err != nil {
		t.Fatal(err)
	}
	start, end, name, err := maps.FindStackAndTls(8994)
	if err != nil {
		t.Fatal(err)
	}
	if start != 0x7bc7505000 || end != 0x7bc7601000 {
		t.Fatalf("range 0x%x-0x%x", start, end)
	}
	if name != "[anon:stack_and_tls:8994]" {
		t.Fatalf("name %q", name)
	}
	if _, _, _, err := maps.FindStackAndTls(1); err == nil {
		t.Fatal("expected error for missing tid")
	}
	regions := maps.Regions()
	if len(regions) != 4 {
		t.Fatalf("regions len %d", len(regions))
	}
}
