package engine

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"strings"
)

// 编码与哈希 (纯标准库, Windows/Linux/Android 行为一致)。

// sha256Hex 返回 UTF-8 字节的 SHA-256 十六进制摘要 (小写)。
func (vm *vmState) sha256Hex(s string) uint64 {
	sum := sha256.Sum256([]byte(s))
	return vm.hs(hex.EncodeToString(sum[:]))
}

// base64Encode 对 UTF-8 字节做标准 Base64 编码 (带 '=' 填充)。
func (vm *vmState) base64Encode(s string) uint64 {
	return vm.hs(base64.StdEncoding.EncodeToString([]byte(s)))
}

// base64Decode 解码标准 Base64; 非法输入返回空串。
func (vm *vmState) base64Decode(s string) uint64 {
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(s))
	if err != nil {
		return vm.empty()
	}
	return vm.hs(string(raw))
}
