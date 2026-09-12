package engine

// CROM v3: magic 'CROM' + version u8 + mem_size u32 + flags u8 + crc32 u32
//        + 2B reserved + payload (zlib 默认压缩, 与 Python zlib level 6 兼容)

import (
	"bytes"
	"compress/zlib"
	"encoding/binary"
	"hash/crc32"
	"io"
)

const cromVersion = 3

// cromMaxTrailer 是载荷允许超出头部 mem_size 的最大余量 (MMU 页表尾部元数据)。
// 必须与 Python 侧 codecin/crom.py: CROM_MAX_TRAILER 保持一致。
const cromMaxTrailer = 4 << 20

// CromPack 打包内存镜像为 CROM 字节流 (compress=true 时 zlib 压缩)。
func CromPack(data []byte, compress bool) []byte {
	var payload []byte
	flags := byte(0)
	if compress {
		flags = 0x01
		var buf bytes.Buffer
		// zlib.NewWriter 使用默认压缩级别 (flate.DefaultCompression = 6)
		w := zlib.NewWriter(&buf)
		_, _ = w.Write(data)
		_ = w.Close()
		payload = buf.Bytes()
	} else {
		payload = data
	}

	out := make([]byte, 16+len(payload))
	copy(out[0:4], "CROM")
	out[4] = cromVersion
	binary.LittleEndian.PutUint32(out[5:9], uint32(len(data)))
	out[9] = flags
	binary.LittleEndian.PutUint32(out[10:14], crc32.ChecksumIEEE(payload))
	// [14:16] reserved 0
	copy(out[16:], payload)
	return out
}

// CromUnpack 解包 CROM, 返回 (raw, flags, ok)。
func CromUnpack(data []byte) ([]byte, int, bool) {
	if len(data) < 16 || string(data[0:4]) != "CROM" {
		return nil, 0, false
	}
	if data[4] != cromVersion {
		return nil, 0, false
	}
	memSize := binary.LittleEndian.Uint32(data[5:9])
	flags := data[9]
	checksum := binary.LittleEndian.Uint32(data[10:14])
	payload := data[16:]
	if crc32.ChecksumIEEE(payload) != checksum {
		return nil, 0, false
	}

	var raw []byte
	if flags&0x01 != 0 {
		r, err := zlib.NewReader(bytes.NewReader(payload))
		if err != nil {
			return nil, 0, false
		}
		// 解压上限取自文件头声明的 mem_size + MMU 尾部余量: 防止 zip bomb
		// (旧实现 io.ReadAll 无上限, 65KB 的合法 CROM 可解出 64MB+)。
		limit := int64(memSize) + cromMaxTrailer
		limited := io.LimitReader(r, limit+1)
		raw, err = io.ReadAll(limited)
		_ = r.Close()
		if err != nil {
			return nil, 0, false
		}
	} else {
		raw = payload
	}
	// mem_size 是物理内存长度; 之后允许跟一段 MMU 页表尾部元数据
	if uint32(len(raw)) < memSize || len(raw) > int(memSize)+cromMaxTrailer {
		return nil, 0, false
	}
	return raw, int(flags), true
}
