package aot

import (
	_ "embed"
	"encoding/binary"
	"fmt"
	"io"
	"os"

	"codecin-native/engine"
)

//go:embed stub_main.go.txt
var stubSource string

// StubSource 返回生成的 main.go 源码。
func StubSource() string { return stubSource }

const maxSteps = 100000000

// 段文件 (program.segs) 布局, 由 aot.py 生成:
//   seg_count u32
//   mem_size u64  sp u64  heap_base u64
//   每段: addr u64 + len u32 + data (初始内存段, 通常只有数据段/零头)
// 程序内存本身由 VM 按 mem_size 分配 (1 GiB 默认, OS 懒提交)。

// Main 运行内嵌的字节码并返回进程退出码 (0 = 正常结束, 1 = 运行期错误)。
//
// 标准输入只在被重定向 (管道/文件) 时读取, 交互式终端下不会挂起等待 EOF。
// AOT 产物里 arg_count()/arg(i) 直接取本进程 os.Args[1:]。
func Main(bytecode, segFile []byte) int {
	segs, memSize, sp, heapBase, err := parseSegFile(segFile)
	if err != nil {
		fmt.Fprintf(os.Stderr, "runtime error: %s\n", err)
		return 1
	}

	engine.SetProgramArgs(os.Args[1:])

	var in []byte
	if fi, err := os.Stdin.Stat(); err == nil &&
		fi.Mode()&os.ModeCharDevice == 0 {
		if data, err := io.ReadAll(os.Stdin); err == nil {
			in = data
		}
	}

	res := engine.RunV2(&engine.RunOptions{
		BC:       bytecode,
		Segments: segs,
		Entry:    0,
		SP:       int64(sp),
		HeapBase: int64(heapBase),
		MemSize:  int64(memSize),
		Input:    in,
		MaxSteps: maxSteps,
	})
	if res == nil {
		fmt.Fprintln(os.Stderr, "runtime error: no result")
		return 1
	}
	engine.ConsoleInit()
	fmt.Print(res.Output)
	if res.Status == engine.StatusError ||
		res.Status == engine.StatusUnsupported {
		msg := res.ErrMsg
		if msg == "" {
			msg = "unsupported instruction (原生 VM 未实现该指令)"
		}
		fmt.Fprintf(os.Stderr, "runtime error: %s\n", msg)
		return 1
	}
	return 0
}

// parseSegFile 解析内嵌的段文件。
func parseSegFile(data []byte) ([]engine.MemSeg, uint64, uint64, uint64, error) {
	if len(data) < 4 {
		return nil, 0, 0, 0, fmt.Errorf("malformed segment file")
	}
	pos := 0
	segCount := int(binary.LittleEndian.Uint32(data[pos:]))
	pos += 4
	if len(data) < pos+24 {
		return nil, 0, 0, 0, fmt.Errorf("malformed segment file")
	}
	memSize := binary.LittleEndian.Uint64(data[pos:])
	sp := binary.LittleEndian.Uint64(data[pos+8:])
	heapBase := binary.LittleEndian.Uint64(data[pos+16:])
	pos += 24
	var segs []engine.MemSeg
	for i := 0; i < segCount; i++ {
		if len(data) < pos+12 {
			return nil, 0, 0, 0, fmt.Errorf("malformed segment file")
		}
		addr := binary.LittleEndian.Uint64(data[pos:])
		dataLen := int(binary.LittleEndian.Uint32(data[pos+8:]))
		pos += 12
		if len(data) < pos+dataLen {
			return nil, 0, 0, 0, fmt.Errorf("malformed segment file")
		}
		if dataLen > 0 {
			segs = append(segs, engine.MemSeg{
				Addr: addr, Data: data[pos : pos+dataLen],
			})
		}
		pos += dataLen
	}
	return segs, memSize, sp, heapBase, nil
}
