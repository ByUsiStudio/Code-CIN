"""官方标准库 dp.cin (经典动态规划) 测试。

覆盖: 每个公开函数至少一个用例 + n <= 0 / 空串 / 容量 0 / 不可达 /
容量钳制 (背包 cap > 256、零钱 amount > 256、串长 > 192、LIS n > 256、
网格列数 > 256) 等边界。
黄金用例全部可独立验算: fib(10)=55、fact(20)=2432902008176640000、
LCS("ABCBDAB","BDCABA")=4、编辑距离("kitten","sitting")=3、
0/1 背包教科书算例 (容量 50 -> 220)、LeetCode 53/64 的数列与网格等。
CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。
"""

import os

import pytest

from tests.helpers import run_cin_file

def _run(workdir, source, name='lib_dp_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)

DP_SRC = '''
import "dp.cin"

// 测试用全局数组: 避免大数组占用 main 的栈帧
int bigseq[70]
int biggrid[257]

function main() -> int {
    // ---- dp_fib (迭代) ----
    if (dp_fib(-1) != 0) { return 1 }
    if (dp_fib(0) != 0) { return 2 }
    if (dp_fib(1) != 1) { return 3 }
    if (dp_fib(2) != 1) { return 4 }
    if (dp_fib(10) != 55) { return 5 }
    if (dp_fib(20) != 6765) { return 6 }
    if (dp_fib(50) != 12586269025) { return 7 }
    if (dp_fib(70) != 190392490709135) { return 8 }

    // ---- dp_fib_memo: 必须与迭代版逐个一致 ----
    if (dp_fib_memo(-3) != 0) { return 9 }
    if (dp_fib_memo(0) != 0) { return 10 }
    if (dp_fib_memo(1) != 1) { return 11 }
    if (dp_fib_memo(10) != 55) { return 12 }
    if (dp_fib_memo(30) != 832040) { return 13 }
    if (dp_fib_memo(50) != 12586269025) { return 14 }
    if (dp_fib_memo(63) != dp_fib(63)) { return 15 }
    if (dp_fib_memo(64) != dp_fib(64)) { return 16 }
    if (dp_fib_memo(70) != dp_fib(70)) { return 17 }
    if (dp_fib_memo(5) != 5) { return 18 }

    // ---- dp_fact ----
    if (dp_fact(-1) != 0) { return 19 }
    if (dp_fact(0) != 1) { return 20 }
    if (dp_fact(1) != 1) { return 21 }
    if (dp_fact(5) != 120) { return 22 }
    if (dp_fact(10) != 3628800) { return 23 }
    if (dp_fact(20) != 2432902008176640000) { return 24 }

    // ---- dp_stairs ----
    if (dp_stairs(-1) != 0) { return 25 }
    if (dp_stairs(0) != 1) { return 26 }
    if (dp_stairs(1) != 1) { return 27 }
    if (dp_stairs(2) != 2) { return 28 }
    if (dp_stairs(3) != 3) { return 29 }
    if (dp_stairs(4) != 5) { return 30 }
    if (dp_stairs(10) != 89) { return 31 }

    // ---- dp_knapsack01 ----
    int w1[4] = {1, 3, 4, 5}
    int v1[4] = {1, 4, 5, 7}
    if (dp_knapsack01(w1, v1, 4, 7) != 9) { return 32 }
    int w2[3] = {10, 20, 30}
    int v2[3] = {60, 100, 120}
    if (dp_knapsack01(w2, v2, 3, 50) != 220) { return 33 }
    int w3[4] = {2, 3, 4, 5}
    int v3[4] = {3, 4, 5, 6}
    if (dp_knapsack01(w3, v3, 4, 5) != 7) { return 34 }
    if (dp_knapsack01(w1, v1, 4, 0) != 0) { return 35 }
    if (dp_knapsack01(w1, v1, 4, -3) != 0) { return 36 }
    if (dp_knapsack01(w1, v1, 0, 10) != 0) { return 37 }
    if (dp_knapsack01(w1, v1, -1, 10) != 0) { return 38 }
    int w4[1] = {9}
    int v4[1] = {5}
    if (dp_knapsack01(w4, v4, 1, 5) != 0) { return 39 }
    if (dp_knapsack01(w4, v4, 1, 9) != 5) { return 40 }
    int w5[2] = {0, 3}
    int v5[2] = {4, 5}
    if (dp_knapsack01(w5, v5, 2, 3) != 9) { return 41 }
    int w8[2] = {200, 100}
    int v8[2] = {7, 3}
    if (dp_knapsack01(w8, v8, 2, 300) != 7) { return 42 }
    if (dp_knapsack01(w8, v8, 2, 256) != 7) { return 43 }
    if (dp_knapsack01(w8, v8, 2, 100) != 3) { return 44 }
    int w6[1] = {257}
    int v6[1] = {9}
    if (dp_knapsack01(w6, v6, 1, 1000) != 0) { return 45 }
    int w7[1] = {256}
    int v7[1] = {9}
    if (dp_knapsack01(w7, v7, 1, 256) != 9) { return 46 }
    if (dp_knapsack01(w7, v7, 1, 1000) != 9) { return 47 }

    // ---- dp_lcs ----
    if (dp_lcs("", "") != 0) { return 48 }
    if (dp_lcs("", "abc") != 0) { return 49 }
    if (dp_lcs("abc", "") != 0) { return 50 }
    if (dp_lcs("ABCBDAB", "BDCABA") != 4) { return 51 }
    if (dp_lcs("abc", "abc") != 3) { return 52 }
    if (dp_lcs("abc", "abd") != 2) { return 53 }
    if (dp_lcs("AGGTAB", "GXTXAYB") != 4) { return 54 }

    // ---- dp_edit_distance ----
    if (dp_edit_distance("", "") != 0) { return 55 }
    if (dp_edit_distance("abc", "") != 3) { return 56 }
    if (dp_edit_distance("", "abc") != 3) { return 57 }
    if (dp_edit_distance("kitten", "sitting") != 3) { return 58 }
    if (dp_edit_distance("intention", "execution") != 5) { return 59 }
    if (dp_edit_distance("flaw", "lawn") != 2) { return 60 }
    if (dp_edit_distance("abc", "abc") != 0) { return 61 }

    // ---- dp_longest_palindrome ----
    if (dp_longest_palindrome("") != 0) { return 62 }
    if (dp_longest_palindrome("a") != 1) { return 63 }
    if (dp_longest_palindrome("ab") != 1) { return 64 }
    if (dp_longest_palindrome("aa") != 2) { return 65 }
    if (dp_longest_palindrome("abba") != 4) { return 66 }
    if (dp_longest_palindrome("babad") != 3) { return 67 }
    if (dp_longest_palindrome("cbbd") != 2) { return 68 }
    if (dp_longest_palindrome("forgeeksskeegfor") != 10) { return 69 }
    if (dp_longest_palindrome("abcda") != 1) { return 70 }

    // ---- 串长钳制: 200 字节 > DP_STR_MAX = 128, 只看前 128 字节 ----
    string s200 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    s200 = s200 + "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    s200 = s200 + "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    s200 = s200 + "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    if (strlen(s200) != 200) { return 71 }
    if (dp_edit_distance(s200, "") != 128) { return 72 }
    if (dp_edit_distance("", s200) != 128) { return 73 }
    if (dp_lcs(s200, "aaa") != 3) { return 74 }
    if (dp_lcs("aaa", s200) != 3) { return 75 }
    string sab = "abcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcabcab"
    if (strlen(sab) != 200) { return 76 }
    if (dp_longest_palindrome(sab) != 1) { return 77 }

    // ---- dp_coin_change ----
    int c1[4] = {1, 5, 10, 25}
    if (dp_coin_change(c1, 4, 0) != 0) { return 78 }
    if (dp_coin_change(c1, 4, -1) != -1) { return 79 }
    if (dp_coin_change(c1, 4, 63) != 6) { return 80 }
    if (dp_coin_change(c1, 4, 1) != 1) { return 81 }
    if (dp_coin_change(c1, 4, 25) != 1) { return 82 }
    if (dp_coin_change(c1, 4, 256) != 12) { return 83 }
    int c2[1] = {2}
    if (dp_coin_change(c2, 1, 3) != -1) { return 84 }
    if (dp_coin_change(c2, 1, 4) != 2) { return 85 }
    if (dp_coin_change(c1, 0, 5) != -1) { return 86 }
    int c3[3] = {0, -5, 7}
    if (dp_coin_change(c3, 3, 7) != 1) { return 87 }
    if (dp_coin_change(c3, 3, 14) != 2) { return 88 }
    if (dp_coin_change(c3, 3, 6) != -1) { return 89 }
    int c7[2] = {1, 7}
    if (dp_coin_change(c7, 2, 256) != 40) { return 90 }
    if (dp_coin_change(c7, 2, 300) != 40) { return 91 }

    // ---- dp_coin_ways ----
    int c4[3] = {1, 2, 5}
    if (dp_coin_ways(c4, 3, 0) != 1) { return 92 }
    if (dp_coin_ways(c4, 3, -1) != 0) { return 93 }
    if (dp_coin_ways(c4, 3, 5) != 4) { return 94 }
    int c5[3] = {1, 2, 3}
    if (dp_coin_ways(c5, 3, 4) != 4) { return 95 }
    int c6[4] = {1, 5, 10, 25}
    if (dp_coin_ways(c6, 4, 63) != 73) { return 96 }
    if (dp_coin_ways(c2, 1, 3) != 0) { return 97 }
    if (dp_coin_ways(c3, 3, 7) != 1) { return 98 }
    int c8[2] = {1, 2}
    if (dp_coin_ways(c8, 2, 256) != 129) { return 99 }
    if (dp_coin_ways(c7, 2, 256) != 37) { return 100 }
    if (dp_coin_ways(c7, 2, 300) != 37) { return 101 }

    // ---- dp_lis ----
    int a1[8] = {10, 9, 2, 5, 3, 7, 101, 18}
    if (dp_lis(a1, 8) != 4) { return 102 }
    int a2[5] = {3, 10, 2, 1, 20}
    if (dp_lis(a2, 5) != 3) { return 103 }
    int a3[4] = {4, 3, 2, 1}
    if (dp_lis(a3, 4) != 1) { return 104 }
    int a4[3] = {2, 2, 2}
    if (dp_lis(a4, 3) != 1) { return 105 }
    int a5[1] = {7}
    if (dp_lis(a5, 1) != 1) { return 106 }
    if (dp_lis(a1, 0) != 0) { return 107 }
    if (dp_lis(a1, -4) != 0) { return 108 }
    int a6[6] = {1, 2, 3, 4, 5, 6}
    if (dp_lis(a6, 6) != 6) { return 109 }
    for (int i = 0; i < 70; i = i + 1) {
        bigseq[i] = i + 1
    }
    if (dp_lis(bigseq, 70) != 64) { return 110 }
    if (dp_lis(bigseq, 64) != 64) { return 111 }
    if (dp_lis(bigseq, 5) != 5) { return 112 }

    // ---- dp_max_subarray ----
    int s1[9] = {-2, 1, -3, 4, -1, 2, 1, -5, 4}
    if (dp_max_subarray(s1, 9) != 6) { return 113 }
    int s2[3] = {-1, -2, -3}
    if (dp_max_subarray(s2, 3) != -1) { return 114 }
    int s3[3] = {5, -1, 5}
    if (dp_max_subarray(s3, 3) != 9) { return 115 }
    int s4[1] = {-7}
    if (dp_max_subarray(s4, 1) != -7) { return 116 }
    if (dp_max_subarray(s1, 0) != 0) { return 117 }
    if (dp_max_subarray(s1, -2) != 0) { return 118 }

    // ---- dp_min_path ----
    int g1[9] = {1, 3, 1, 1, 5, 1, 4, 2, 1}
    if (dp_min_path(g1, 3, 3) != 7) { return 119 }
    int g2[1] = {5}
    if (dp_min_path(g2, 1, 1) != 5) { return 120 }
    int g3[3] = {1, 2, 3}
    if (dp_min_path(g3, 1, 3) != 6) { return 121 }
    if (dp_min_path(g3, 3, 1) != 6) { return 122 }
    int g4[4] = {1, 2, 3, 4}
    if (dp_min_path(g4, 2, 2) != 7) { return 123 }
    int g5[4] = {-1, -2, -3, -4}
    if (dp_min_path(g5, 2, 2) != -8) { return 124 }
    if (dp_min_path(g1, 0, 3) != 0) { return 125 }
    if (dp_min_path(g1, 3, 0) != 0) { return 126 }
    if (dp_min_path(g1, -1, -1) != 0) { return 127 }
    if (dp_min_path(biggrid, 1, 257) != -1) { return 128 }
    if (dp_min_path(biggrid, 1, 256) != 0) { return 129 }

    // ---- 反复调用不串状态 (共享暂存区) ----
    if (dp_lcs("abc", "abc") != 3) { return 130 }
    if (dp_coin_change(c1, 4, 63) != 6) { return 131 }
    if (dp_lis(a1, 8) != 4) { return 132 }
    if (dp_edit_distance("kitten", "sitting") != 3) { return 133 }
    if (dp_knapsack01(w1, v1, 4, 7) != 9) { return 134 }
    if (dp_coin_ways(c6, 4, 63) != 73) { return 135 }
    if (dp_min_path(g1, 3, 3) != 7) { return 136 }
    if (dp_max_subarray(s1, 9) != 6) { return 137 }
    return 0
}'''

def test_dp_lib(workdir):
    cpu = _run(workdir, DP_SRC)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
