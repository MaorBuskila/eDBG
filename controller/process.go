package controller

import (
	"path"
	"syscall"
	"fmt"
	"os"
	"strings"
	"eDBG/utils"
	"golang.org/x/exp/slices"
	"strconv"
	"eDBG/config"
)



type Process struct {
	PidList []uint32
	PackageName string
	ExecPath string
	ProcMaps map[uint32]*ProcMaps
	StoppedPid []uint32
	Context *ProcessContext
	WorkPid uint32
	WorkTid uint32
	Threads map[uint32][]*Thread
	MapsUpToDate map[uint32]bool
	ThreadsUpToDate map[uint32]bool
	Symbols map[uint64]string
}

func CreateProcess(packageName string) (*Process, error) {
	process := &Process{}
	process.ProcMaps = make(map[uint32]*ProcMaps)
	process.MapsUpToDate = make(map[uint32]bool)
	process.ThreadsUpToDate = make(map[uint32]bool)
	process.Threads = make(map[uint32][]*Thread)
	process.Symbols = make(map[uint64]string)
	process.PackageName = packageName
	process.Context = &ProcessContext{}
	process.WorkPid = 0
	err := process.GetExecPath()
	if err != nil {
		return &Process{}, err
	}
	err = process.checkPackageName()
	if err != nil {
		return &Process{}, err
	}
	return process, nil
}

func (this *Process) GetExecPath() error {
	exec_path, err := os.Executable()
    if err != nil {
        return fmt.Errorf("please build as executable binary, %v", err)
    }
	this.ExecPath = path.Dir(exec_path)
	return nil
}

func (this *Process) UpdatePidList() {
	this.PidList = []uint32{}
	selfPid := uint32(os.Getpid())
	var content string
	var err error
	if config.TargetUID != 0 {
		cmd := fmt.Sprintf("ps -A -o uid,pid,name | awk '$1==%d {print $3,$2}'", config.TargetUID)
		config.Debugf("UpdatePidList: running: %s", cmd)
		content, err = utils.RunCommand("sh", "-c", cmd)
	} else {
		content, err = utils.RunCommand("sh", "-c", "ps -ef -o name,pid,ppid | grep ^"+this.PackageName)
	}
	config.Debugf("UpdatePidList: content=%q err=%v", content, err)
	if err != nil {
		return
	}
	lines := strings.Split(content, "\n")
	for _, line := range lines {
		parts := strings.Fields(line)
		if len(parts) < 2 {
			continue
		}
		value, err := strconv.ParseUint(parts[1], 10, 32)
		if err != nil {
			continue
		}
		pid := uint32(value)
		if pid == selfPid {
			continue
		}
		this.PidList = append(this.PidList, pid)
	}
}


func (this *Process) checkPackageName() error {
	if config.DisablePackageCheck {
		return nil
	}
	packageinfos := utils.GetPackageInfos()
	_, err := packageinfos.FindPackageByName(this.PackageName)
	return err
}

func (this *Process) GetLibSearchPaths() []string {
	SearchPath := []string{
        "/system/lib64",
        "/apex/com.android.art/lib64",
        "/apex/com.android.conscrypt/lib64",
        "/apex/com.android.runtime/bin",
        "/apex/com.android.runtime/lib64/bionic",
    }
	if this.PackageName == "" {
		return SearchPath
	}
	this.UpdatePidList()
	this.UpdateMaps()
	for _, mapsInfo := range this.ProcMaps {
		mapsPaths := mapsInfo.GetLibSearchPaths()
		for _, paths := range mapsPaths {
			if !slices.Contains(SearchPath, paths) {
				SearchPath = append(SearchPath, paths)
			}
		}
	}

	pkgPaths := FindLibPathFromPackage(this.PackageName)
	for _, path := range pkgPaths {
		if !slices.Contains(SearchPath, path) {
			SearchPath = append(SearchPath, path)
		}
	}
	return SearchPath
}

func FindLibPathFromPackage(name string) []string {
	SearchPath := []string{}
    content, err := utils.RunCommand("pm", "path", name)
    if err != nil {
        return SearchPath
    }
    for _, line := range strings.Split(content, "\n") {
        parts := strings.Split(line, ":")
        if len(parts) == 2 {
            // 将 apk 文件也作为搜索路径
            apk_path := parts[1]
            _, err := os.Stat(apk_path)
            if err == nil {
                SearchPath = append(SearchPath, apk_path)
            }
            // 将 apk + /lib/arm64 作为搜索路径
            items := strings.Split(parts[1], "/")
            lib_search_path := strings.Join(items[:len(items)-1], "/") + "/lib/arm64"
            _, err = os.Stat(lib_search_path)
            if err == nil {
                SearchPath = append(SearchPath, lib_search_path)
            }
        }
    }
	return SearchPath
}

func (this *Process) Continue() error {
	Continued := make(map[uint32]bool)
	for _, pid := range this.StoppedPid {
		if val, ok := Continued[pid]; !ok || !val {
			// fmt.Printf("Continued pid: %d\n", int(pid))
			this.MapsUpToDate[pid] = false
			this.ThreadsUpToDate[pid] = false
			err := syscall.Kill(int(pid), syscall.SIGCONT)
			if err != nil {
				if err == syscall.ESRCH {
					return fmt.Errorf("No such process -> %d\n", pid)
				} else {
					return fmt.Errorf("Process continue error:%v\n", pid)
				}
			}
			Continued[pid] = true
		}
    }
	if len(this.StoppedPid) > 0 {
		this.StoppedPid = []uint32{}
		this.WorkPid = 0
	}
	return nil
}

func (this *Process) StoppedPID(pid uint32) {
	// fmt.Println("Really Stopped: ", pid)
	this.StoppedPid = append(this.StoppedPid, pid)
}

func (this *Process) Stop() error {
	this.UpdatePidList()
	for _, pid := range this.PidList {
        err := syscall.Kill(int(pid), syscall.SIGSTOP)
		this.StoppedPid = append(this.StoppedPid, pid)
        if err != nil {
            if err == syscall.ESRCH {
                return fmt.Errorf("No such process -> %d\n", pid)
            } else {
                return fmt.Errorf("Process Stop error:%v\n", pid)
            }
        }
    }
	return nil
}

