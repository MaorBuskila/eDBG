package module

import (
	"eDBG/config"
	"eDBG/controller"
	"eDBG/utils"
	"fmt"
	"github.com/cilium/ebpf/perf"
	manager "github.com/gojue/ebpfmanager"
)

type IEventListener interface {
	SendRecord(rec perf.Record)
	OnEvent(int, []byte, *manager.PerfMap, *manager.Manager)
}

type BreakPoint struct {
	Addr      *controller.Address
	Enable    bool
	Deleted   bool
	Hardware  bool
	Temporary bool
	Pid       uint32
	Type      int
}

type BreakPointManager struct {
	process             *controller.Process
	BreakPoints         []*BreakPoint
	temporaryBreakPoint []*BreakPoint
	ProbeHandler        *ProbeHandler
	TempBreakTid        uint32
	Running             bool
	TargetLibName       string
	pendingHWBreaks     []*controller.Address
	waitingForLoad      bool
	FlowMode            bool
	FlowEntryAbs        uint64
}

func CreateBreakPointManager(listener IEventListener, BTF_File string, process *controller.Process) *BreakPointManager {
	return &BreakPointManager{
		process:      process,
		ProbeHandler: CreateProbeHandler(listener, BTF_File),
		Running:      false,
	}
}

func (this *BreakPointManager) SetProcess(process *controller.Process) {
	this.process = process
}

func (this *BreakPointManager) Reset() {
	this.BreakPoints = nil
	this.temporaryBreakPoint = nil
	this.TempBreakTid = 0
	this.Running = false
}

func checkOffset(offset uint64) bool {
	return offset%4 == 0
}

func (this *BreakPointManager) SetTempBreak(address *controller.Address, tid uint32) error {
	if checkOffset(address.Offset) == false {
		return fmt.Errorf("Invalid address: %x", address.Offset)
	}
	for _, brk := range this.BreakPoints {
		if controller.Equals(brk.Addr, address) && brk.Enable == true {
			return nil
		}
	}

	brk := &BreakPoint{
		Addr:      address,
		Enable:    true,
		Deleted:   false,
		Temporary: true,
		Pid:       tid,
		Type:      config.HW_BREAKPOINT_X,
	}

	switch config.Preference {
	case config.ALL_UPROBE, config.PREFER_UPROBE:
		brk.Hardware = false
		if address.IsAnouymous() {
			return fmt.Errorf("Failed: Anouymous address using uprobe.")
		}
	case config.ALL_PERF:
		brk.Hardware = true
	case config.PREFER_PERF:
		safe, err := utils.SafeAddress(this.process.WorkPid, address.Absolute)
		if err != nil {
			fmt.Printf("Failed parse current addr: %v\n", address.Absolute, err)
			brk.Hardware = false
			break
		}
		if !safe {
			brk.Hardware = true
		} else {
			brk.Hardware = false
		}
		if address.IsAnouymous() {
			brk.Hardware = true
		}
	}

	this.TempBreakTid = tid
	this.temporaryBreakPoint = append(this.temporaryBreakPoint, brk)
	return nil
}

func (this *BreakPointManager) CreateBreakPoint(address *controller.Address, enable bool) error {
	offset := address.Offset
	if checkOffset(offset) == false {
		return fmt.Errorf("Invalid address: %x", offset)
	}
	if address.IsAnouymous() {
		return fmt.Errorf("Anouymous address: %x, use hbreak.", offset)
	}
	for _, brk := range this.BreakPoints {
		if !brk.Deleted && controller.Equals(address, brk.Addr) {
			// fmt.Println("What?")
			if brk.Enable != enable {
				brk.Enable = enable
			} else {
				// return fmt.Errorf("BreakPoint %x exsists")
			}
			return nil
		}
	}
	brk := &BreakPoint{
		Addr:     address,
		Hardware: false,
		Enable:   enable,
		Deleted:  false,
		Pid:      this.process.WorkPid,
	}
	this.BreakPoints = append(this.BreakPoints, brk)
	return nil
}

func (this *BreakPointManager) CreateHWBreakPoint(address *controller.Address, enable bool, Type int) error {
	Count := 0
	for _, brk := range this.BreakPoints {
		if !brk.Deleted && controller.Equals(address, brk.Addr) {
			if brk.Enable != enable {
				brk.Enable = enable
			} else {
				// return fmt.Errorf("BreakPoint %x exsists")
			}
			return nil
		}
		if !brk.Deleted && brk.Hardware == true {
			Count++
		}
	}
	if Count >= config.Available_HW-2 {
		return fmt.Errorf("Hardware Breakpoint count limit exceed. Delete some hardware breakpoints or use uprobe.")
	}
	brk := &BreakPoint{
		Addr:     address,
		Hardware: true,
		Enable:   enable,
		Deleted:  false,
		Pid:      this.process.WorkPid,
		Type:     Type,
	}
	this.BreakPoints = append(this.BreakPoints, brk)
	return nil
}

func (this *BreakPointManager) ClearTempBreak() {
	this.temporaryBreakPoint = []*BreakPoint{}
}

func (this *BreakPointManager) SetupProbe() error {
	if this.Running {
		return fmt.Errorf("Probes are running now.")
	}
	if len(this.temporaryBreakPoint) == 0 {
		this.TempBreakTid = 0
	}
	brks := this.BreakPoints
	if config.FlowTracing && this.FlowEntryAbs != 0 {
		if len(this.temporaryBreakPoint) > 0 {
			this.ProbeHandler.CloseHWBreakAtAddress(this.FlowEntryAbs)
		}
		brks = make([]*BreakPoint, 0, len(this.BreakPoints))
		for _, brk := range this.BreakPoints {
			if brk.Hardware && !brk.Deleted {
				if len(this.temporaryBreakPoint) > 0 || brk.Addr.Absolute != this.FlowEntryAbs {
					continue
				}
			}
			brks = append(brks, brk)
		}
	}
	err := this.ProbeHandler.SetupManager(append(this.temporaryBreakPoint, brks...))
	if err != nil {
		return err
	}
	this.ClearTempBreak()
	err = this.ProbeHandler.Run()
	// fmt.Println("probe is running..")
	if err != nil {
		return err
	}
	this.Running = true
	return nil
}
func (this *BreakPointManager) Init() error {
	return this.ProbeHandler.SetupManagerOptions()
}

func (this *BreakPointManager) Start(addresss []*controller.Address) error {
	if config.Preference == config.ALL_PERF && len(addresss) > 0 {
		this.process.UpdatePidList()
		if len(this.process.PidList) > 0 {
			this.process.WorkPid = this.process.PidList[0]
		}
		config.Debugf("Start: Preference=ALL_PERF, WorkPid=%d, PidList=%v", this.process.WorkPid, this.process.PidList)
		needWait := false
		for _, addr := range addresss {
			absAddr, err := this.process.GetAbsoluteAddress(addr)
			if err != nil {
				config.Debugf("Start: GetAbsoluteAddress(%s+0x%x) failed: %v -> entering linker-wait", addr.LibInfo.LibName, addr.Offset, err)
				needWait = true
				break
			}
			config.Debugf("Start: GetAbsoluteAddress(%s+0x%x) = 0x%x", addr.LibInfo.LibName, addr.Offset, absAddr)
		}
		if needWait {
			return this.startLinkerCtorWait(addresss)
		}
	}

	if this.FlowMode {
		return nil
	}

	for _, addr := range addresss {
		var err error
		if config.Preference == config.ALL_PERF {
			err = this.CreateHWBreakPoint(addr, true, config.HW_BREAKPOINT_X)
		} else {
			err = this.CreateBreakPoint(addr, true)
		}
		if err != nil {
			fmt.Printf("Create Breakpoints Failed: %v, skipped.\n", err)
			continue
		}
	}
	return this.SetupProbe()
}

func (this *BreakPointManager) startLinkerCtorWait(addresses []*controller.Address) error {
	config.Debugf("startLinkerCtorWait: %d pending addresses, TargetLibName=%q", len(addresses), this.TargetLibName)
	this.pendingHWBreaks = addresses
	this.waitingForLoad = true
	config.WaitCtor = true

	linkerInfo, err := controller.ResolveLinkerInfo(this.process)
	if err != nil {
		return fmt.Errorf("linker-wait: %v", err)
	}
	config.Debugf("startLinkerCtorWait: linkerPath=%s ctorOffset=0x%x sonameOffset=%d",
		linkerInfo.Path, linkerInfo.CtorSymbolOffset, linkerInfo.SonameFieldOffset)

	targetUID := config.TargetUID
	if targetUID == 0 && this.process != nil && this.process.PackageName != "" {
		packageInfos := utils.GetPackageInfos()
		pkgInfo, err := packageInfos.FindPackageByName(this.process.PackageName)
		if err == nil {
			targetUID = pkgInfo.Uid
		}
		config.Debugf("startLinkerCtorWait: resolved UID from package: %d (err=%v)", targetUID, err)
	}
	config.Debugf("startLinkerCtorWait: targetUID=%d filterLib=%q", targetUID, this.TargetLibName)

	return this.ProbeHandler.SetupLinkerProbe(
		linkerInfo.Path,
		linkerInfo.CtorSymbolOffset,
		this.TargetLibName,
		linkerInfo.SonameFieldOffset,
		targetUID,
	)
}

func (this *BreakPointManager) OnLinkerCtorHit() error {
	config.Debugf("OnLinkerCtorHit: entered, waitingForLoad=%v", this.waitingForLoad)
	if !this.waitingForLoad {
		return nil
	}
	this.waitingForLoad = false
	config.WaitCtor = false

	if err := this.ProbeHandler.StopLinkerProbe(); err != nil {
		fmt.Printf("Warning: failed to stop linker probe: %v\n", err)
	}
	config.Debugf("OnLinkerCtorHit: linker probe stopped, updating pid list & maps")

	this.process.UpdatePidList()
	config.Debugf("OnLinkerCtorHit: PidList=%v WorkPid=%d", this.process.PidList, this.process.WorkPid)
	this.process.UpdateMaps()

	if this.FlowMode {
		config.Debugf("OnLinkerCtorHit: FlowMode, skipping breakpoint setup")
		this.pendingHWBreaks = nil
		return nil
	}

	for _, addr := range this.pendingHWBreaks {
		config.Debugf("OnLinkerCtorHit: resolving %s+0x%x", addr.LibInfo.LibName, addr.Offset)
		absAddr, err := this.process.GetAbsoluteAddress(addr)
		if err != nil {
			fmt.Printf("Failed to resolve address %s+0x%x: %v\n", addr.LibInfo.LibName, addr.Offset, err)
			continue
		}
		config.Debugf("OnLinkerCtorHit: resolved to 0x%x", absAddr)
		addr.Absolute = absAddr
		err = this.CreateHWBreakPoint(addr, true, config.HW_BREAKPOINT_X)
		if err != nil {
			fmt.Printf("Failed to create HW breakpoint at 0x%x: %v\n", absAddr, err)
			continue
		}
		fmt.Printf("HW breakpoint set at 0x%x (%s+0x%x)\n", absAddr, addr.LibInfo.LibName, addr.Offset)
	}

	this.pendingHWBreaks = nil
	config.Debugf("OnLinkerCtorHit: calling SetupProbe")
	return this.SetupProbe()
}

func (this *BreakPointManager) IsWaitingForLoad() bool {
	return this.waitingForLoad
}

func (this *BreakPointManager) Stop() error {
	this.ProbeHandler.StopLinkerProbe()
	err := this.ProbeHandler.Stop()
	if err == nil {
		this.Running = false
	}
	return err
}

func (this *BreakPointManager) StopAll() error {
	this.ProbeHandler.StopLinkerProbe()
	err := this.ProbeHandler.StopAll()
	if err == nil {
		this.Running = false
	}
	return err
}

func (this *BreakPointManager) PrintBreakPoints() {
	for id, brk := range this.BreakPoints {
		if brk.Deleted {
			continue
		}
		if !brk.Enable {
			fmt.Printf("[-] ")
		} else {
			fmt.Printf("[+] ")
		}
		if brk.Hardware {
			fmt.Printf("%d: %x Hardware\n", id, brk.Addr.Absolute)
		} else {
			fmt.Printf("%d: %s+%x\n", id, brk.Addr.LibInfo.LibName, brk.Addr.Offset)
		}
	}
}

func (this *BreakPointManager) ChangeBreakPoint(id int, status bool) {
	if id >= len(this.BreakPoints) {
		fmt.Println("Breakpoint doesn't exist.")
		return
	}
	if this.BreakPoints[id].Deleted {
		fmt.Println("Breakpoint doesn't exist.")
		return
	}
	this.BreakPoints[id].Enable = status
}

func (this *BreakPointManager) DeleteBreakPoint(id int) {
	if id >= len(this.BreakPoints) {
		fmt.Println("Breakpoint doesn't exist.")
		return
	}
	if this.BreakPoints[id].Deleted {
		fmt.Println("Breakpoint doesn't exist.")
		return
	}
	this.BreakPoints[id].Enable = false
	this.BreakPoints[id].Deleted = true
}
