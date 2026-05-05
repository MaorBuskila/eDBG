package config

import "fmt"

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
	HW_BREAKPOINT_R       	 = 1
	HW_BREAKPOINT_W          = 2
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
var Verbose = false

func Debugf(format string, args ...interface{}) {
	if Verbose {
		fmt.Printf("[DEBUG] "+format+"\n", args...)
	}
}