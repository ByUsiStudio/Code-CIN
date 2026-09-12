"""cache 与 stats 单元测试。"""

from codecin.cache import Cache


def test_cache_hit_miss_counts():
    c = Cache(size=16, assoc=4, line_size=8)
    # 同一地址连续访问 -> 命中
    assert c.read(0x100) is False          # 首次缺失
    assert c.read(0x100) is True           # 命中
    assert c.hits == 1 and c.misses == 1


def test_cache_write_and_eviction_lru():
    c = Cache(size=8, assoc=2, line_size=4)
    # num_sets = 8//2 = 4; 选择同一 set (set 0) 的不同 tag:
    # addr//4 % 4 == 0  => addr 为 16 的倍数
    addrs = [0x0, 0x10, 0x20, 0x30]
    for a in addrs:
        c.read(a)
    # 关联度 2, 第 3 次开始逐出 LRU: 0x0 与 0x10 已被淘汰
    assert c.misses == 4
    assert c.hits == 0
    # 访问最早被逐出的 0x0 -> 再次缺失
    assert c.read(0x0) is False


def test_cache_warmup():
    c = Cache(size=16, assoc=4, line_size=8)
    c.warmup([None] * 4)
    assert c.hits == 0 and c.misses == 4


def test_cache_stats_rates():
    c = Cache(size=16, assoc=4, line_size=8)
    c.read(0x10)
    c.read(0x10)
    stats = c.get_stats()
    assert stats['total_accesses'] == 2
    assert stats['hit_rate'] == 0.5 and stats['miss_rate'] == 0.5
