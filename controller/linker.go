package controller

import (
	"eDBG/config"
	"eDBG/utils"
	"fmt"
	"os"
	"strings"
)

type LinkerInfo struct {
	Path              string
	CtorSymbolOffset  uint64
	SonameFieldOffset uint64
}

var linkerSearchPaths = []string{
	"/apex/com.android.runtime/bin/linker64",
	"/system/bin/linker64",
	"/system/bin/bootstrap/linker64",
}

const callConstructorsSymbol = "__dl__ZN6soinfo17call_constructorsEv"

func findZygotePid() uint32 {
	content, err := utils.RunCommand("sh", "-c", "ps -A -o pid,name | grep -E 'zygote64$' | head -1")
	if err != nil {
		return 0
	}
	parts := strings.Fields(strings.TrimSpace(content))
	if len(parts) < 1 {
		return 0
	}
	var pid uint32
	fmt.Sscanf(parts[0], "%d", &pid)
	return pid
}

func ResolveLinkerInfo(process *Process) (*LinkerInfo, error) {
	linkerPath := ""

	if process != nil {
		pid := process.WorkPid
		if pid == 0 && len(process.PidList) > 0 {
			pid = process.PidList[0]
		}
		if pid == 0 {
			pid = findZygotePid()
			config.Debugf("ResolveLinkerInfo: no target pid, using zygote64 pid=%d", pid)
		}
		if pid == 0 {
			pid = 1
		}
		config.Debugf("ResolveLinkerInfo: using pid=%d to read maps", pid)
		maps, err := GetProcMaps(pid)
		if err == nil {
			for _, seg := range maps.segments {
				if seg.libName == "linker64" {
					config.Debugf("ResolveLinkerInfo: found linker64 segment at %s", seg.libPath)
					if _, err := os.Stat(seg.libPath); err == nil {
						linkerPath = seg.libPath
						break
					}
				}
			}
		} else {
			config.Debugf("ResolveLinkerInfo: GetProcMaps(%d) failed: %v", pid, err)
		}
	}

	if linkerPath == "" {
		for _, p := range linkerSearchPaths {
			if _, err := os.Stat(p); err == nil {
				linkerPath = p
				config.Debugf("ResolveLinkerInfo: fallback linker path=%s", p)
				break
			}
		}
	}

	if linkerPath == "" {
		return nil, fmt.Errorf("cannot locate linker64 on device")
	}

	config.Debugf("ResolveLinkerInfo: looking up symbol %s in %s", callConstructorsSymbol, linkerPath)
	ctorOffset, err := utils.LookupSymbolOffset(linkerPath, callConstructorsSymbol)
	if err != nil {
		return nil, fmt.Errorf("failed to resolve %s in %s: %w", callConstructorsSymbol, linkerPath, err)
	}
	config.Debugf("ResolveLinkerInfo: ctorOffset=0x%x, sonameOffset=%d", ctorOffset, config.DefaultSonameOffset)

	return &LinkerInfo{
		Path:              linkerPath,
		CtorSymbolOffset:  ctorOffset,
		SonameFieldOffset: config.DefaultSonameOffset,
	}, nil
}
