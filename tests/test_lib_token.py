"""官方标准库 token.cin (纯 CIN 切分/分词) 测试。

每个用例的 CIN `main()` 返回 0 表示全部通过, 非 0 是具体失败点编号。
两条执行路径 (interp / native) 都必须过。
"""

import os

import pytest

from tests.helpers import run_cin_file


def _run(workdir, source, name='lib_token_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_token_split_core(workdir, use_native):
    """tok_count / tok_get / tok_len: 保留空 token、多字符分隔符、空 delim。"""
    src = '''
import "token.cin"
function main() -> int {
    if (tok_count("a,b,c", ",") != 3) { return 1 }
    if (tok_count("a,,b", ",") != 3) { return 2 }
    if (tok_count(",a,", ",") != 3) { return 3 }
    if (tok_count(",", ",") != 2) { return 4 }
    if (tok_count("", ",") != 1) { return 5 }
    if (tok_count("abc", ",") != 1) { return 6 }
    if (tok_count("a::b::", "::") != 3) { return 7 }
    if (tok_count("a:b", "::") != 1) { return 8 }
    if (tok_count("a,b", "") != 1) { return 9 }
    if (tok_count("", "") != 1) { return 10 }
    if (strcmp(tok_get("a,b,c", ",", 0), "a") != 0) { return 11 }
    if (strcmp(tok_get("a,b,c", ",", 1), "b") != 0) { return 12 }
    if (strcmp(tok_get("a,b,c", ",", 2), "c") != 0) { return 13 }
    if (strcmp(tok_get("a,b,c", ",", 3), "") != 0) { return 14 }
    if (strcmp(tok_get("a,b,c", ",", -1), "") != 0) { return 15 }
    if (strcmp(tok_get("a,,b", ",", 1), "") != 0) { return 16 }
    if (strcmp(tok_get(",a,", ",", 0), "") != 0) { return 17 }
    if (strcmp(tok_get(",a,", ",", 1), "a") != 0) { return 18 }
    if (strcmp(tok_get(",a,", ",", 2), "") != 0) { return 19 }
    if (strcmp(tok_get("", ",", 0), "") != 0) { return 20 }
    if (strcmp(tok_get("a::b", "::", 1), "b") != 0) { return 21 }
    if (strcmp(tok_get("a,b", "", 0), "a,b") != 0) { return 22 }
    if (strcmp(tok_get("a,b", "", 1), "") != 0) { return 23 }
    if (tok_len("a,bb,ccc", ",", 0) != 1) { return 24 }
    if (tok_len("a,bb,ccc", ",", 1) != 2) { return 25 }
    if (tok_len("a,bb,ccc", ",", 2) != 3) { return 26 }
    if (tok_len("a,bb,ccc", ",", 3) != -1) { return 27 }
    if (tok_len("a,bb,ccc", ",", -1) != -1) { return 28 }
    if (tok_len("a,,b", ",", 1) != 0) { return 29 }
    if (tok_len("abc", "", 0) != 3) { return 30 }
    if (tok_len("abc", "", 5) != -1) { return 31 }
    if (tok_len("", ",", 0) != 0) { return 32 }
    if (tok_len("a::b::", "::", 2) != 0) { return 33 }
    // 字节语义: 非 ASCII 分隔符按整串匹配
    string zh = "\\xE4\\xB8\\xAD|\\xE6\\x96\\x87"
    if (tok_count(zh, "|") != 2) { return 34 }
    if (tok_len(zh, "|", 0) != 3) { return 35 }
    if (tok_len(zh, "|", 1) != 3) { return 36 }
    if (strcmp(tok_get(zh, "|", 1), "\\xE6\\x96\\x87") != 0) { return 37 }
    return 0
}'''
    assert _run(workdir, src, use_native=use_native).regs.read(0) == 0


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_token_find_and_ends(workdir, use_native):
    """tok_find / tok_first / tok_last, 含长串 (77 字节)。"""
    long_line = ','.join(c * 2 for c in 'abcdefghijklmnopqrstuvwxyz')
    src = ('''
import "token.cin"
function main() -> int {
    if (tok_find("a,bb,ccc", ",", "bb") != 1) { return 1 }
    if (tok_find("a,bb,ccc", ",", "ccc") != 2) { return 2 }
    if (tok_find("a,bb,ccc", ",", "z") != -1) { return 3 }
    if (tok_find("a,,b", ",", "") != 1) { return 4 }
    if (tok_find("a,bb,ccc", ",", "A") != -1) { return 5 }
    if (tok_find("a,bb,ccc", ",", "a") != 0) { return 6 }
    if (tok_find(",a,b", ",", "") != 0) { return 7 }
    if (strcmp(tok_first("a,bb,ccc", ","), "a") != 0) { return 8 }
    if (strcmp(tok_last("a,bb,ccc", ","), "ccc") != 0) { return 9 }
    if (strcmp(tok_first(",a,", ","), "") != 0) { return 10 }
    if (strcmp(tok_last(",a,", ","), "") != 0) { return 11 }
    if (strcmp(tok_first("abc", ","), "abc") != 0) { return 12 }
    if (strcmp(tok_last("", ","), "") != 0) { return 13 }
    if (strcmp(tok_last("a::b::", "::"), "") != 0) { return 14 }
    if (strcmp(tok_first("a,b", ""), "a,b") != 0) { return 15 }
    if (strcmp(tok_last("a,b", ""), "a,b") != 0) { return 16 }
    string long_line = "''' + long_line + '''"
    if (strlen(long_line) != 77) { return 17 }
    if (tok_count(long_line, ",") != 26) { return 18 }
    if (tok_len(long_line, ",", 0) != 2) { return 19 }
    if (tok_len(long_line, ",", 25) != 2) { return 20 }
    if (tok_len(long_line, ",", 26) != -1) { return 21 }
    if (strcmp(tok_get(long_line, ",", 0), "aa") != 0) { return 22 }
    if (strcmp(tok_get(long_line, ",", 25), "zz") != 0) { return 23 }
    if (strcmp(tok_first(long_line, ","), "aa") != 0) { return 24 }
    if (strcmp(tok_last(long_line, ","), "zz") != 0) { return 25 }
    if (tok_find(long_line, ",", "zz") != 25) { return 26 }
    if (tok_find(long_line, ",", "mm") != 12) { return 27 }
    if (tok_count(long_line, ", ") != 1) { return 28 }
    return 0
}''')
    assert _run(workdir, src, use_native=use_native).regs.read(0) == 0


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_token_words(workdir, use_native):
    """tok_word_count / tok_word_get: 连续空白算一个、忽略首尾、越界返回空串。"""
    long_words = ' '.join(['word'] * 50)      # 249 字节
    src = ('''
import "token.cin"
function main() -> int {
    if (tok_word_count("") != 0) { return 1 }
    if (tok_word_count("   \\t\\n ") != 0) { return 2 }
    if (tok_word_count("one") != 1) { return 3 }
    if (tok_word_count("  one   two  ") != 2) { return 4 }
    if (tok_word_count("a\\tb\\nc\\rd") != 4) { return 5 }
    if (tok_word_count(" \\xE4\\xB8\\xAD\\xE6\\x96\\x87 ") != 1) { return 6 }
    if (strcmp(tok_word_get("", 0), "") != 0) { return 7 }
    if (strcmp(tok_word_get("  one   two  ", 0), "one") != 0) { return 8 }
    if (strcmp(tok_word_get("  one   two  ", 1), "two") != 0) { return 9 }
    if (strcmp(tok_word_get("  one   two  ", 2), "") != 0) { return 10 }
    if (strcmp(tok_word_get("one", -1), "") != 0) { return 11 }
    if (strcmp(tok_word_get("a\\tb\\nc\\rd", 3), "d") != 0) { return 12 }
    if (strcmp(tok_word_get("\\xE4\\xB8\\xAD \\xE6\\x96\\x87", 1), "\\xE6\\x96\\x87") != 0) { return 13 }
    if (strcmp(tok_word_get("   ", 0), "") != 0) { return 14 }
    string long_words = "''' + long_words + '''"
    if (strlen(long_words) != 249) { return 15 }
    if (tok_word_count(long_words) != 50) { return 16 }
    if (strcmp(tok_word_get(long_words, 0), "word") != 0) { return 17 }
    if (strcmp(tok_word_get(long_words, 49), "word") != 0) { return 18 }
    if (strcmp(tok_word_get(long_words, 50), "") != 0) { return 19 }
    return 0
}''')
    assert _run(workdir, src, use_native=use_native).regs.read(0) == 0


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_text_token_prefixes_do_not_clash(workdir, use_native):
    """text.cin 与 token.cin 同时导入: 前缀不同, 全局符号无冲突。"""
    src = '''
import "text.cin"
import "token.cin"
function main() -> int {
    if (strcmp(txt_reverse("ab"), "ba") != 0) { return 1 }
    if (strcmp(tok_get("a,b", ",", 1), "b") != 0) { return 2 }
    if (txt_word_count("a b") != tok_word_count("a b")) { return 3 }
    if (strcmp(txt_title(tok_first("hello world", " ")), "Hello") != 0) { return 4 }
    if (strcmp(txt_replace("a,b", ",", tok_last("x-y", "-")), "ayb") != 0) { return 5 }
    return 0
}'''
    assert _run(workdir, src, use_native=use_native).regs.read(0) == 0
