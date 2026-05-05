package module

import (
	"eDBG/config"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"

	"github.com/cilium/ebpf"
	"github.com/cilium/ebpf/perf"
)

type PerfBreaks struct {
	Fds       []*perf.Reader
	Pid       uint32
	Addr      uint64
	Type      int
	Temporary bool
	Live      bool
	listener  IEventListener
}

func (this *PerfBreaks) startReader(rd *perf.Reader, eopt *perf.ExtraPerfOptions) {
	go func() {
		for {
			record, err := rd.ReadWithExtraOptions(eopt)
			if err != nil {
				if errors.Is(err, perf.ErrClosed) {
					return
				}
				fmt.Println("Got record Failed: ", err)
			}
			this.listener.SendRecord(record)
		}
	}()
}

func (this *PerfBreaks) RunPerfBreak(em *ebpf.Map) error {
	if this.Live {
		config.Debugf("RunPerfBreak: addr=0x%x already live, skipping", this.Addr)
		return nil
	}

	address := this.Addr
	buf := os.Getpagesize() * (1 * 1024 / 4)

	if this.Temporary {
		config.Debugf("RunPerfBreak: addr=0x%x temp mode, single TID=%d", address, this.Pid)
		eopt := perf.ExtraPerfOptions{
			ShowRegs:         true,
			BrkPid:           int(this.Pid),
			BrkAddr:          address,
			BrkLen:           4,
			BrkType:          uint32(this.Type),
			Sample_regs_user: (1 << 33) - 1,
		}
		rd, err := perf.NewReaderWithOptions(em, buf, perf.ReaderOptions{}, eopt)
		if err != nil {
			return fmt.Errorf("Setup perf event failed (temp tid=%d): %v", this.Pid, err)
		}
		this.Fds = []*perf.Reader{rd}
		this.startReader(rd, &eopt)
	} else if config.GlobalHWBreak {
		config.Debugf("RunPerfBreak: addr=0x%x using global mode (pid=-1)", address)
		eopt := perf.ExtraPerfOptions{
			ShowRegs:         true,
			BrkPid:           -1,
			BrkAddr:          address,
			BrkLen:           4,
			BrkType:          uint32(this.Type),
			Sample_regs_user: (1 << 33) - 1,
		}
		rd, err := perf.NewReaderWithOptions(em, buf, perf.ReaderOptions{}, eopt)
		if err != nil {
			return fmt.Errorf("Setup perf event failed (global): %v", err)
		}
		this.Fds = []*perf.Reader{rd}
		this.startReader(rd, &eopt)
	} else {
		tids, err := enumThreads(this.Pid)
		if err != nil {
			config.Debugf("RunPerfBreak: enumThreads(%d) failed: %v, falling back to pid only", this.Pid, err)
			tids = []int{int(this.Pid)}
		}
		config.Debugf("RunPerfBreak: addr=0x%x inherit mode, %d TIDs: %v", address, len(tids), tids)

		for _, tid := range tids {
			eopt := perf.ExtraPerfOptions{
				ShowRegs:         true,
				Inherit:          true,
				BrkPid:           tid,
				BrkAddr:          address,
				BrkLen:           4,
				BrkType:          uint32(this.Type),
				Sample_regs_user: (1 << 33) - 1,
			}
			rd, err := perf.NewReaderWithOptions(em, buf, perf.ReaderOptions{}, eopt)
			if err != nil {
				config.Debugf("RunPerfBreak: perf_event_open for tid=%d addr=0x%x failed: %v", tid, address, err)
				continue
			}
			this.Fds = append(this.Fds, rd)
			this.startReader(rd, &eopt)
		}
		if len(this.Fds) == 0 {
			return fmt.Errorf("Setup perf event failed: no TIDs succeeded for addr 0x%x", address)
		}
	}

	this.Live = true
	return nil
}

func (this *PerfBreaks) Close() {
	if !this.Live {
		return
	}
	for _, fd := range this.Fds {
		fd.Close()
	}
	this.Fds = nil
	this.Live = false
}

func enumThreads(pid uint32) ([]int, error) {
	taskDir := filepath.Join("/proc", strconv.Itoa(int(pid)), "task")
	entries, err := os.ReadDir(taskDir)
	if err != nil {
		return nil, fmt.Errorf("read %s: %v", taskDir, err)
	}
	tids := make([]int, 0, len(entries))
	for _, e := range entries {
		tid, err := strconv.Atoi(e.Name())
		if err != nil {
			continue
		}
		tids = append(tids, tid)
	}
	return tids, nil
}

func (this *ProbeHandler) AddHWBreak(pid uint32, address uint64, tp int, temporary bool) error {
	for _, p := range this.Perfs {
		if p.Addr == address && p.Live {
			config.Debugf("AddHWBreak: addr=0x%x already live, reusing", address)
			return nil
		}
	}
	this.Perfs = append(this.Perfs, &PerfBreaks{
		Pid:       pid,
		Addr:      address,
		Type:      tp,
		Temporary: temporary,
		listener:  this.listener,
	})
	return nil
}

func (this *ProbeHandler) SetHWBreakInternel() error {
	em, found, err := this.bpfManager.GetMap("brk_events")
	if !found {
		return fmt.Errorf("map not found")
	}
	if err != nil {
		return fmt.Errorf("Get map failed: %v", err)
	}
	for _, p := range this.Perfs {
		err := p.RunPerfBreak(em)
		if err != nil {
			fmt.Printf("Failed to start breakpoint at %x: %v\n", p.Addr, err)
		}
	}
	return nil
}

func (this *ProbeHandler) CloseTempHWBreak() {
	kept := make([]*PerfBreaks, 0, len(this.Perfs))
	for _, p := range this.Perfs {
		if p.Temporary {
			p.Close()
		} else {
			kept = append(kept, p)
		}
	}
	this.Perfs = kept
}

func (this *ProbeHandler) CloseHWBreakAtAddress(addr uint64) {
	kept := make([]*PerfBreaks, 0, len(this.Perfs))
	for _, p := range this.Perfs {
		if p.Addr == addr {
			p.Close()
		} else {
			kept = append(kept, p)
		}
	}
	this.Perfs = kept
}

func (this *ProbeHandler) CloseAllHWBreak() {
	for _, p := range this.Perfs {
		p.Close()
	}
	this.Perfs = nil
}
