package controller

import (
	"eDBG/utils"
	"errors"
	"fmt"
	"io/ioutil"
	"strings"

	"golang.org/x/exp/slices"
)



type Segment struct {
    baseAddr uint64
    off      uint64
    endAddr  uint64
    libPath  string
    libName  string
    permission string
}

type ProcMaps struct {
	pid uint32
	segments []*Segment
}

func (this *Segment) ParseLib() {
    parts := strings.Split(this.libPath, "/")
    this.libName = parts[len(parts)-1]
}

func GetProcMaps(pid uint32) (*ProcMaps, error) {
	procMaps := &ProcMaps{}
	procMaps.pid = pid
	err := procMaps.ReadMaps()
	if err != nil {
		return &ProcMaps{}, err
	}
    return procMaps, nil
}

func (this *Process) UpdateMapsPid(pid uint32) error {
	procMaps, err := GetProcMaps(pid)
	if err == nil {
		this.ProcMaps[pid] = procMaps
		this.MapsUpToDate[pid] = true
		return nil
	}
	return err
}

func (this *Process) UpdateMaps() {
	for _, pid := range this.PidList {
		procMaps, err := GetProcMaps(pid)
		if err == nil {
			this.ProcMaps[pid] = procMaps
		}
	}
}

func (this *Process) GetCurrentMaps() (*ProcMaps, error) {
    if val, ok := this.MapsUpToDate[this.WorkPid]; !ok || !val {
        err := this.UpdateMapsPid(this.WorkPid)
        if err != nil {
            return &ProcMaps{}, fmt.Errorf("Failed to update maps: %v", err)
        }
    }
	maps, exsists := this.ProcMaps[this.WorkPid]
	if exsists == false {
		return &ProcMaps{}, fmt.Errorf("GetMaps: Bad pid: %x", this.WorkPid)
	}
    return maps, nil
}

func (this *ProcMaps) ReadMaps() error {
	if this.pid == 0 {
		return errors.New("Unexpected: pid is not ready")
	}
	filename := fmt.Sprintf("/proc/%d/maps", this.pid)
    content, err := ioutil.ReadFile(filename)
    if err != nil {
        return fmt.Errorf("Error when opening file:%v", err)
    }
    return this.ParseMapsContent(content)
}

func (this *ProcMaps) ParseMapsContent(content []byte) error {
	var (
        seg_start  uint64
        seg_end    uint64
        permission string
        seg_offset uint64
        device     string
        inode      uint64
        seg_path   string
    )
	this.segments = []*Segment{}

    for _, line := range strings.Split(string(content), "\n") {
        reader := strings.NewReader(line)
        n, err := fmt.Fscanf(reader, "%x-%x %s %x %s %d %s", &seg_start, &seg_end, &permission, &seg_offset, &device, &inode, &seg_path)
        if err == nil && n == 7 {
            if seg_path == "" {
                seg_path = fmt.Sprintf("UNNAMED_0x%x", seg_start)
            }
            new_info := &Segment{
                baseAddr: seg_start,
                off:      seg_offset,
                endAddr:  seg_end,
                libPath:  seg_path,
                permission: permission,
            }
            new_info.ParseLib()
			this.segments = append(this.segments, new_info)
        }
    }
    return nil
}

func (this *ProcMaps) FindStackAndTls(tid uint32) (start, end uint64, name string, err error) {
	want := fmt.Sprintf("[anon:stack_and_tls:%d]", tid)
	for _, seg := range this.segments {
		if seg.libPath == want {
			return seg.baseAddr, seg.endAddr, want, nil
		}
	}
	return 0, 0, "", fmt.Errorf("no %s in maps for pid %d", want, this.pid)
}

func (this *ProcMaps) Regions() []utils.MapRegion {
	regions := make([]utils.MapRegion, 0, len(this.segments))
	for _, seg := range this.segments {
		regions = append(regions, utils.MapRegion{
			Start: seg.baseAddr,
			End:   seg.endAddr,
			Perm:  seg.permission,
			Path:  seg.libPath,
		})
	}
	return regions
}

func (this *ProcMaps) GetLibSearchPaths() []string {
	search_paths := []string{}
	for _, libInfo := range this.segments {
		if strings.HasPrefix(libInfo.libPath, "/") && strings.HasSuffix(libInfo.libPath, ".so") {
            items := strings.Split(libInfo.libPath, "/")
            lib_search_path := strings.Join(items[:len(items)-1], "/")
            if !slices.Contains(search_paths, lib_search_path) {
                search_paths = append(search_paths, lib_search_path)
            }
        }
	}
	return search_paths
}