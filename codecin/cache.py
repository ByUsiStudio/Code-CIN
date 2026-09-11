from collections import OrderedDict
from typing import Any, Dict, List


class CacheLine:
    __slots__ = ('tag', 'valid', 'dirty')

    def __init__(self, tag: int = 0, valid: bool = False, dirty: bool = False):
        self.tag = tag
        self.valid = valid
        self.dirty = dirty


class Cache:
    def __init__(self, size: int = 64, assoc: int = 4, line_size: int = 16):
        self.size = max(assoc, size)
        self.assoc = assoc
        self.line_size = line_size
        self.num_sets = max(1, self.size // assoc)
        self._sets: List[OrderedDict] = [OrderedDict() for _ in range(self.num_sets)]
        self.hits = 0
        self.misses = 0
        self.writes = 0

    def _set_index(self, addr: int) -> int:
        return (addr // self.line_size) % self.num_sets

    def _tag(self, addr: int) -> int:
        return addr // (self.line_size * self.num_sets)

    def access(self, addr: int, is_write: bool = False) -> bool:
        s = self._sets[self._set_index(addr)]
        tag = self._tag(addr)
        if tag in s:
            s.move_to_end(tag)
            if is_write:
                s[tag] = True
                self.writes += 1
            self.hits += 1
            return True
        s[tag] = is_write
        s.move_to_end(tag)
        while len(s) > self.assoc:
            s.popitem(last=False)
        self.misses += 1
        return False

    def read(self, addr: int) -> bool:
        return self.access(addr, False)

    def write(self, addr: int) -> bool:
        return self.access(addr, True)

    def flush(self) -> None:
        for s in self._sets:
            s.clear()

    def warmup(self, instructions) -> None:
        for pc in range(len(instructions)):
            self.access(pc * self.line_size)

    def get_stats(self) -> Dict[str, Any]:
        total = self.hits + self.misses
        return {
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': self.hits / total if total > 0 else 0.0,
            'miss_rate': self.misses / total if total > 0 else 0.0,
            'total_accesses': total,
            'dirty_writes': self.writes,
        }
