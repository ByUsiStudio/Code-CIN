package engine

import (
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// 网络能力 (Go 标准库 net/http; Windows/Linux/Android 通用)。
// 所有调用都带超时与大小上限, 失败时返回空串 / -1, 不 panic。
const (
	httpTimeout      = 15 * time.Second
	maxHTTPBodyBytes = 8 << 20   // 8 MiB: 单次 http_get/http_post 返回上限
	maxDownloadBytes = 256 << 20 // 256 MiB: download 落盘上限
)

var httpClient = &http.Client{Timeout: httpTimeout}

// readBody 读取响应体 (带上限); 非 2xx 状态码由调用方决定处理方式。
func readBody(resp *http.Response) (string, bool) {
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxHTTPBodyBytes))
	if err != nil {
		return "", false
	}
	return string(body), true
}

// httpGet 发起 GET 请求, 返回响应体 (失败为空串)。
func (vm *vmState) httpGet(url string) uint64 {
	resp, err := httpClient.Get(url)
	if err != nil {
		return vm.empty()
	}
	body, ok := readBody(resp)
	if !ok {
		return vm.empty()
	}
	return vm.hs(body)
}

// httpPost 发起 POST (text/plain; charset=utf-8), 返回响应体 (失败为空串)。
func (vm *vmState) httpPost(url, body string) uint64 {
	resp, err := httpClient.Post(url, "text/plain; charset=utf-8",
		strings.NewReader(body))
	if err != nil {
		return vm.empty()
	}
	out, ok := readBody(resp)
	if !ok {
		return vm.empty()
	}
	return vm.hs(out)
}

// download 下载 url 到本地文件; 0 成功 / -1 失败 (含非 2xx 状态码)。
func (vm *vmState) download(url, path string) uint64 {
	resp, err := httpClient.Get(url)
	if err != nil {
		return mask64
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return mask64
	}
	f, err := os.Create(path)
	if err != nil {
		return mask64
	}
	defer f.Close()
	if _, err := io.Copy(f, io.LimitReader(resp.Body, maxDownloadBytes)); err != nil {
		return mask64
	}
	return 0
}
