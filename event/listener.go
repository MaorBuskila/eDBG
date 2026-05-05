package event

import (
	"eDBG/cli"
	"eDBG/config"
	"eDBG/controller"
	"encoding/binary"
	"fmt"
	"strings"
	"syscall"
	"unsafe"

	"github.com/cilium/ebpf/perf"
	manager "github.com/gojue/ebpfmanager"
)

type EventListener struct {
	pid           uint32
	client        *cli.Client
	process       *controller.Process
	ByteOrder     binary.ByteOrder
	Incomingdata  chan []byte
	EventData     chan []byte
	Record        chan perf.Record
	WaitingEvents int
}

func CreateEventListener(process *controller.Process) *EventListener {
	return &EventListener{
		process:      process,
		ByteOrder:    getHostByteOrder(),
		Incomingdata: make(chan []byte, 512),
		EventData:    make(chan []byte, 512),
		Record:       make(chan perf.Record, 1),
	}
}

func (this *EventListener) SendRecord(rec perf.Record) {
	this.Record <- rec
}

func (this *EventListener) SetupClient(client *cli.Client) {
	this.client = client
}

func getHostByteOrder() binary.ByteOrder {
	var i int32 = 0x01020304
	u := unsafe.Pointer(&i)
	pb := (*byte)(u)
	b := *pb
	if b == 0x04 {
		return binary.LittleEndian
	}
	return binary.BigEndian
}

func (this *EventListener) parseContext(data []byte) *controller.ProcessContext {
	bo := this.ByteOrder
	context := &controller.ProcessContext{}
	for i := 12; i < 12+8*30; i += 8 {
		context.Regs = append(context.Regs, bo.Uint64(data[i:i+8]))
	}
	context.LR = bo.Uint64(data[12+8*30 : 12+8*31])
	context.SP = bo.Uint64(data[12+8*31 : 12+8*32])
	context.PC = bo.Uint64(data[12+8*32 : 12+8*33])
	if len(data) >= 284 {
		// 硬件断点无法采样 pstate
		context.Pstate = bo.Uint64(data[12+8*33 : 12+8*34])
	} else {
		context.Pstate = 0xFFFFFFFF
	}
	return context
}

func (this *EventListener) Workdata(data []byte) {
	if this.client == nil || this.client.Process == nil {
		return
	}
	<-this.client.Done
	this.process.Context = this.parseContext(data)
	this.client.RecordBreakpointHit()
	this.client.Incoming <- true
}

func (this *EventListener) hitOnlyDispatch(data []byte) {
	this.process.Context = this.parseContext(data)
	this.client.HitOnlyOutput()
	this.client.Working = false
	this.client.NotifyContinue <- true
}

func (this *EventListener) dispatchEvent(data []byte, PC uint64) {
	if config.HitOnly {
		if PC == 0xFFFFFFFF {
			dataRaw := <-this.Record
			this.hitOnlyDispatch(dataRaw.RawSample[12:])
		} else {
			this.hitOnlyDispatch(data)
		}
		return
	}
	if PC == 0xFFFFFFFF {
		dataRaw := <-this.Record
		this.Incomingdata <- dataRaw.RawSample[12:]
	} else {
		this.Incomingdata <- data
	}
	this.client.DoClean <- true
}

func (this *EventListener) Run() {
	go func() {
		for {
			data := <-this.Incomingdata
			this.Workdata(data)
		}
	}()
	go func() {
		for {
			data := <-this.EventData
			this.WorkEvent(data)
			<-this.client.NotifyContinue
			// this.client.Time = time.Now()
			if this.WaitingEvents > 0 {
				this.WaitingEvents -= 1
			}
			// fmt.Println("Event End.")
			// fmt.Println(this.WaitingEvents)
			if this.WaitingEvents == 0 {
				// fmt.Println("OK, Continue.")
				if this.client != nil && this.client.Process != nil {
					this.client.Process.Continue()
				}
			}
		}
	}()
}

func (this *EventListener) OnEvent(cpu int, data []byte, perfmap *manager.PerfMap, manager *manager.Manager) {
	this.WaitingEvents += 1 // 还是有触发竞争的可能，但是每次加锁效率有点低，感觉高并发场景需求没那么高
	// fmt.Println("OnEvent: ", this.ByteOrder.Uint32(data[4:8]))
	this.EventData <- data
}
func (this *EventListener) PassEvent(IsHardware bool) {
	// fmt.Println("PASSED EVENT")
	if IsHardware {
		<-this.Record // 舍弃这个 Sample
	}
	this.client.Working = false
	this.client.NotifyContinue <- true
}
func (this *EventListener) WorkEvent(data []byte) {
	if this.client == nil || this.client.Process == nil {
		return
	}
	process := this.client.Process
	// fmt.Println("Event: ", this.ByteOrder.Uint32(data[4:8]))
	for {
		if !this.client.Working {
			break
		}
	}
	this.client.Working = true
	process.UpdatePidList()
	bo := this.ByteOrder
	this.pid = bo.Uint32(data[4:8])
	nowTid := bo.Uint32(data[12+8*34 : 16+8*34])
	PC := bo.Uint64(data[12+8*32 : 12+8*33])

	config.Debugf("WorkEvent: pid=%d tid=%d PC=0x%x waitingForLoad=%v", this.pid, nowTid, PC, this.client.BrkManager.IsWaitingForLoad())

	if PC == 0xFFFFFFFD {
		libBytes := data[12 : 12+56]
		end := 0
		for i, b := range libBytes {
			if b == 0 {
				end = i
				break
			}
			if i == len(libBytes)-1 {
				end = len(libBytes)
			}
		}
		libStr := string(libBytes[:end])
		dataPtr := bo.Uint64(data[12+8*28 : 12+8*29])
		strSize := bo.Uint64(data[12+8*29 : 12+8*30])
		soinfoPtr := bo.Uint64(data[12+8*30 : 12+8*31])
		config.Debugf("LinkerLib: pid=%d soinfo=0x%x data_ptr=0x%x str_size=%d str=%q",
			this.pid, soinfoPtr, dataPtr, strSize, libStr)
		this.client.Working = false
		this.client.NotifyContinue <- true
		return
	}

	if PC == config.LinkerCtorSentinelPC && this.client.BrkManager.IsWaitingForLoad() {
		config.Debugf("WorkEvent: linker ctor sentinel matched! pid=%d", this.pid)
		process.WorkPid = this.pid
		if err := this.client.BrkManager.OnLinkerCtorHit(); err != nil {
			fmt.Printf("Linker ctor hit error: %v\n", err)
		}
		syscall.Kill(int(this.pid), syscall.SIGCONT)
		this.client.Working = false
		this.client.NotifyContinue <- true
		return
	}

	for _, ablepid := range process.PidList {
		if this.pid == ablepid {
			process.WorkPid = this.pid
			if !config.HitOnly {
				process.StoppedPID(this.pid)
			}
			if this.client.BrkManager.TempBreakTid != 0 {
				// 临时断点判断线程 ID
				if PC == 0xFFFFFFFF {
					if nowTid == this.client.BrkManager.TempBreakTid {
						process.WorkTid = nowTid
						this.dispatchEvent(data, PC)
						return
					}
					this.PassEvent(PC == 0xFFFFFFFF)
					return
				}
			}

			valid := false
			for _, t := range this.client.Config.ThreadFilters {
				if !t.Enable {
					continue
				}
				if t.Thread.Tid != 0 {
					valid = true
					if nowTid == t.Thread.Tid {
						process.WorkTid = nowTid
						this.dispatchEvent(data, PC)
						return
					}
					continue
				}
				if t.Thread.Name != "" {
					tList, err := process.GetCurrentThreads()
					if err != nil {
						fmt.Printf("WARNING: Failed to get threads: %v. Filters on thread name not working.\n", err)
						continue
					}
					found := false
					for _, tInfo := range tList {
						if strings.Contains(t.Thread.Name, tInfo.Name) {
							valid = true
							found = true
							if tInfo.Tid == nowTid {
								process.WorkTid = nowTid
								if !config.HitOnly {
									process.StoppedPID(this.pid)
								}
								this.dispatchEvent(data, PC)
								return
							}
						}
					}
					if !found {
						fmt.Printf("WARNING: No thread Named %s\n", t.Thread.Name)
						continue
					}
				}
			}
			if !valid {
				// 没有可用的线程过滤器，按照 pid 工作
				process.WorkTid = nowTid
				if !config.HitOnly {
					process.StoppedPID(this.pid)
				}
				this.dispatchEvent(data, PC)
				return
			}
			this.PassEvent(PC == 0xFFFFFFFF)
			return
		}
	}
	this.PassEvent(PC == 0xFFFFFFFF)
	if !config.HitOnly {
		syscall.Kill(int(this.pid), syscall.SIGCONT)
	}
}
