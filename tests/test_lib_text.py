"""官方标准库 text.cin (纯 CIN 文本处理) 测试。

每个用例的 CIN `main()` 返回 0 表示全部通过, 非 0 是具体失败点编号。
两条执行路径 (interp / native) 都必须过。
"""

import os

import pytest

from tests.helpers import run_cin_file

def _run(workdir, source, name='lib_text_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)

def test_text_predicates(workdir):
    """txt_is_empty / txt_equals_ignore_case / txt_startswith_ignore_case。"""
    src = '''
import "text.cin"
function main() -> int {
    if (txt_is_empty("") != 1) { return 1 }
    if (txt_is_empty(" ") != 0) { return 2 }
    if (txt_is_empty("a") != 0) { return 3 }
    if (txt_is_empty("\\xE4\\xB8\\xAD") != 0) { return 4 }
    if (txt_equals_ignore_case("AbC", "aBc") != 1) { return 5 }
    if (txt_equals_ignore_case("abc", "abcd") != 0) { return 6 }
    if (txt_equals_ignore_case("", "") != 1) { return 7 }
    if (txt_equals_ignore_case("a", "a ") != 0) { return 8 }
    if (txt_equals_ignore_case("a1", "A1") != 1) { return 9 }
    if (txt_equals_ignore_case("a[", "A{") != 0) { return 10 }
    if (txt_equals_ignore_case("\\xE4\\xB8\\xAD", "\\xE4\\xB8\\xAD") != 1) { return 11 }
    if (txt_equals_ignore_case("\\xE4\\xB8\\xAD", "\\xE4\\xB8\\xADx") != 0) { return 12 }
    if (txt_startswith_ignore_case("Hello.cin", "heLLo") != 1) { return 13 }
    if (txt_startswith_ignore_case("Hello.cin", "hello.cin.x") != 0) { return 14 }
    if (txt_startswith_ignore_case("abc", "") != 1) { return 15 }
    if (txt_startswith_ignore_case("", "a") != 0) { return 16 }
    if (txt_startswith_ignore_case("", "") != 1) { return 17 }
    if (txt_startswith_ignore_case("\\xE4\\xB8\\xADx", "\\xE4\\xB8\\xAD") != 1) { return 18 }
    if (txt_startswith_ignore_case("abc", "B") != 0) { return 19 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0

def test_text_search(workdir):
    """txt_index_from / txt_last_index_of (字节下标, 越界裁剪, 重叠匹配)。"""
    src = '''
import "text.cin"
function main() -> int {
    if (txt_index_from("banana", "na", 0) != 2) { return 1 }
    if (txt_index_from("banana", "na", 3) != 4) { return 2 }
    if (txt_index_from("banana", "na", 5) != -1) { return 3 }
    if (txt_index_from("banana", "", 99) != 6) { return 4 }
    if (txt_index_from("banana", "", -7) != 0) { return 5 }
    if (txt_index_from("banana", "ba", -5) != 0) { return 6 }
    if (txt_index_from("banana", "x", 0) != -1) { return 7 }
    if (txt_index_from("", "a", 0) != -1) { return 8 }
    if (txt_index_from("", "", 0) != 0) { return 9 }
    if (txt_index_from("aaa", "aa", 1) != 1) { return 10 }
    if (txt_index_from("abc", "abcx", 0) != -1) { return 11 }
    if (txt_last_index_of("banana", "na") != 4) { return 12 }
    if (txt_last_index_of("banana", "x") != -1) { return 13 }
    if (txt_last_index_of("banana", "") != 6) { return 14 }
    if (txt_last_index_of("aaaa", "aa") != 2) { return 15 }
    if (txt_last_index_of("", "a") != -1) { return 16 }
    if (txt_last_index_of("", "") != 0) { return 17 }
    if (txt_last_index_of("abc", "abc") != 0) { return 18 }
    if (txt_last_index_of("abcabc", "abc") != 3) { return 19 }
    // 字节语义: 多字节字符按字节计数
    string zh = "\\xE4\\xB8\\xAD\\xE4\\xB8\\xAD"
    if (strlen(zh) != 6) { return 20 }
    if (txt_index_from(zh, "\\xE4\\xB8\\xAD", 0) != 0) { return 21 }
    if (txt_index_from(zh, "\\xE4\\xB8\\xAD", 1) != 3) { return 22 }
    if (txt_index_from(zh, "\\xE4\\xB8\\xAD", 4) != -1) { return 23 }
    if (txt_last_index_of(zh, "\\xE4\\xB8\\xAD") != 3) { return 24 }
    if (txt_index_from(zh, "\\xB8\\xAD", 0) != 1) { return 25 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0

def test_text_transforms(workdir):
    """txt_reverse / txt_capitalize / txt_title / txt_swap_case / txt_word_count。"""
    src = '''
import "text.cin"
function main() -> int {
    if (strcmp(txt_reverse(""), "") != 0) { return 1 }
    if (strcmp(txt_reverse("a"), "a") != 0) { return 2 }
    if (strcmp(txt_reverse("abcde"), "edcba") != 0) { return 3 }
    if (strcmp(txt_reverse("ab ba"), "ab ba") != 0) { return 4 }
    if (strcmp(txt_reverse("0123456789"), "9876543210") != 0) { return 5 }
    if (strcmp(txt_capitalize(""), "") != 0) { return 6 }
    if (strcmp(txt_capitalize("hELLO"), "Hello") != 0) { return 7 }
    if (strcmp(txt_capitalize("hello world"), "Hello world") != 0) { return 8 }
    if (strcmp(txt_capitalize("123abc"), "123Abc") != 0) { return 9 }
    if (strcmp(txt_capitalize("  hi"), "  Hi") != 0) { return 10 }
    if (strcmp(txt_capitalize("abc DEF"), "Abc def") != 0) { return 11 }
    if (strcmp(txt_capitalize("!!!"), "!!!") != 0) { return 12 }
    if (strcmp(txt_title(""), "") != 0) { return 13 }
    if (strcmp(txt_title("hello world"), "Hello World") != 0) { return 14 }
    if (strcmp(txt_title("tHE qUICK brown"), "The Quick Brown") != 0) { return 15 }
    if (strcmp(txt_title("it's ok"), "It'S Ok") != 0) { return 16 }
    if (strcmp(txt_title("  spaced  out  "), "  Spaced  Out  ") != 0) { return 17 }
    if (strcmp(txt_title("abc3def"), "Abc3def") != 0) { return 18 }
    if (strcmp(txt_title("a-b"), "A-B") != 0) { return 19 }
    if (strcmp(txt_swap_case(""), "") != 0) { return 20 }
    if (strcmp(txt_swap_case("Hello, World!"), "hELLO, wORLD!") != 0) { return 21 }
    if (strcmp(txt_swap_case("aA1zZ"), "Aa1Zz") != 0) { return 22 }
    if (strcmp(txt_swap_case("aAbBcC"), "AaBbCc") != 0) { return 23 }
    if (txt_word_count("") != 0) { return 24 }
    if (txt_word_count("   ") != 0) { return 25 }
    if (txt_word_count("one") != 1) { return 26 }
    if (txt_word_count("  one   two  three ") != 3) { return 27 }
    if (txt_word_count("a\\tb\\nc\\rd") != 4) { return 28 }
    if (txt_word_count("\\xE4\\xB8\\xAD \\xE6\\x96\\x87") != 2) { return 29 }
    // 非 ASCII 字节不属于 ASCII 字母: 整段原样保留
    if (strcmp(txt_swap_case("\\xE4\\xB8\\xADa"), "\\xE4\\xB8\\xADA") != 0) { return 30 }
    if (strcmp(txt_swap_case("b\\xE4\\xB8\\xAD"), "B\\xE4\\xB8\\xAD") != 0) { return 31 }
    if (strcmp(txt_title("\\xE4\\xB8\\xADab"), "\\xE4\\xB8\\xADAb") != 0) { return 32 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0

def test_text_edits(workdir):
    """txt_replace / txt_replace_first / txt_remove / txt_slice / txt_insert /
    txt_delete_range 的常规与越界行为。"""
    src = '''
import "text.cin"
function main() -> int {
    if (strcmp(txt_replace("banana", "na", "NA"), "baNANA") != 0) { return 1 }
    if (strcmp(txt_replace("a.b.c", ".", "-"), "a-b-c") != 0) { return 2 }
    if (strcmp(txt_replace("abc", "x", "y"), "abc") != 0) { return 3 }
    if (strcmp(txt_replace("abc", "", "X"), "abc") != 0) { return 4 }
    if (strcmp(txt_replace("aaa", "aa", "b"), "ba") != 0) { return 5 }
    if (strcmp(txt_replace("abc", "b", ""), "ac") != 0) { return 6 }
    if (strcmp(txt_replace("", "a", "b"), "") != 0) { return 7 }
    if (strcmp(txt_replace("abc", "abc", ""), "") != 0) { return 8 }
    if (strcmp(txt_replace("x", "x", "yy"), "yy") != 0) { return 9 }
    if (strcmp(txt_replace_first("banana", "na", "NA"), "baNAna") != 0) { return 10 }
    if (strcmp(txt_replace_first("abc", "x", "y"), "abc") != 0) { return 11 }
    if (strcmp(txt_replace_first("abc", "", "X"), "abc") != 0) { return 12 }
    if (strcmp(txt_replace_first("aaa", "a", "b"), "baa") != 0) { return 13 }
    if (strcmp(txt_remove("a-b-c", "-"), "abc") != 0) { return 14 }
    if (strcmp(txt_remove("abc", ""), "abc") != 0) { return 15 }
    if (strcmp(txt_remove("abc", "z"), "abc") != 0) { return 16 }
    if (strcmp(txt_remove("aaa", "a"), "") != 0) { return 17 }
    // 非 ASCII: 模式用完整字符, 结果仍为完整字节序列
    if (strcmp(txt_remove("\\xE4\\xB8\\xADx\\xE4\\xB8\\xAD", "\\xE4\\xB8\\xAD"), "x") != 0) { return 18 }
    if (strcmp(txt_replace("\\xE4\\xB8\\xADa", "a", "\\xE4\\xB8\\xAD"), "\\xE4\\xB8\\xAD\\xE4\\xB8\\xAD") != 0) { return 19 }
    if (strcmp(txt_insert("abc", 1, "XY"), "aXYbc") != 0) { return 20 }
    if (strcmp(txt_insert("abc", 0, ">"), ">abc") != 0) { return 21 }
    if (strcmp(txt_insert("abc", 3, "<"), "abc<") != 0) { return 22 }
    if (strcmp(txt_insert("abc", 99, "!"), "abc!") != 0) { return 23 }
    if (strcmp(txt_insert("abc", -5, "!"), "!abc") != 0) { return 24 }
    if (strcmp(txt_insert("abc", 1, ""), "abc") != 0) { return 25 }
    if (strcmp(txt_insert("", 5, "ab"), "ab") != 0) { return 26 }
    if (strcmp(txt_delete_range("abcdef", 1, 3), "aef") != 0) { return 27 }
    if (strcmp(txt_delete_range("abcdef", 0, 0), "abcdef") != 0) { return 28 }
    if (strcmp(txt_delete_range("abcdef", 4, 100), "abcd") != 0) { return 29 }
    if (strcmp(txt_delete_range("abcdef", 99, 2), "abcdef") != 0) { return 30 }
    if (strcmp(txt_delete_range("abcdef", -3, 2), "cdef") != 0) { return 31 }
    if (strcmp(txt_delete_range("abcdef", 2, -1), "abcdef") != 0) { return 32 }
    if (strcmp(txt_delete_range("", 0, 5), "") != 0) { return 33 }
    if (strcmp(txt_slice("abcdef", 1, 4), "bcd") != 0) { return 34 }
    if (strcmp(txt_slice("abcdef", 0, 6), "abcdef") != 0) { return 35 }
    if (strcmp(txt_slice("abcdef", 4, 2), "") != 0) { return 36 }
    if (strcmp(txt_slice("abcdef", -2, 3), "abc") != 0) { return 37 }
    if (strcmp(txt_slice("abcdef", 3, 99), "def") != 0) { return 38 }
    if (strcmp(txt_slice("abcdef", 99, 100), "") != 0) { return 39 }
    if (strcmp(txt_slice("", 0, 5), "") != 0) { return 40 }
    if (strcmp(txt_slice("abc", 2, 2), "") != 0) { return 41 }
    // 字节切片: 对齐到完整字符
    if (strcmp(txt_slice("\\xE4\\xB8\\xAD\\xE6\\x96\\x87", 3, 6), "\\xE6\\x96\\x87") != 0) { return 42 }
    if (strcmp(txt_delete_range("\\xE4\\xB8\\xAD\\xE6\\x96\\x87", 3, 3), "\\xE4\\xB8\\xAD") != 0) { return 43 }
    if (strcmp(txt_insert("\\xE4\\xB8\\xAD\\xE6\\x96\\x87", 3, "x"), "\\xE4\\xB8\\xADx\\xE6\\x96\\x87") != 0) { return 44 }
    // 字节切片: 允许切开多字节字符 (纯字节语义)
    if (strcmp(txt_slice("\\xE4\\xB8\\xAD", 1, 2), "\\xB8") != 0) { return 45 }
    if (strlen(txt_slice("\\xE4\\xB8\\xAD", 1, 3)) != 2) { return 46 }
    return 0
}'''
    assert _run(workdir, src).regs.read(0) == 0

def test_text_long_strings(workdir):
    """长串 (120 字节) 上不分配/少分配堆块的路径。

    只调用不逐段重建结果串的接口: 字符串拼接不回收堆块, 逐字节重建的长串会
    吃满默认 64 KiB 内存 (见 test_text_long_rebuild 的说明)。
    """
    many = 'ab ' * 40            # 120 字节
    src = ('''
import "text.cin"
function main() -> int {
    string many = "''' + many + '''"
    if (strlen(many) != 120) { return 1 }
    if (txt_is_empty(many) != 0) { return 2 }
    if (txt_word_count(many) != 40) { return 3 }
    if (txt_last_index_of(many, "ab") != 117) { return 4 }
    if (txt_index_from(many, "b", 118) != 118) { return 5 }
    if (txt_index_from(many, "b", 119) != -1) { return 6 }
    if (strcmp(txt_slice(many, 117, 120), "ab ") != 0) { return 7 }
    if (strlen(txt_delete_range(many, 117, 3)) != 117) { return 8 }
    if (strcmp(txt_insert(many, 0, ""), many) != 0) { return 9 }
    if (txt_equals_ignore_case(many, many) != 1) { return 10 }
    if (strcmp(txt_replace_first(many, "ab", "AB"), "''' + ('AB ' + 'ab ' * 39) + '''") != 0) { return 11 }
    if (strcmp(txt_remove(many, " "), "''' + 'ab' * 40 + '''") != 0) { return 12 }
    return 0
}''')
    assert _run(workdir, src).regs.read(0) == 0

def test_text_long_rebuild(workdir):
    """长串 (90 字节, 30 个单词) 的全串重建: reverse / swap_case / title。

    规模刻意停在 90 字节: 每次拼接都新建堆块且不回收, 重建 n 字节约需 O(n^2)
    字节堆, 默认 64 KiB 内存下 120 字节的多段文本就会 Stack overflow。
    """
    many = 'ab ' * 30            # 90 字节
    rev = ' ba' * 30             # txt_reverse(many)
    upper = 'AB ' * 30           # txt_swap_case(many)
    titled = 'Ab ' * 30          # txt_title(many)
    src = ('''
import "text.cin"
function main() -> int {
    string many = "''' + many + '''"
    if (strlen(many) != 90) { return 1 }
    if (txt_word_count(many) != 30) { return 2 }
    if (strcmp(txt_reverse(many), "''' + rev + '''") != 0) { return 3 }
    if (strcmp(txt_swap_case(many), "''' + upper + '''") != 0) { return 4 }
    if (strcmp(txt_title(many), "''' + titled + '''") != 0) { return 5 }
    if (strcmp(txt_capitalize("''' + titled + '''"), "''' + ('Ab ' + 'ab ' * 29) + '''") != 0) { return 6 }
    return 0
}''')
    assert _run(workdir, src).regs.read(0) == 0
