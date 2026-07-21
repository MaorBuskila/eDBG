package utils

import "testing"

func countingResolver(calls *int) func(uint64) (TlsClass, string) {
	return func(value uint64) (TlsClass, string) {
		*calls++
		if value == 0 {
			return TlsClassJunk, ""
		}
		return TlsClassStack, "→ 0x1"
	}
}

func TestFlowTlsRowsWalksEightByteSlots(t *testing.T) {
	buf := []byte{
		1, 0, 0, 0, 0, 0, 0, 0,
		0, 0, 0, 0, 0, 0, 0, 0,
	}
	calls := 0
	rows := FlowTlsRows(7, 0x7bc75fd580, buf, countingResolver(&calls))
	if len(rows) != 2 {
		t.Fatalf("rows = %d want 2", len(rows))
	}
	if rows[0].Step != 7 || rows[0].Slot != 0 || rows[0].Addr != 0x7bc75fd580 {
		t.Fatalf("first row = %+v", rows[0])
	}
	if rows[0].Value != 1 || rows[0].Class != TlsClassStack || rows[0].Annot != "→ 0x1" {
		t.Fatalf("first row value/class = %+v", rows[0])
	}
	if rows[1].Slot != 1 || rows[1].Addr != 0x7bc75fd588 || rows[1].Class != TlsClassJunk {
		t.Fatalf("second row = %+v", rows[1])
	}
}

func TestFlowTlsRowsDropsATrailingPartialSlot(t *testing.T) {
	// A clipped or short read leaves fewer than eight bytes at the end. Those
	// bytes are not a slot value and must not be padded into one.
	buf := make([]byte, 12)
	calls := 0
	rows := FlowTlsRows(0, 0x1000, buf, countingResolver(&calls))
	if len(rows) != 1 {
		t.Fatalf("rows = %d want 1", len(rows))
	}
}

func TestFlowTlsRowsOnAnEmptyReadIsEmpty(t *testing.T) {
	calls := 0
	if rows := FlowTlsRows(0, 0x1000, nil, countingResolver(&calls)); len(rows) != 0 {
		t.Fatalf("rows = %d want 0", len(rows))
	}
	if calls != 0 {
		t.Fatalf("resolver called %d times on an empty read", calls)
	}
}

func TestMemoResolverAsksOncePerDistinctValue(t *testing.T) {
	// Annotating costs a read of the traced process per pointer. A flow run
	// walks the same slots thousands of times, so repeats must be free.
	calls := 0
	resolve := MemoResolver(countingResolver(&calls))
	for i := 0; i < 5; i++ {
		resolve(0x7bc75fd580)
	}
	resolve(0x7bc75fd588)
	if calls != 2 {
		t.Fatalf("underlying resolver called %d times want 2", calls)
	}
}

func TestMemoResolverReturnsTheSameAnswerEveryTime(t *testing.T) {
	calls := 0
	resolve := MemoResolver(countingResolver(&calls))
	first, firstAnnot := resolve(0x1234)
	again, againAnnot := resolve(0x1234)
	if first != again || firstAnnot != againAnnot {
		t.Fatalf("memo returned %q/%q then %q/%q", first, firstAnnot, again, againAnnot)
	}
}
