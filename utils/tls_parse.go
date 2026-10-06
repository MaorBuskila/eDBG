package utils

import (
	"encoding/binary"
	"fmt"
	"strings"
)

const TlsDumpLen = 0x800

type MapRegion struct {
	Start uint64
	End   uint64
	Perm  string
	Path  string
}

type TlsClass string

const (
	TlsClassString TlsClass = "string"
	TlsClassCode   TlsClass = "code"
	TlsClassStack  TlsClass = "stack"
	TlsClassHeap   TlsClass = "heap"
	TlsClassMapped TlsClass = "mapped"
	TlsClassJunk   TlsClass = "junk"
)

func FindRegion(regions []MapRegion, addr uint64) *MapRegion {
	for i := range regions {
		if regions[i].Start <= addr && addr < regions[i].End {
			return &regions[i]
		}
	}
	return nil
}

func ClipDumpRange(base, mapEnd, wantLen uint64) (start, length uint64) {
	if base >= mapEnd {
		return base, 0
	}
	remaining := mapEnd - base
	if wantLen > remaining {
		wantLen = remaining
	}
	return base, wantLen
}

func Classify(value uint64, regions []MapRegion, stackStart, stackEnd uint64, looksLikeString func(uint64) bool) TlsClass {
	if value < 0x1000 {
		return TlsClassJunk
	}
	if looksLikeString != nil && looksLikeString(value) {
		return TlsClassString
	}
	region := FindRegion(regions, value)
	if region == nil {
		return TlsClassJunk
	}
	if strings.Contains(region.Perm, "x") {
		return TlsClassCode
	}
	if stackStart <= value && value < stackEnd {
		return TlsClassStack
	}
	if isAnonHeap(region) {
		return TlsClassHeap
	}
	return TlsClassMapped
}

func isAnonHeap(region *MapRegion) bool {
	if region.Path == "" {
		return true
	}
	if strings.HasPrefix(region.Path, "UNNAMED_") {
		return true
	}
	if strings.HasPrefix(region.Path, "[anon:") && !strings.Contains(region.Path, "stack_and_tls") {
		return true
	}
	return false
}

func LooksLikeCString(pid uint32, addr uint64) bool {
	buf := make([]byte, 16)
	n, err := ReadProcessMemory(pid, uintptr(addr), buf)
	if err != nil || n < 4 {
		return false
	}
	printable := 0
	for i := 0; i < n; i++ {
		if buf[i] == 0 {
			return printable >= 4
		}
		if buf[i] >= 0x20 && buf[i] <= 0x7e {
			printable++
		} else {
			return false
		}
	}
	return printable >= 4
}

// AsciiPreview formats bytes as an xxd-style |....| column (non-printable → '.').
func AsciiPreview(data []byte) string {
	if len(data) == 0 {
		return ""
	}
	out := make([]byte, len(data))
	for i, b := range data {
		if b >= 0x20 && b <= 0x7e {
			out[i] = b
		} else {
			out[i] = '.'
		}
	}
	return "|" + string(out) + "|"
}

// FormatLEAscii renders a uint64 slot value as little-endian |ASCII|.
func FormatLEAscii(value uint64) string {
	var b [8]byte
	binary.LittleEndian.PutUint64(b[:], value)
	return AsciiPreview(b[:])
}

// PeekPtrAnnotate returns a short "→ …" preview of *addr (C-string or qword+ASCII).
func PeekPtrAnnotate(pid uint32, addr uint64) string {
	buf := make([]byte, 32)
	n, err := ReadProcessMemory(pid, uintptr(addr), buf)
	if err != nil || n < 1 {
		return ""
	}
	buf = buf[:n]

	printable := 0
	for i := 0; i < n; i++ {
		if buf[i] == 0 {
			break
		}
		if buf[i] < 0x20 || buf[i] > 0x7e {
			printable = -1
			break
		}
		printable++
	}
	if printable >= 4 {
		end := printable
		if end > 29 {
			end = 29
		}
		s := string(buf[:end])
		if printable > 29 {
			s += "..."
		}
		return "→ \"" + s + "\""
	}

	if n < 8 {
		return ""
	}
	val := binary.LittleEndian.Uint64(buf[:8])
	return fmt.Sprintf("→ 0x%X  %s", val, AsciiPreview(buf[:8]))
}
