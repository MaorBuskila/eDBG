package utils

import (
    "fmt"
    "golang.org/x/arch/arm64/arm64asm"
)

type IContext interface {
	GetReg(int) uint64
	GetPC() uint64
	GetLR() uint64
	GetSP() uint64
	GetPstate() uint64
}

type IProcess interface {
    GetSymbol(uint64) string
}

func DisASM(code []byte, PC uint64, process IProcess) (string, error) {
	inst, err := arm64asm.Decode(code)
	if err != nil {
		return "", err
	}
    if PC == 0 {
        return inst.String(), nil
    }
    switch inst.Op {
    case arm64asm.B, arm64asm.BL:
        cond := getCondition(inst)
        if cond == "AL" {
            cond = ""
        } else {
            cond = "."+cond   
        }
		if target := getBranchTarget(inst, PC); target != 0 {
            return fmt.Sprintf("%s%s %x%s", inst.Op.String(), cond, target, process.GetSymbol(target)), nil
        }
	case arm64asm.CBZ, arm64asm.CBNZ:
		if _, target := getCBZTarget(inst, PC); target != 0 {
            return fmt.Sprintf("%s %s, %x%s", inst.Op.String(), inst.Args[0].String(), target, process.GetSymbol(target)), nil
        }
	case arm64asm.TBZ, arm64asm.TBNZ:
		if _, _, target := getTBZTarget(inst, PC); target != 0 {
            return fmt.Sprintf("%s %s, %s, %x%s", inst.Op.String(), inst.Args[0].String(), inst.Args[1].String(), target, process.GetSymbol(target)), nil
        }
    }
	return inst.String(), nil
}

func getCondition(inst arm64asm.Inst) string {
    if inst.Op != arm64asm.B {
        return "AL"
    }
    for _, arg := range inst.Args {
        if cond, ok := arg.(arm64asm.Cond); ok {
            return cond.String()
        }
    }
    return "AL"
}

func SafeAddress(pid uint32, PC uint64) (bool, error) {
    asm := make([]byte, 4)
	n, err := ReadProcessMemory(pid, uintptr(PC), asm)
    if n < 4 || err != nil {
		return true, fmt.Errorf("Failed to read instruction: %v", err)
	}

    inst, err := arm64asm.Decode(asm)
    if err != nil {
        return true, fmt.Errorf("Failed to disassemble instruction: %v", err)
    }
    switch inst.Op {
	case arm64asm.RET, arm64asm.BR, arm64asm.B, arm64asm.BL, arm64asm.BLR, arm64asm.CBZ, arm64asm.CBNZ, arm64asm.TBZ, arm64asm.TBNZ:
        return true, nil
    }
    return false, nil
}
func GetTarget(pid uint32, ctx IContext) (uintptr, error) {
    PC := ctx.GetPC()
    asm := make([]byte, 4)
	n, err := ReadProcessMemory(pid, uintptr(PC), asm)
	if n < 4 || err != nil {
		return uintptr(PC + 4), fmt.Errorf("GetTarget: Failed to read instruction: %v", err)
	}

    // 1. 解码ARM64指令
    inst, err := arm64asm.Decode(asm)
    if err != nil {
        return uintptr(PC + 4), fmt.Errorf("GetTarget: Failed to disassemble instruction: %v", err)
    }

    return uintptr(getBranchTarget(inst, PC)), nil
}
// overLevel controls flow/step prediction:
//
//	0 = into (follow all transfers)
//	1 = over calls (BL/BLR → PC+4)
//	2 = over branches (all branch ops except RET → PC+4)
//	3 = over out-of-lib (follow only if inLib(target); RET always followed)
//
// inLib may be nil when overLevel < 3.
func PredictNextPC(pid uint32, ctx IContext, overLevel int, inLib func(uint64) bool) (uintptr, error) {
	PC := ctx.GetPC()
	pstate := ctx.GetPstate()
	asm := make([]byte, 4)
	n, err := ReadProcessMemory(pid, uintptr(PC), asm)
	if n < 4 || err != nil {
		return uintptr(PC + 4), fmt.Errorf("PredictNextPC: Failed to read instruction: %v", err)
	}

	inst, err := arm64asm.Decode(asm)
	if err != nil {
		return uintptr(PC + 4), fmt.Errorf("PredictNextPC: Failed to disassemble instruction: %v", err)
	}

	fallthroughPC := uintptr(PC + 4)
	pick := func(op arm64asm.Op, target uint64, isRet bool) uintptr {
		if ShouldOverFallthrough(overLevel, op, isRet, target, inLib) {
			return fallthroughPC
		}
		return uintptr(target)
	}

	switch inst.Op {
	case arm64asm.RET:
		if reg, ok := inst.Args[0].(arm64asm.Reg); ok {
			return pick(inst.Op, ctx.GetReg(int(reg)), true), nil
		}
		return fallthroughPC, nil
	case arm64asm.BR:
		if reg, ok := inst.Args[0].(arm64asm.Reg); ok {
			return pick(inst.Op, ctx.GetReg(int(reg)), false), nil
		}
		return fallthroughPC, nil
	case arm64asm.B:
		cond := getCondition(inst)
		if cond != "AL" && pstate == 0xFFFFFFFF {
			return 0xDEADBEEF, fmt.Errorf("PredictNextPC: Missing pstate")
		}
		if conditionMet(cond, pstate) {
			if target := getBranchTarget(inst, PC); target != 0 {
				return pick(inst.Op, target, false), nil
			}
			return fallthroughPC, fmt.Errorf("PredictNextPC: Failed to get B target")
		}
		return fallthroughPC, nil
	case arm64asm.BL:
		if target := getBranchTarget(inst, PC); target != 0 {
			return pick(inst.Op, target, false), nil
		}
		return fallthroughPC, fmt.Errorf("PredictNextPC: Failed to get BL target")
	case arm64asm.BLR:
		if reg, ok := inst.Args[0].(arm64asm.Reg); ok {
			return pick(inst.Op, ctx.GetReg(int(reg)), false), nil
		}
		return fallthroughPC, fmt.Errorf("PredictNextPC: Failed to get BLR target")
	case arm64asm.CBZ, arm64asm.CBNZ:
		if reg, target := getCBZTarget(inst, PC); target != 0 {
			value := ctx.GetReg(reg)
			if (inst.Op == arm64asm.CBZ && value == 0) ||
				(inst.Op == arm64asm.CBNZ && value != 0) {
				return pick(inst.Op, target, false), nil
			}
		}
		return fallthroughPC, nil
	case arm64asm.TBZ, arm64asm.TBNZ:
		if reg, bit, target := getTBZTarget(inst, PC); target != 0 {
			value := ctx.GetReg(reg)
			bitVal := (value >> bit) & 1
			if (inst.Op == arm64asm.TBZ && bitVal == 0) ||
				(inst.Op == arm64asm.TBNZ && bitVal != 0) {
				return pick(inst.Op, target, false), nil
			}
		}
		return fallthroughPC, nil
	}
	return fallthroughPC, nil
}

// ShouldOverFallthrough reports whether prediction should land at PC+4 instead of target.
func ShouldOverFallthrough(overLevel int, op arm64asm.Op, isRet bool, target uint64, inLib func(uint64) bool) bool {
	if overLevel <= 0 || isRet {
		return false
	}
	switch overLevel {
	case 1:
		return op == arm64asm.BL || op == arm64asm.BLR
	case 2:
		return true
	case 3:
		if inLib == nil {
			return op == arm64asm.BL || op == arm64asm.BLR
		}
		return !inLib(target)
	default:
		return op == arm64asm.BL || op == arm64asm.BLR
	}
}

func getCBZTarget(inst arm64asm.Inst, PC uint64) (reg int, target uint64) {
    if len(inst.Args) < 2 {
        return 0, 0
    }
    regArg, ok1 := inst.Args[0].(arm64asm.Reg)
    pcrelArg, ok2 := inst.Args[1].(arm64asm.PCRel)
    if !ok1 || !ok2 {
        return 0, 0
    }
    return int(regArg), PC + uint64(int64(pcrelArg))
}

// 获取 TBZ/TBNZ 的寄存器、测试位和目标地址
func getTBZTarget(inst arm64asm.Inst, PC uint64) (reg int, bit uint64, target uint64) {
    if len(inst.Args) < 3 {
        return 0, 0, 0
    }
    regArg, ok1 := inst.Args[0].(arm64asm.Reg)
    bitArg, ok2 := inst.Args[1].(arm64asm.Imm)
    pcrelArg, ok3 := inst.Args[2].(arm64asm.PCRel)
    if !ok1 || !ok2 || !ok3 {
        return 0, 0, 0
    }
    return int(regArg), uint64(bitArg.Imm), PC + uint64(int64(pcrelArg))
}

func getBranchTarget(inst arm64asm.Inst, PC uint64) uint64 {
    for _, arg := range inst.Args {
        if pcrel, ok := arg.(arm64asm.PCRel); ok {
            return PC + uint64(int64(pcrel))
        }
    }
	fmt.Println("Failed to get branch target.")
    return 0
}


// conditionMet 判断条件码是否满足
func conditionMet(cond string, pstate uint64) bool {
    // 提取NZCV标志位
    n := (pstate >> 31) & 1
    z := (pstate >> 30) & 1
    c := (pstate >> 29) & 1
    v := (pstate >> 28) & 1
	// fmt.Printf("Condition: N%x Z%x C%x V%x\n", n, z, c, v)
    switch cond {
	case "AL": return true
    case "EQ":  return z == 1
    case "NE":  return z == 0
    case "HS": return c == 1
    case "LO": return c == 0
    case "MI":  return n == 1
    case "PL":  return n == 0
    case "VS": return v == 1
    case "VC": return v == 0
    case "HI": return c == 1 && z == 0
    case "LS": return c == 0 || z == 1
    case "GE": return n == v
    case "LT": return n != v
    case "GT": return z == 0 && n == v
    case "LE": return z == 1 || n != v
    default: return false
    }
}