package controller

import (
	"archive/zip"
	"eDBG/config"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
)

type Address struct {
	LibInfo    *LibraryInfo
	Offset     uint64
	Permission string
	Absolute   uint64
}

type CachedLibInfo struct {
	LibInfo    *LibraryInfo
	BaseAddr   uint64
	Offset     uint64
	EndAddr    uint64
	Permission string
}

var DoneLib []*CachedLibInfo

func NewAddress(libInfo *LibraryInfo, offset uint64) *Address {
	return &Address{
		LibInfo:    libInfo,
		Offset:     offset,
		Permission: "rwxp",
	}
}
func (this *Address) IsAnouymous() bool {
	return this.LibInfo.LibName == "UNNAMED"
}
func Equals(a *Address, b *Address) bool {
	if !a.IsAnouymous() && a.LibInfo.LibName == b.LibInfo.LibName && a.Offset == b.Offset {
		return true
	}
	if a.Absolute == b.Absolute && a.Absolute != 0 {
		return true
	}
	return false
}

func (this *Process) ParseAddress(address uint64) (*Address, error) {
	for _, lib := range DoneLib {
		if lib.BaseAddr <= address && address < lib.EndAddr {
			return &Address{
				Absolute:   address,
				LibInfo:    lib.LibInfo,
				Offset:     address - lib.BaseAddr + lib.Offset,
				Permission: lib.Permission,
			}, nil
		}
	}
	return this.ParseAddressNew(address)
}

func (this *Process) ParseIdaAddress(address uint64) (*Address, error) {
	for _, lib := range DoneLib {
		if lib.BaseAddr <= address && address < lib.EndAddr {
			return &Address{
				Absolute:   address,
				LibInfo:    lib.LibInfo,
				Offset:     address - lib.BaseAddr,
				Permission: lib.Permission,
			}, nil
		}
	}
	return this.ParseAddressNew(address)
}

func (this *Process) ParseAddressNew(address uint64) (*Address, error) {
	maps, err := this.GetCurrentMaps()
	if err != nil {
		return &Address{Absolute: address}, err
	}
	addressParsed, err := maps.ParseAbsoluteAddress(this, address)
	if err != nil {
		return &Address{Absolute: address}, err
	}
	addressParsed.Absolute = address
	return addressParsed, nil
}

func (this *ProcMaps) ParseAbsoluteAddress(process *Process, address uint64) (*Address, error) {
	for _, seg := range this.segments {
		if seg.baseAddr <= address && address < seg.endAddr {
			if strings.HasPrefix(seg.libPath, "UNNAMED") || strings.HasPrefix(seg.libPath, "[") {
				return &Address{LibInfo: &LibraryInfo{LibName: "UNNAMED"}}, nil // 忽略匿名地址
			}

			if strings.HasSuffix(seg.libName, ".apk") {
				apk_path := seg.libPath
				lib := &LibraryInfo{Process: process, RealFilePath: apk_path}
				off := address - seg.baseAddr + seg.off
				zf, err := zip.OpenReader(apk_path)
				if err != nil {
					return &Address{}, err
				}
				for _, f := range zf.File {
					if strings.HasPrefix(f.Name, "lib/arm64-v8a") {
						offset, err := f.DataOffset()
						if err != nil {
							return &Address{}, err
						}
						if uint64(offset) <= off && off < uint64(offset)+f.UncompressedSize64 {
							parts := strings.Split(f.Name, "/")
							lib.LibName = parts[len(parts)-1]
							lib.LibPath = filepath.Join(process.ExecPath, lib.LibName)
							lib.NonElfOffset = uint64(offset)
							if _, err := os.Stat(lib.LibPath); err != nil {
								srcFile, err := f.Open()
								if err != nil {
									return &Address{}, err
								}
								dstFile, err := os.OpenFile(lib.LibPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, f.Mode())
								if err != nil {
									panic(err)
								}
								if _, err := io.Copy(dstFile, srcFile); err != nil {
									panic(err)
								}
								dstFile.Close()
								srcFile.Close()
							}

							DoneLib = append(DoneLib, &CachedLibInfo{
								LibInfo:    lib,
								BaseAddr:   seg.baseAddr + uint64(offset) - seg.off,
								EndAddr:    seg.baseAddr + uint64(offset) + f.UncompressedSize64 - seg.off,
								Offset:     0,
								Permission: seg.permission,
							})

							return &Address{
								LibInfo:    lib,
								Offset:     off - uint64(offset),
								Permission: seg.permission,
							}, nil
						}

					}
				}

			} else {
				res := &Address{
					LibInfo: &LibraryInfo{
						LibName: seg.libName,
						LibPath: seg.libPath,
						Process: process,
					},
					Offset:     address - seg.baseAddr + seg.off,
					Permission: seg.permission,
				}
				err := res.LibInfo.ParseLibrary()
				if err != nil {
					return &Address{}, err
				}
				DoneLib = append(DoneLib, &CachedLibInfo{
					LibInfo:    res.LibInfo,
					BaseAddr:   seg.baseAddr,
					EndAddr:    seg.endAddr,
					Offset:     seg.off,
					Permission: seg.permission,
				})
				return res, nil
			}
		}
	}
	return &Address{LibInfo: &LibraryInfo{LibName: "UNNAMED"}}, nil
}

func (this *ProcMaps) GetAbsoluteAddressNew(address *Address) (uint64, error) {
	libInfo := address.LibInfo
	for _, seg := range this.segments {
		if seg.libName == libInfo.LibName && seg.off == 0 {
			absAddr := seg.baseAddr + address.Offset
			config.Debugf("GetAbsoluteAddressNew: loadBase=0x%x + offset=0x%x = 0x%x", seg.baseAddr, address.Offset, absAddr)
			return absAddr, nil
		}
		if strings.HasSuffix(seg.libName, ".apk") {
			apk_path := seg.libPath
			zf, err := zip.OpenReader(apk_path)
			if err != nil {
				continue
			}
			for _, f := range zf.File {
				if strings.HasPrefix(f.Name, "lib/arm64-v8a") {
					parts := strings.Split(f.Name, "/")
					libName := parts[len(parts)-1]
					if libInfo.LibName == libName && address.Offset < f.UncompressedSize64 {
						offset, err := f.DataOffset()
						if err != nil {
							return 0, fmt.Errorf("Cannot get library offset: %v.", err)
						}
						return seg.baseAddr + uint64(offset) - seg.off, nil
					}
				}
			}
		}
	}
	return 0, fmt.Errorf("Cannot find address %s+%x", libInfo.LibName, address.Offset)
}

func (this *Process) GetAbsoluteAddress(address *Address) (uint64, error) {
	if address.Absolute != 0 {
		return address.Absolute, nil
	}
	libInfo := address.LibInfo
	for _, lib := range DoneLib {
		if lib.LibInfo.LibName == libInfo.LibName && lib.Offset == 0 {
			absAddr := lib.BaseAddr + address.Offset
			config.Debugf("GetAbsoluteAddress: DoneLib loadBase=0x%x + offset=0x%x = 0x%x", lib.BaseAddr, address.Offset, absAddr)
			return absAddr, nil
		}
	}
	maps, err := this.GetCurrentMaps()
	if err != nil {
		return 0, fmt.Errorf("Cannot get current maps")
	}
	address.Absolute, err = maps.GetAbsoluteAddressNew(address)
	if err != nil {
		address.Absolute = 0
	}
	return address.Absolute, err
}
