//go:build !windows

package engine

import (
	"os"
	"strconv"
	"strings"
)

func SystemMemoryKB() (uint64, uint64, bool) {
	data, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0, 0, false
	}
	var total, avail uint64
	for _, line := range strings.Split(string(data), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 2 {
			continue
		}
		value, err := strconv.ParseUint(fields[1], 10, 64)
		if err != nil {
			continue
		}
		switch fields[0] {
		case "MemTotal:":
			total = value
		case "MemAvailable:":
			avail = value
		}
	}
	if total == 0 {
		return 0, 0, false
	}
	return total, avail, true
}
