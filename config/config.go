package config

import (
	"fmt"
	"strings"
)

const ALL_UPROBE = 0
const PREFER_UPROBE = 1 // Not Actually used. Same as ALL_UPROBE
const PREFER_PERF = 2
const ALL_PERF = 3

var Preference = PREFER_PERF
var Available_HW = 6

const (
	PERF_TYPE_BREAKPOINT     = 5
	PERF_COUNT_HW_BREAKPOINT = 6
	HW_BREAKPOINT_X          = 4
	HW_BREAKPOINT_R          = 1
	HW_BREAKPOINT_W          = 2
	HW_BREAKPOINT_RW         = HW_BREAKPOINT_R | HW_BREAKPOINT_W
	HW_BREAKPOINT_LEN_4      = 0x40
)

var RED = "\033[0;31m"
var GREEN = "\033[0;32m"
var YELLOW = "\033[0;33m"
var BLUE = "\033[0;34m"
var CYAN = "\033[0;36m"
var NC = "\033[0m"

var DisablePackageCheck = false
var SHOW_VERTUAL = false
var HitOnly = false
var FlowTracing = false
var TargetUID uint32 = 0
var WaitCtor = false
var StopOnLoad = false
var Verbose = false
var GlobalHWBreak = false

const LinkerCtorSentinelPC uint64 = 0xFFFFFFFE
const DefaultSonameOffset uint64 = 416

func Debugf(format string, args ...interface{}) {
	if Verbose {
		fmt.Printf("[DEBUG] "+format+"\n", args...)
	}
}

// ParseHWAccess maps x/r/w/rw to a perf bp_type. rw and wr are the same.
func ParseHWAccess(s string) (int, error) {
	switch strings.ToLower(s) {
	case "x":
		return HW_BREAKPOINT_X, nil
	case "r":
		return HW_BREAKPOINT_R, nil
	case "w":
		return HW_BREAKPOINT_W, nil
	case "rw", "wr":
		return HW_BREAKPOINT_RW, nil
	default:
		return 0, fmt.Errorf("invalid breakpoint access %q (use x, r, w, or rw)", s)
	}
}

// IsDataWatch is true for r/w/rw. Those cannot be uprobes.
func IsDataWatch(t int) bool {
	return t == HW_BREAKPOINT_R || t == HW_BREAKPOINT_W || t == HW_BREAKPOINT_RW
}

func HWAccessName(t int) string {
	switch t {
	case HW_BREAKPOINT_R:
		return "r"
	case HW_BREAKPOINT_W:
		return "w"
	case HW_BREAKPOINT_RW:
		return "rw"
	default:
		return "x"
	}
}

// SplitAccessSuffix splits "0x1234:rw" into address "0x1234" and HW_BREAKPOINT_RW.
// No suffix returns (arg, 0, false, nil). 0 means execute, follow -prefer.
func SplitAccessSuffix(arg string) (addr string, typ int, explicit bool, err error) {
	i := strings.LastIndex(arg, ":")
	if i < 0 {
		return arg, 0, false, nil
	}
	t, err := ParseHWAccess(arg[i+1:])
	if err != nil {
		return "", 0, false, err
	}
	return arg[:i], t, true, nil
}
