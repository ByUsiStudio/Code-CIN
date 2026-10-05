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
// 状态码同样记录到 lastHTTPSt (与 http_req 一致, 供 http_code 查询)。
func (vm *vmState) httpGet(url string) uint64 {
	vm.lastHTTPSt = -1
	resp, err := httpClient.Get(url)
	if err != nil {
		return vm.empty()
	}
	vm.lastHTTPSt = int64(resp.StatusCode)
	body, ok := readBody(resp)
	if !ok {
		return vm.empty()
	}
	return vm.hs(body)
}

// httpPost 发起 POST (text/plain; charset=utf-8), 返回响应体 (失败为空串)。
func (vm *vmState) httpPost(url, body string) uint64 {
	vm.lastHTTPSt = -1
	resp, err := httpClient.Post(url, "text/plain; charset=utf-8",
		strings.NewReader(body))
	if err != nil {
		return vm.empty()
	}
	vm.lastHTTPSt = int64(resp.StatusCode)
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

// httpReq 发起任意方法的 HTTP 请求 (SYS 145)。
// headers 为 '\n' 分隔的 "Key: Value" 行 (可空); body 可空。
// 返回响应体 (新堆字符串; 失败为空串), 状态码记录到 lastHTTPSt 供
// SYS 146 (http_code) 查询; 请求失败时状态码为 -1。
func (vm *vmState) httpReq(method, url, headers, body string) uint64 {
	vm.lastHTTPSt = -1
	if method == "" {
		method = http.MethodGet
	}
	var rd io.Reader
	if body != "" {
		rd = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, url, rd)
	if err != nil {
		return vm.empty()
	}
	for _, line := range strings.Split(headers, "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		k, v, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		k = strings.TrimSpace(k)
		v = strings.TrimSpace(v)
		if k == "" || strings.EqualFold(k, "Host") {
			continue
		}
		req.Header.Set(k, v)
	}
	resp, err := httpClient.Do(req)
	if err != nil {
		return vm.empty()
	}
	vm.lastHTTPSt = int64(resp.StatusCode)
	out, ok := readBody(resp)
	if !ok {
		return vm.empty()
	}
	return vm.hs(out)
}
