"""原生引擎执行统计回归测试。

v5.9.0 起程序由 Go 引擎一次性执行; 早期实现只回传 steps, 于是 --debug 的
"Instruction Cycle Profile" 永远是占位项 "?", 内存读写恒 0, 分支准确率
在没有任何分支时也显示 100%。这里锁定三点:

* 新 ABI 回传真实的逐操作码直方图 / 内存计数 / 分支计数;
* Python 侧据此生成热指令与周期分布, 不再出现 "?";
* 旧库 (只有 steps) 的降级输出必须显示 N/A, 而不是伪造数字。
"""

import pytest

from codecin.console import Console
from codecin.stats import Statistics
from tests.helpers import run_cin_source

SOURCE = """
function main() -> int {
    int a[8]
    int sum = 0
    for (int i = 0; i < 8; i++) {
        a[i] = i * i
        sum = sum + a[i]
    }
    println(sum)
    return 0
}
"""


def test_native_execution_stats_are_real():
    cpu = run_cin_source(SOURCE)
    if not cpu.stats.native_stats:
        pytest.skip("当前 codecin-native 仍是旧 ABI (只回传 steps)")

    stats = cpu.stats
    assert stats.instruction_count > 0
    # 直方图覆盖全部已执行指令, 且不再是占位项 "?"
    assert '?' not in stats.opcode_count
    assert sum(stats.opcode_count.values()) >= stats.instruction_count
    # 循环里的数组读写必须被计数
    assert stats.memory_reads > 0
    assert stats.memory_writes > 0
    # 分支: 循环条件 + 回边
    counters = stats.performance_counters.counters
    assert counters['branches'] > 0
    assert counters['branch_mispredictions'] >= 0
    assert counters['branch_mispredictions'] <= counters['branches']
    # 周期模型由真实直方图换算; IPC 与 CPI 必须自洽
    assert stats.inst_profiler.get_total_cycles() >= stats.instruction_count
    assert counters['cycles'] == stats.inst_profiler.get_total_cycles()
    assert counters['cycles'] > 0


def test_legacy_stats_output_is_honest(capsys):
    """没有统计尾部时不得再打印 "?" 或伪造的 0/100%。"""
    stats = Statistics()
    stats.instruction_count = 123
    stats.execution_time = 0.5

    stats.display_summary(Console(), native_used=True)
    out = capsys.readouterr().out

    assert '?' not in out
    assert 'N/A' in out
    assert 'Instruction Cycle Profile' not in out
    assert 'Performance Counters' not in out
