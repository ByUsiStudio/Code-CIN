"""官方标准库 graph.cin (定长邻接矩阵图) 测试。

覆盖: 每个公开函数至少一个用例 + 空图 / 单节点 / n 钳制 / 节点越界 /
重复边 / 自环 / 权重 0 与负权 / 不可达 / 有环与无环 / 有向与无向。
黄金用例全部可独立验算 (CLRS 最短路算例、手工数过的 BFS/DFS 顺序等)。
CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。
"""

import os

import pytest

from tests.helpers import run_cin_file

def _run(workdir, source, name='lib_graph_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)

GRAPH_SRC = '''
import "graph.cin"

function main() -> int {
    // ---- 容量常量与空图 ----
    if (graph_max() != 16) { return 1 }
    graph_reset(0, 0)
    if (graph_nodes() != 0) { return 2 }
    if (graph_is_directed() != 0) { return 3 }
    if (graph_edge_count() != 0) { return 4 }
    if (graph_component_count() != 0) { return 5 }
    if (graph_connected() != 1) { return 6 }
    if (graph_is_cyclic() != 0) { return 7 }
    if (graph_degree(0) != -1) { return 8 }
    if (graph_in_degree(0) != -1) { return 9 }
    if (graph_has_edge(0, 0) != 0) { return 10 }
    if (graph_weight(0, 0) != 0) { return 11 }
    if (graph_add_edge(0, 0, 1) != 0) { return 12 }
    int order[16]
    int dist[16]
    if (graph_bfs(0, order, dist) != 0) { return 13 }
    if (graph_dfs(0, order) != 0) { return 14 }
    if (graph_dijkstra(0, dist) != 0) { return 15 }
    if (graph_topo_order(order) != -1) { return 16 }

    // ---- n 钳制 / 越界 ----
    graph_reset(-5, 0)
    if (graph_nodes() != 0) { return 17 }
    graph_reset(99, 1)
    if (graph_nodes() != 16) { return 18 }
    if (graph_is_directed() != 1) { return 19 }
    if (graph_topo_order(order) != 16 || order[0] != 0 || order[15] != 15) { return 20 }
    if (graph_add_edge(0, 15, 2) != 1) { return 21 }
    if (graph_add_edge(0, 16, 2) != 0) { return 22 }
    if (graph_add_edge(16, 0, 2) != 0) { return 23 }
    if (graph_add_edge(-1, 0, 2) != 0) { return 24 }
    if (graph_has_edge(0, 16) != 0) { return 25 }
    if (graph_has_edge(16, 0) != 0) { return 26 }
    if (graph_degree(16) != -1) { return 27 }
    if (graph_in_degree(-1) != -1) { return 28 }
    if (graph_edge_count() != 1) { return 29 }
    if (graph_has_edge(15, 0) != 0) { return 30 }

    // ---- graph_clear: 只删边, 保留节点数与方向 ----
    graph_clear()
    if (graph_nodes() != 16) { return 31 }
    if (graph_is_directed() != 1) { return 32 }
    if (graph_edge_count() != 0) { return 33 }
    if (graph_has_edge(0, 15) != 0) { return 34 }

    // ---- 无向图: 加边 / 重复边 / 权重 0 与负权 / 度 ----
    graph_reset(4, 0)
    if (graph_is_directed() != 0) { return 35 }
    if (graph_add_edge(0, 1, 7) != 1) { return 36 }
    if (graph_add_edge(0, 1, 8) != 0) { return 37 }
    if (graph_add_edge(1, 0, 9) != 0) { return 38 }
    if (graph_has_edge(0, 1) != 1 || graph_has_edge(1, 0) != 1) { return 39 }
    if (graph_weight(0, 1) != 7 || graph_weight(1, 0) != 7) { return 40 }
    if (graph_add_edge(1, 2, 0) != 1) { return 41 }
    if (graph_has_edge(1, 2) != 1) { return 42 }
    if (graph_weight(1, 2) != 0) { return 43 }
    if (graph_add_edge(2, 3, -4) != 1) { return 44 }
    if (graph_weight(2, 3) != -4) { return 45 }
    if (graph_edge_count() != 3) { return 46 }
    if (graph_degree(0) != 1) { return 47 }
    if (graph_degree(1) != 2) { return 48 }
    if (graph_in_degree(1) != 2) { return 49 }
    if (graph_degree(2) != 2) { return 50 }
    if (graph_degree(3) != 1) { return 51 }
    if (graph_weight(0, 2) != 0) { return 52 }
    if (graph_has_edge(0, 2) != 0) { return 53 }
    if (graph_has_edge(0, 4) != 0) { return 54 }
    if (graph_has_edge(-1, 0) != 0) { return 55 }

    // ---- 自环: 只算一条边, 只贡献 1 度, 算环 ----
    if (graph_add_edge(2, 2, 5) != 1) { return 56 }
    if (graph_add_edge(2, 2, 6) != 0) { return 57 }
    if (graph_has_edge(2, 2) != 1) { return 58 }
    if (graph_weight(2, 2) != 5) { return 59 }
    if (graph_edge_count() != 4) { return 60 }
    if (graph_degree(2) != 3) { return 61 }
    if (graph_is_cyclic() != 1) { return 62 }
    if (graph_component_count() != 1) { return 63 }

    // ---- BFS / DFS: 7 个节点, 节点 6 孤立 ----
    graph_reset(7, 0)
    graph_add_edge(0, 1, 1)
    graph_add_edge(0, 2, 1)
    graph_add_edge(1, 3, 1)
    graph_add_edge(2, 4, 1)
    graph_add_edge(4, 5, 1)
    if (graph_nodes() != 7) { return 64 }
    if (graph_edge_count() != 5) { return 65 }
    if (graph_component_count() != 2) { return 66 }
    if (graph_connected() != 0) { return 67 }
    if (graph_is_cyclic() != 0) { return 68 }

    int o[16]
    int d[16]
    int cnt = graph_bfs(0, o, d)
    if (cnt != 6) { return 69 }
    if (o[0] != 0 || o[1] != 1 || o[2] != 2 || o[3] != 3 || o[4] != 4 || o[5] != 5) { return 70 }
    if (d[0] != 0 || d[1] != 1 || d[2] != 1 || d[3] != 2 || d[4] != 2 || d[5] != 3) { return 71 }
    if (d[6] != -1) { return 72 }

    cnt = graph_bfs(3, o, d)
    if (cnt != 6) { return 73 }
    if (o[0] != 3 || o[1] != 1 || o[2] != 0 || o[3] != 2 || o[4] != 4 || o[5] != 5) { return 74 }
    if (d[3] != 0 || d[1] != 1 || d[0] != 2 || d[2] != 3 || d[4] != 4 || d[5] != 5) { return 75 }

    cnt = graph_bfs(6, o, d)
    if (cnt != 1) { return 76 }
    if (o[0] != 6 || d[6] != 0 || d[0] != -1) { return 77 }

    if (graph_bfs(9, o, d) != 0) { return 78 }
    if (d[0] != -1 || d[6] != -1) { return 79 }

    cnt = graph_dfs(0, o)
    if (cnt != 6) { return 80 }
    if (o[0] != 0 || o[1] != 1 || o[2] != 3 || o[3] != 2 || o[4] != 4 || o[5] != 5) { return 81 }
    cnt = graph_dfs(6, o)
    if (cnt != 1 || o[0] != 6) { return 82 }
    if (graph_dfs(9, o) != 0) { return 83 }

    // 加一条边 5-3 造环 (此时仍是 2 个分量)
    if (graph_add_edge(3, 5, 1) != 1) { return 84 }
    if (graph_edge_count() != 6) { return 85 }
    if (graph_component_count() != 2) { return 86 }
    if (graph_connected() != 0) { return 87 }
    if (graph_is_cyclic() != 1) { return 88 }

    // ---- 单节点 ----
    graph_reset(1, 0)
    if (graph_nodes() != 1) { return 89 }
    if (graph_component_count() != 1) { return 90 }
    if (graph_connected() != 1) { return 91 }
    if (graph_degree(0) != 0) { return 92 }
    if (graph_is_cyclic() != 0) { return 93 }
    cnt = graph_bfs(0, o, d)
    if (cnt != 1 || o[0] != 0 || d[0] != 0) { return 94 }
    cnt = graph_dfs(0, o)
    if (cnt != 1 || o[0] != 0) { return 95 }
    if (graph_dijkstra(0, d) != 1 || d[0] != 0) { return 96 }

    // ---- 连通性: 环 + 孤立点 ----
    graph_reset(4, 0)
    graph_add_edge(0, 1, 1)
    graph_add_edge(1, 2, 1)
    graph_add_edge(2, 0, 1)
    if (graph_component_count() != 2) { return 97 }
    if (graph_connected() != 0) { return 98 }
    if (graph_is_cyclic() != 1) { return 99 }
    graph_add_edge(2, 3, 1)
    if (graph_component_count() != 1) { return 100 }
    if (graph_connected() != 1) { return 101 }
    if (graph_is_cyclic() != 1) { return 102 }

    // ---- 有向图: BFS 只沿出边 ----
    graph_reset(3, 1)
    graph_add_edge(0, 1, 1)
    cnt = graph_bfs(0, o, d)
    if (cnt != 2) { return 103 }
    if (o[0] != 0 || o[1] != 1 || d[0] != 0 || d[1] != 1) { return 104 }
    if (d[2] != -1) { return 105 }
    cnt = graph_bfs(2, o, d)
    if (cnt != 1 || o[0] != 2) { return 106 }
    cnt = graph_bfs(1, o, d)
    if (cnt != 1 || o[0] != 1) { return 107 }
    if (graph_has_edge(1, 0) != 0) { return 108 }
    if (graph_degree(0) != 1 || graph_in_degree(0) != 0) { return 109 }
    if (graph_degree(1) != 0 || graph_in_degree(1) != 1) { return 110 }
    if (graph_connected() != 0) { return 111 }
    if (graph_component_count() != 2) { return 112 }
    if (graph_is_cyclic() != 0) { return 113 }

    // ---- 有向图拓扑排序 (Kahn, 每轮取编号最小的入度 0 节点) ----
    graph_reset(6, 1)
    graph_add_edge(5, 2, 1)
    graph_add_edge(5, 0, 1)
    graph_add_edge(4, 0, 1)
    graph_add_edge(4, 1, 1)
    graph_add_edge(2, 3, 1)
    graph_add_edge(3, 1, 1)
    if (graph_edge_count() != 6) { return 114 }
    int t[16]
    if (graph_topo_order(t) != 6) { return 115 }
    if (t[0] != 4 || t[1] != 5 || t[2] != 0 || t[3] != 2 || t[4] != 3 || t[5] != 1) { return 116 }
    if (graph_is_cyclic() != 0) { return 117 }
    if (graph_connected() != 1) { return 118 }
    if (graph_degree(5) != 2 || graph_in_degree(1) != 2) { return 119 }
    graph_add_edge(1, 5, 1)
    if (graph_is_cyclic() != 1) { return 120 }
    if (graph_topo_order(t) != -1) { return 121 }
    if (graph_add_edge(0, 3, 1) != 1) { return 122 }

    // 有向自环
    graph_reset(2, 1)
    graph_add_edge(0, 0, 3)
    if (graph_is_cyclic() != 1) { return 123 }
    if (graph_topo_order(t) != -1) { return 124 }
    if (graph_degree(0) != 1 || graph_in_degree(0) != 1) { return 125 }

    // 2 节点有向无环
    graph_reset(2, 1)
    graph_add_edge(0, 1, 1)
    if (graph_is_cyclic() != 0) { return 126 }
    if (graph_topo_order(t) != 2) { return 127 }
    if (t[0] != 0 || t[1] != 1) { return 128 }

    // 无向图没有拓扑序
    graph_reset(3, 0)
    graph_add_edge(0, 1, 1)
    if (graph_topo_order(t) != -1) { return 129 }

    // ---- Dijkstra: CLRS 教科书算例 ----
    graph_reset(5, 1)
    graph_add_edge(0, 1, 10)
    graph_add_edge(0, 3, 5)
    graph_add_edge(1, 2, 1)
    graph_add_edge(1, 3, 2)
    graph_add_edge(2, 4, 4)
    graph_add_edge(3, 1, 3)
    graph_add_edge(3, 2, 9)
    graph_add_edge(3, 4, 2)
    graph_add_edge(4, 2, 6)
    graph_add_edge(4, 0, 7)
    int dd[16]
    int reach = graph_dijkstra(0, dd)
    if (reach != 5) { return 130 }
    if (dd[0] != 0) { return 131 }
    if (dd[1] != 8) { return 132 }
    if (dd[2] != 9) { return 133 }
    if (dd[3] != 5) { return 134 }
    if (dd[4] != 7) { return 135 }

    // ---- Dijkstra: 不可达与 0 权边 ----
    graph_reset(6, 1)
    graph_add_edge(0, 1, 4)
    graph_add_edge(0, 2, 1)
    graph_add_edge(2, 1, 2)
    graph_add_edge(2, 3, 0)
    reach = graph_dijkstra(0, dd)
    if (reach != 4) { return 136 }
    if (dd[0] != 0 || dd[2] != 1 || dd[1] != 3 || dd[3] != 1) { return 137 }
    if (dd[4] != -1 || dd[5] != -1) { return 138 }
    if (graph_dijkstra(9, dd) != 0) { return 139 }
    graph_reset(0, 1)
    if (graph_dijkstra(0, dd) != 0) { return 140 }

    // ---- Dijkstra: 无向图 (两个方向都可走) ----
    graph_reset(3, 0)
    graph_add_edge(0, 1, 5)
    graph_add_edge(1, 2, 5)
    reach = graph_dijkstra(0, dd)
    if (reach != 3) { return 141 }
    if (dd[0] != 0 || dd[1] != 5 || dd[2] != 10) { return 142 }
    reach = graph_dijkstra(2, dd)
    if (dd[2] != 0 || dd[1] != 5 || dd[0] != 10) { return 143 }

    // ---- reset 会彻底丢弃上一张图的状态 ----
    graph_reset(2, 0)
    if (graph_nodes() != 2 || graph_is_directed() != 0) { return 144 }
    if (graph_edge_count() != 0) { return 145 }
    if (graph_has_edge(0, 1) != 0) { return 146 }
    if (graph_add_edge(0, 1, 1) != 1) { return 147 }
    if (graph_degree(0) != 1 || graph_degree(1) != 1) { return 148 }
    if (graph_component_count() != 1) { return 149 }
    if (graph_dijkstra(1, dd) != 2) { return 150 }
    if (dd[0] != 1 || dd[1] != 0) { return 151 }

    // ---- 满容量 16 节点 ----
    graph_reset(16, 0)
    for (int i = 0; i < 15; i = i + 1) {
        if (graph_add_edge(i, i + 1, i + 1) != 1) { return 152 }
    }
    if (graph_nodes() != 16) { return 153 }
    if (graph_edge_count() != 15) { return 154 }
    if (graph_connected() != 1) { return 155 }
    if (graph_is_cyclic() != 0) { return 156 }
    if (graph_degree(0) != 1 || graph_degree(7) != 2 || graph_degree(15) != 1) { return 157 }
    cnt = graph_bfs(0, o, d)
    if (cnt != 16) { return 158 }
    if (o[0] != 0 || o[15] != 15) { return 159 }
    if (d[15] != 15) { return 160 }
    reach = graph_dijkstra(0, dd)
    if (reach != 16) { return 161 }
    if (dd[15] != 120) { return 162 }
    if (graph_weight(14, 15) != 15) { return 163 }

    // ---- 内部辅助 (非稳定接口, 顺带覆盖) ----
    if (graph_aux_node(0) != 1 || graph_aux_node(15) != 1) { return 164 }
    if (graph_aux_node(16) != 0 || graph_aux_node(-1) != 0) { return 165 }
    if (graph_aux_arc(0, 1) != 1 || graph_aux_arc(1, 0) != 1) { return 166 }
    if (graph_aux_arc(0, 2) != 0) { return 167 }
    return 0
}'''

TOGETHER_SRC = '''
import "graph.cin"
import "dp.cin"

function main() -> int {
    graph_reset(3, 0)
    graph_add_edge(0, 1, 2)
    graph_add_edge(1, 2, 3)
    int dd[16]
    if (graph_dijkstra(0, dd) != 3) { return 1 }
    if (dd[0] != 0 || dd[1] != 2 || dd[2] != 5) { return 2 }
    if (graph_connected() != 1) { return 3 }
    if (dp_fib(10) != 55) { return 4 }
    if (dp_lcs("ABCBDAB", "BDCABA") != 4) { return 5 }
    return 0
}'''

def test_graph_lib(workdir):
    cpu = _run(workdir, GRAPH_SRC)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0

def test_graph_and_dp_importable_together(workdir):
    """两个新库同时导入不得冲突 (全局符号/名称)。"""
    cpu = _run(workdir, TOGETHER_SRC, name='lib_graph_dp_together.cin')
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
