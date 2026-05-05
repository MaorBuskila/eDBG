package module

import (
	"bytes"
	"eDBG/assets"
	"eDBG/config"
	"eDBG/utils"
	"fmt"
	"math"
	"path/filepath"

	"github.com/cilium/ebpf"
	"github.com/cilium/ebpf/btf"
	manager "github.com/gojue/ebpfmanager"
	"golang.org/x/sys/unix"
)

type ProbeHandler struct {
	bpfManager        *manager.Manager
	linkerManager     *manager.Manager
    bpfManagerOptions manager.Options
    listener          IEventListener
    BTF_File          string
    Perfs             []*PerfBreaks
}

func CreateProbeHandler(listener IEventListener, BTF_File string) *ProbeHandler {
    return &ProbeHandler{
        listener: listener, 
        BTF_File: BTF_File, 
    }
}

func (this *ProbeHandler) SetupManagerOptions() error {
    // 对于没有开启 CONFIG_DEBUG_INFO_BTF 的加载额外的 btf.Spec
    if this.BTF_File != "" {
        byteBuf, err := assets.Asset("assets/" + this.BTF_File)
        if err != nil {
            return fmt.Errorf("SetupManagerOptions failed, err:%v", err)
        }
        spec, err := btf.LoadSpecFromReader((bytes.NewReader(byteBuf)))
        if err != nil {
            return fmt.Errorf("SetupManagerOptions failed, err:%v", err)
        }
        this.bpfManagerOptions = manager.Options{
            DefaultKProbeMaxActive: 512,
            VerifierOptions: ebpf.CollectionOptions{
                Programs: ebpf.ProgramOptions{
                    LogSize:     2097152,
                    KernelTypes: spec,
                },
            },
            RLimit: &unix.Rlimit{
                Cur: math.MaxUint64,
                Max: math.MaxUint64,
            },
        }
    } else {
        this.bpfManagerOptions = manager.Options{
            DefaultKProbeMaxActive: 512,
            VerifierOptions: ebpf.CollectionOptions{
                Programs: ebpf.ProgramOptions{
                    LogSize:     2097152,
                },
            },
            RLimit: &unix.Rlimit{
                Cur: math.MaxUint64,
                Max: math.MaxUint64,
            },
        }
    }
    return nil
}

func (this *ProbeHandler) SetupManager(brks []*BreakPoint) error {
    perf := false
    this.Perfs = []*PerfBreaks{}
    probes := []*manager.Probe{}
    usedCount := 0
    for i, brk := range brks {
        if !brk.Enable || brk.Deleted {
            continue
        }
        if brk.Hardware {
            this.AddHWBreak(brk.Pid, brk.Addr.Absolute, brk.Type)
            perf = true
            continue
        }
        var probe *manager.Probe
        usedCount++
        if usedCount > 20 {
            return fmt.Errorf("setupManager: Failed to Set Breakpoint: %x. Breakpoint count exceed 20.", brk.Addr.Absolute)
        }
        sym := utils.RandStringBytes(8)
        probe = &manager.Probe{
            Section:          fmt.Sprintf("uprobe/probe_%d", i),
            EbpfFuncName:     fmt.Sprintf("probe_%d", i),
            AttachToFuncName: sym,
            RealFilePath:     brk.Addr.LibInfo.RealFilePath,
            BinaryPath:       brk.Addr.LibInfo.LibPath,
            NonElfOffset:     brk.Addr.LibInfo.NonElfOffset,
            UAddress: brk.Addr.Offset,
            UprobeOffset: 0,
        }
        // fmt.Printf("Set uprobe: %s[%x]+%x\n", brk.Addr.LibInfo.RealFilePath, brk.Addr.LibInfo.NonElfOffset, brk.Addr.Offset)
        probes = append(probes, probe)
    }
    

    if len(probes) == 0 {
        fmt.Println("WARNING: No valid uprobe breakpoints set.")
        // return fmt.Errorf("No valid reakpoints")
    }

    this.bpfManager = &manager.Manager{
        Probes: probes,
        PerfMaps: []*manager.PerfMap{
            &manager.PerfMap{
                Map: manager.Map{
                    Name: "events",
                },
                PerfMapOptions: manager.PerfMapOptions{
                    DataHandler: this.listener.OnEvent,
                },
            },
        },
    }
    if perf {
        this.bpfManager.Probes = append(this.bpfManager.Probes,
            &manager.Probe{
                Section: "kprobe/perf_output_sample",
            	EbpfFuncName: "probe_perf",
                AttachToFuncName: "perf_output_sample",
            })
        this.bpfManager.Maps = []*manager.Map{
            &manager.Map{
                Name: "brk_events",
            },
        }
    } 
    return nil
}

func (this *ProbeHandler) Run() error {
    var bpfFileName = filepath.Join("assets", "ebpf_module.o")
    byteBuf, err := assets.Asset(bpfFileName)

    if err != nil {
        return fmt.Errorf("ProbeHandler.Run(): couldn't find asset %v .", err)
    }

    if err = this.bpfManager.InitWithOptions(bytes.NewReader(byteBuf), this.bpfManagerOptions); err != nil {
        return fmt.Errorf("ProbeHandler.Run(): couldn't init manager %v", err)
    }

    if err = this.bpfManager.Start(); err != nil {
        return fmt.Errorf("ProbeHandler.Run(): couldn't start bootstrap manager %v .", err)
    }
    if config.HitOnly {
        if err = this.setHitOnlyConfig(); err != nil {
            return fmt.Errorf("Failed to set hit-only config: %v", err)
        }
    }
    if err = this.SetHWBreakInternel(); err != nil {
        return fmt.Errorf("Failed to set up Hardware breakpoint: %v", err)
    }
    return nil
}

func (this *ProbeHandler) setHitOnlyConfig() error {
    em, found, err := this.bpfManager.GetMap("config_map")
    if !found {
        return fmt.Errorf("config_map not found")
    }
    if err != nil {
        return fmt.Errorf("get config_map failed: %v", err)
    }
    key := uint32(0)
    val := uint32(1)
    return em.Put(key, val)
}

func (this *ProbeHandler) Stop() error {
    this.CloseHWBreak()
    if this.bpfManager == nil {
        return nil
    }
    return this.bpfManager.Stop(manager.CleanAll)
}

func (this *ProbeHandler) SetupLinkerProbe(linkerPath string, ctorOffset uint64, filterLib string, sonameOffset uint64, targetUID uint32) error {
    config.Debugf("SetupLinkerProbe: linkerPath=%s ctorOffset=0x%x filterLib=%q sonameOffset=%d targetUID=%d",
        linkerPath, ctorOffset, filterLib, sonameOffset, targetUID)

    sym := utils.RandStringBytes(8)
    probe := &manager.Probe{
        Section:          "uprobe/probe_linker",
        EbpfFuncName:     "probe_linker",
        AttachToFuncName: sym,
        RealFilePath:     linkerPath,
        BinaryPath:       linkerPath,
        UAddress:         ctorOffset,
        UprobeOffset:     0,
    }

    this.linkerManager = &manager.Manager{
        Probes: []*manager.Probe{probe},
        PerfMaps: []*manager.PerfMap{
            {
                Map: manager.Map{Name: "events"},
                PerfMapOptions: manager.PerfMapOptions{
                    DataHandler: this.listener.OnEvent,
                },
            },
        },
    }

    var bpfFileName = filepath.Join("assets", "ebpf_module.o")
    byteBuf, err := assets.Asset(bpfFileName)
    if err != nil {
        return fmt.Errorf("SetupLinkerProbe: asset load failed: %v", err)
    }

    if err = this.linkerManager.InitWithOptions(bytes.NewReader(byteBuf), this.bpfManagerOptions); err != nil {
        return fmt.Errorf("SetupLinkerProbe: init failed: %v", err)
    }
    config.Debugf("SetupLinkerProbe: linkerManager initialized OK")

    type linkerFilterT struct {
        Str [256]byte
        Len uint32
    }
    em, found, err := this.linkerManager.GetMap("linker_filter")
    if !found || err != nil {
        return fmt.Errorf("SetupLinkerProbe: linker_filter map not found")
    }
    filter := linkerFilterT{}
    copy(filter.Str[:], filterLib)
    filter.Len = uint32(len(filterLib))
    if err = em.Put(uint32(0), filter); err != nil {
        return fmt.Errorf("SetupLinkerProbe: failed to set linker_filter: %v", err)
    }
    config.Debugf("SetupLinkerProbe: linker_filter set: str=%q len=%d", filterLib, filter.Len)

    type linkerConfigT struct {
        SonameOffset uint64
    }
    em2, found, err := this.linkerManager.GetMap("linker_config")
    if !found || err != nil {
        return fmt.Errorf("SetupLinkerProbe: linker_config map not found")
    }
    if err = em2.Put(uint32(0), linkerConfigT{SonameOffset: sonameOffset}); err != nil {
        return fmt.Errorf("SetupLinkerProbe: failed to set linker_config: %v", err)
    }
    config.Debugf("SetupLinkerProbe: linker_config set: sonameOffset=%d", sonameOffset)

    em3, found, err := this.linkerManager.GetMap("uid_filter")
    if !found || err != nil {
        return fmt.Errorf("SetupLinkerProbe: uid_filter map not found")
    }
    if err = em3.Put(targetUID, uint32(1)); err != nil {
        return fmt.Errorf("SetupLinkerProbe: failed to set uid_filter: %v", err)
    }
    config.Debugf("SetupLinkerProbe: uid_filter set: uid=%d", targetUID)

    if err = this.linkerManager.Start(); err != nil {
        return fmt.Errorf("SetupLinkerProbe: start failed: %v", err)
    }
    config.Debugf("SetupLinkerProbe: linkerManager started OK")

    fmt.Printf("Linker ctor probe active: waiting for %s to load...\n", filterLib)
    return nil
}

func (this *ProbeHandler) StopLinkerProbe() error {
    if this.linkerManager == nil {
        return nil
    }
    err := this.linkerManager.Stop(manager.CleanAll)
    this.linkerManager = nil
    return err
}

