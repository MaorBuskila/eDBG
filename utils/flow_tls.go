package utils

import "encoding/binary"

// TlsSnapshot is the classification context a flow run takes once, at entry.
// A flow traces one function, so its mappings do not move; re-reading them per
// step is what would make --tls unaffordable.
type TlsSnapshot struct {
	Regions    []MapRegion
	StackStart uint64
	StackEnd   uint64
}

// FlowTlsRow is one slot of the stack_and_tls dump at one step, as written to
// the run's <lib>_0x<rva>_flow_tls.csv sidecar.
type FlowTlsRow struct {
	Step  int
	Slot  int
	Addr  uint64
	Value uint64
	Class TlsClass
	Annot string
}

// FlowTlsRows reads buf as consecutive 8-byte slots based at base. Trailing
// bytes shorter than a slot come from a clipped or short read and are dropped
// rather than padded into a value that was never there.
func FlowTlsRows(step int, base uint64, buf []byte, resolve func(uint64) (TlsClass, string)) []FlowTlsRow {
	rows := make([]FlowTlsRow, 0, len(buf)/8)
	for off := 0; off+8 <= len(buf); off += 8 {
		value := binary.LittleEndian.Uint64(buf[off : off+8])
		class, annot := resolve(value)
		rows = append(rows, FlowTlsRow{
			Step:  step,
			Slot:  off / 8,
			Addr:  base + uint64(off),
			Value: value,
			Class: class,
			Annot: annot,
		})
	}
	return rows
}

// MemoResolver caches resolve per distinct value. Classifying and annotating a
// pointer costs reads of the traced process, and a run walks the same slots at
// every one of its steps.
func MemoResolver(resolve func(uint64) (TlsClass, string)) func(uint64) (TlsClass, string) {
	type answer struct {
		class TlsClass
		annot string
	}
	seen := make(map[uint64]answer)
	return func(value uint64) (TlsClass, string) {
		if a, ok := seen[value]; ok {
			return a.class, a.annot
		}
		class, annot := resolve(value)
		seen[value] = answer{class, annot}
		return class, annot
	}
}
