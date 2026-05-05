package utils

import (
	"debug/elf"
	"fmt"
)

func LookupSymbolOffset(elfPath string, symbolName string) (uint64, error) {
	f, err := elf.Open(elfPath)
	if err != nil {
		return 0, fmt.Errorf("failed to open ELF %s: %w", elfPath, err)
	}
	defer f.Close()

	syms, _ := f.Symbols()
	for _, s := range syms {
		if s.Name == symbolName && s.Value != 0 {
			offset, err := vaddrToFileOffset(f, s.Value)
			if err != nil {
				return 0, err
			}
			return offset, nil
		}
	}

	dynSyms, _ := f.DynamicSymbols()
	for _, s := range dynSyms {
		if s.Name == symbolName && s.Value != 0 {
			offset, err := vaddrToFileOffset(f, s.Value)
			if err != nil {
				return 0, err
			}
			return offset, nil
		}
	}

	return 0, fmt.Errorf("symbol %q not found in %s", symbolName, elfPath)
}

func vaddrToFileOffset(f *elf.File, vaddr uint64) (uint64, error) {
	for _, p := range f.Progs {
		if p.Type != elf.PT_LOAD {
			continue
		}
		if vaddr >= p.Vaddr && vaddr < p.Vaddr+p.Memsz {
			return p.Off + (vaddr - p.Vaddr), nil
		}
	}
	return 0, fmt.Errorf("vaddr 0x%x not in any PT_LOAD segment", vaddr)
}
