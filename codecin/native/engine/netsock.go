package engine

// netsock.go: 完整的 TCP/UDP/DNS 网络系统调用 (SYS 147-157)。
//
// 句柄语义: fd 是从 1 开始递增的整数 (三种套接字共用计数), 0/-1 表示失败。
//   - tcp_dial(host, port)         -> fd / -1
//   - tcp_send(fd, buf, n)         -> 发送字节数 / -1
//   - tcp_recv(fd, buf, max)       -> 读取字节数 / 0 对端关闭 / -1 错误
//   - tcp_close(fd)                -> 0 成功 / -1
//   - tcp_listen(port)             -> 监听 fd / -1
//   - tcp_accept(lfd)              -> 连接 fd / -1
//   - udp_open(port)               -> fd / -1 (port=0 系统分配)
//   - udp_sendto(fd,host,port,buf,n) -> 发送字节数 / -1
//   - udp_recvfrom(fd,buf,max,srcbuf)-> 读取字节数 / -1 (srcbuf 写入 "ip:port")
//   - udp_close(fd)                -> 0 成功 / -1
//   - dns_lookup(host)             -> IP 字符串 (新堆串; 失败空串)

import (
	"io"
	"net"
	"strconv"
	"sync"
	"time"
)

const (
	netDialTimeout   = 10 * time.Second
	maxNetPayload    = 8 << 20 // 单次 send/recv 上限 8 MiB
	maxRecvBlockSize = 4 << 20 // recv 一次最多读 4 MiB
)

type sockRegistry struct {
	mu        sync.Mutex
	next      int
	conns     map[int]net.Conn
	listeners map[int]net.Listener
	udps      map[int]*net.UDPConn
}

var socks = &sockRegistry{
	next:      1,
	conns:     map[int]net.Conn{},
	listeners: map[int]net.Listener{},
	udps:      map[int]*net.UDPConn{},
}

func (r *sockRegistry) alloc() int {
	id := r.next
	r.next++
	return id
}

func (r *sockRegistry) getConn(fd uint64) net.Conn {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.conns[int(fd)]
}

func (r *sockRegistry) getListener(fd uint64) net.Listener {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.listeners[int(fd)]
}

func (r *sockRegistry) getUDP(fd uint64) *net.UDPConn {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.udps[int(fd)]
}

func failUint() uint64 { return mask64 } // -1

// resolveHost 把主机名解析为 IP (IP 字面量直接返回)。
// 解析结果偏好 IPv4 地址 —— Windows 上 "localhost" 常先返回 ::1,
// 对端只监听 IPv4 回环时会收不到包。
func resolveHost(host string) net.IP {
	if ip := net.ParseIP(host); ip != nil {
		return ip
	}
	addrs, err := net.LookupHost(host)
	if err != nil || len(addrs) == 0 {
		return nil
	}
	for _, a := range addrs {
		if ip := net.ParseIP(a); ip != nil && ip.To4() != nil {
			return ip
		}
	}
	return net.ParseIP(addrs[0])
}

func (vm *vmState) tcpDial(host string, port int) uint64 {
	if host == "" || port <= 0 || port > 65535 {
		return failUint()
	}
	d := net.Dialer{Timeout: netDialTimeout}
	conn, err := d.Dial("tcp", net.JoinHostPort(host, strconv.Itoa(port)))
	if err != nil {
		return failUint()
	}
	socks.mu.Lock()
	defer socks.mu.Unlock()
	id := socks.alloc()
	socks.conns[id] = conn
	return uint64(id)
}

func (vm *vmState) tcpSend(fd, buf uint64, n int) uint64 {
	if n < 0 || n > maxNetPayload {
		return failUint()
	}
	conn := socks.getConn(fd)
	if conn == nil {
		return failUint()
	}
	if n == 0 {
		return 0
	}
	if e := vm.checkAddr(buf, n); e != "" {
		return failUint()
	}
	data := make([]byte, n)
	copy(data, vm.mem[buf:buf+uint64(n)])
	written := 0
	for written < n {
		k, err := conn.Write(data[written:])
		written += k
		if err != nil {
			if written > 0 {
				return uint64(written)
			}
			return failUint()
		}
	}
	return uint64(written)
}

// tcpRecv 阻塞读取 (最多 max 字节); 对端正常关闭返回 0, 错误返回 -1。
func (vm *vmState) tcpRecv(fd, buf uint64, max int) (uint64, string) {
	if max < 0 || max > maxRecvBlockSize {
		return failUint(), ""
	}
	conn := socks.getConn(fd)
	if conn == nil || max == 0 {
		if conn == nil {
			return failUint(), ""
		}
		return 0, ""
	}
	if e := vm.checkAddr(buf, max); e != "" {
		return 0, "tcp_recv: " + e
	}
	tmp := make([]byte, max)
	n, err := conn.Read(tmp)
	if n > 0 {
		copy(vm.mem[buf:buf+uint64(n)], tmp[:n])
		vm.touch(buf, uint64(n))
		return uint64(n), ""
	}
	if err == io.EOF {
		return 0, ""
	}
	if err == nil {
		return 0, ""
	}
	return failUint(), ""
}

func (vm *vmState) tcpClose(fd uint64) uint64 {
	socks.mu.Lock()
	conn, ok := socks.conns[int(fd)]
	if ok {
		delete(socks.conns, int(fd))
	}
	socks.mu.Unlock()
	if !ok {
		return failUint()
	}
	if err := conn.Close(); err != nil {
		return failUint()
	}
	return 0
}

func (vm *vmState) tcpListen(port uint64) uint64 {
	if port == 0 || port > 65535 {
		return failUint()
	}
	ln, err := net.Listen("tcp", ":"+strconv.FormatUint(port, 10))
	if err != nil {
		return failUint()
	}
	socks.mu.Lock()
	defer socks.mu.Unlock()
	id := socks.alloc()
	socks.listeners[id] = ln
	return uint64(id)
}

func (vm *vmState) tcpAccept(lfd uint64) uint64 {
	ln := socks.getListener(lfd)
	if ln == nil {
		return failUint()
	}
	conn, err := ln.Accept()
	if err != nil {
		return failUint()
	}
	socks.mu.Lock()
	defer socks.mu.Unlock()
	id := socks.alloc()
	socks.conns[id] = conn
	return uint64(id)
}

func (vm *vmState) udpOpen(port uint64) uint64 {
	if port > 65535 {
		return failUint()
	}
	var addr *net.UDPAddr
	if port == 0 {
		addr = &net.UDPAddr{IP: nil} // 系统分配端口
	} else {
		addr = &net.UDPAddr{Port: int(port)}
	}
	conn, err := net.ListenUDP("udp", addr)
	if err != nil {
		return failUint()
	}
	socks.mu.Lock()
	defer socks.mu.Unlock()
	id := socks.alloc()
	socks.udps[id] = conn
	return uint64(id)
}

func (vm *vmState) udpSendTo(fd uint64, host string, port int, buf uint64, n int) uint64 {
	if host == "" || port <= 0 || port > 65535 || n < 0 || n > maxNetPayload {
		return failUint()
	}
	conn := socks.getUDP(fd)
	if conn == nil {
		return failUint()
	}
	// host 兼容 IP 字面量与主机名 (主机名走系统解析, 取第一个地址)
	ip := net.ParseIP(host)
	if ip == nil {
		addrs, err := net.LookupHost(host)
		if err != nil || len(addrs) == 0 {
			return failUint()
		}
		ip = net.ParseIP(addrs[0])
		if ip == nil {
			return failUint()
		}
	}
	if n == 0 {
		k, err := conn.WriteToUDP(nil, &net.UDPAddr{IP: ip, Port: port})
		if err != nil {
			return failUint()
		}
		return uint64(k)
	}
	if e := vm.checkAddr(buf, n); e != "" {
		return failUint()
	}
	data := make([]byte, n)
	copy(data, vm.mem[buf:buf+uint64(n)])
	k, err := conn.WriteToUDP(data, &net.UDPAddr{IP: ip, Port: port})
	if err != nil {
		return failUint()
	}
	return uint64(k)
}

func (vm *vmState) udpRecvFrom(fd, buf uint64, max int, srcInfo uint64) (uint64, string) {
	if max < 0 || max > maxRecvBlockSize {
		return failUint(), ""
	}
	conn := socks.getUDP(fd)
	if conn == nil {
		return failUint(), ""
	}
	if e := vm.checkAddr(buf, max); e != "" {
		return 0, "udp_recvfrom: " + e
	}
	tmp := make([]byte, max)
	n, raddr, err := conn.ReadFromUDP(tmp)
	if err != nil {
		return failUint(), ""
	}
	copy(vm.mem[buf:buf+uint64(n)], tmp[:n])
	vm.touch(buf, uint64(n))
	if srcInfo != 0 {
		if e := vm.writeString(srcInfo, raddr.String()); e != "" {
			return uint64(n), "" // 源地址写不进去不影响收包结果
		}
	}
	return uint64(n), ""
}

func (vm *vmState) udpClose(fd uint64) uint64 {
	socks.mu.Lock()
	conn, ok := socks.udps[int(fd)]
	if ok {
		delete(socks.udps, int(fd))
	}
	socks.mu.Unlock()
	if !ok {
		return failUint()
	}
	if err := conn.Close(); err != nil {
		return failUint()
	}
	return 0
}

// dnsLookup 解析主机名 -> 第一个 IPv4/IPv6 地址 (新堆字符串; 失败空串)。
func (vm *vmState) dnsLookup(host string) uint64 {
	if host == "" {
		return vm.empty()
	}
	addrs, err := net.LookupHost(host)
	if err != nil || len(addrs) == 0 {
		return vm.empty()
	}
	return vm.hs(addrs[0])
}
