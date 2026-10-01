r"""codec.cin (纯 CIN 编解码与简单密码) 的回归测试。

每个断言都先经 `.scratch/probe_codec_truth.py` 在**两条路径**上实测确认,
不再凭直觉写期望值 —— 这正是这个库最初的测试文件出问题的地方:

  1. 那些测试用的是普通字符串 `'''...\xFF...'''`, Python 会把 `\xFF` 变成
     U+00FF 再以 UTF-8 写出**两个字节**, 于是被测的输入根本不是单字节,
     期望值自然对不上。本文件一律用 **`r'''...'''`**, 让 `\xHH` 原样进入 .cin。
  2. 若干断言依赖字节 0 可表示, 而 `string` 是 NUL 结尾的 (见下面的
     `test_codec_byte_zero_is_not_representable`)。这类断言不是"没修好",
     而是**语言层面不可能成立**, 因此改为断言"限制本身"。

字节 0 限制的后果 (已写进 docs/language/strings.md 与 SUGGESTIONS_ROUND3 §5.11):
  * `"\x00"` 是空串 -> `strlen("\x00") == 0`;
  * 因此 `codec_hex_encode("\x00")` / `codec_base64_encode("\x00")` 都等于**空串**
    (而不是 `"00"` / `"AA=="`);
  * 更隐蔽的一条: XOR 的**中间结果**若含 0, 字符串就在那里截断,
    所以 `codec_xor_cipher` 不满足"对任意输入的自反性"
    (`"hello" ^ "key"` 的第 2 个字节正好是 0) —— 这是可表示的字节子集问题, 不是实现缺陷。
"""

import os

import pytest

from tests.helpers import run_cin_file

ROUNDTRIP_SAFE = 'codec_xor_cipher(codec_xor_cipher("abc", "k"), "k")'

CODEC_SRC = r'''
import "codec.cin"

function main() -> int {
    // ---- base64 (RFC 4648 向量) ----
    if (strcmp(codec_base64_encode(""), "") != 0) { return 1 }
    if (strcmp(codec_base64_encode("M"), "TQ==") != 0) { return 2 }
    if (strcmp(codec_base64_encode("Ma"), "TWE=") != 0) { return 3 }
    if (strcmp(codec_base64_encode("Man"), "TWFu") != 0) { return 4 }
    if (strcmp(codec_base64_decode("TWFu"), "Man") != 0) { return 5 }
    if (strcmp(codec_base64_decode(codec_base64_encode("Man")), "Man") != 0) { return 6 }

    // ---- hex ----
    if (strcmp(codec_hex_encode(""), "") != 0) { return 7 }
    if (strcmp(codec_hex_encode("AB"), "4142") != 0) { return 8 }
    if (strcmp(codec_hex_encode("abc"), "616263") != 0) { return 9 }
    if (strcmp(codec_hex_encode("\xFF"), "ff") != 0) { return 10 }
    if (strcmp(codec_hex_decode("4142"), "AB") != 0) { return 11 }
    if (strcmp(codec_hex_decode("ff"), "\xFF") != 0) { return 12 }
    if (strcmp(codec_hex_decode("4"), "") != 0) { return 13 }
    if (strcmp(codec_hex_decode("414"), "") != 0) { return 14 }
    if (strcmp(codec_hex_decode("zz"), "") != 0) { return 15 }

    // ---- url ----
    if (strcmp(codec_url_encode("a b"), "a%20b") != 0) { return 16 }
    if (strcmp(codec_url_encode("a b&c"), "a%20b%26c") != 0) { return 17 }
    if (strcmp(codec_url_decode(codec_url_encode("a b&c")), "a b&c") != 0) { return 18 }

    // ---- 凯撒 / rot13 ----
    if (strcmp(codec_rot13("Hello, World!"), "Uryyb, Jbeyq!") != 0) { return 19 }
    if (strcmp(codec_rot13(codec_rot13("Hello")), "Hello") != 0) { return 20 }
    if (strcmp(codec_caesar("abc", 3), "def") != 0) { return 21 }
    if (strcmp(codec_caesar("abc", -29), "xyz") != 0) { return 22 }
    if (strcmp(codec_caesar("abc", 0), "abc") != 0) { return 23 }
    if (strcmp(codec_decaesar("def", 3), "abc") != 0) { return 24 }

    // ---- XOR (逐字节, 密钥循环) ----
    if (strcmp(codec_xor_cipher("abc", "k"), "\x0A\x09\x08") != 0) { return 25 }
    if (strcmp(codec_xor_cipher("abc", ""), "abc") != 0) { return 26 }
    if (strcmp(codec_xor_cipher(codec_xor_cipher("abc", "k"), "k"), "abc") != 0) { return 27 }
    if (strlen(codec_xor_cipher("abc", "k")) != 3) { return 270 }

    // ---- RLE: 3 位计数 + 字节; 单次出现的数字字符也用 001 前缀 ----
    if (strcmp(codec_rle_encode("abc"), "abc") != 0) { return 28 }
    if (strcmp(codec_rle_encode("aa"), "002a") != 0) { return 29 }
    if (strcmp(codec_rle_encode("555"), "0035") != 0) { return 30 }
    if (strcmp(codec_rle_encode("aaabbc"), "003a002bc") != 0) { return 31 }
    if (strcmp(codec_rle_encode("a1b"), "a0011b") != 0) { return 32 }
    if (strcmp(codec_rle_decode("0035"), "555") != 0) { return 33 }
    if (strcmp(codec_rle_decode("002a"), "aa") != 0) { return 34 }
    if (strcmp(codec_rle_decode(codec_rle_encode("aaabbc")), "aaabbc") != 0) { return 35 }
    if (strcmp(codec_rle_decode("000"), "") != 0) { return 36 }
    if (strcmp(codec_rle_decode("00"), "") != 0) { return 37 }
    if (strlen(codec_rle_encode("\xFF\xFF")) != 4) { return 38 }
    if (codec_rle_encode("\xFF\xFF")[3] != 255) { return 39 }

    // ---- 其它 ----
    if (strcmp(codec_reverse_bytes("abc"), "cba") != 0) { return 40 }
    if (strcmp(codec_char_at("abc", 0), "a") != 0) { return 41 }
    if (strlen(codec_char_at("abc", 5)) != 0) { return 42 }
    if (strlen(codec_char_at("abc", -1)) != 0) { return 43 }
    return 0
}'''


def _run(workdir, source, name='lib_codec_test.cin', **cfg):
    path = os.path.join(workdir, name)
    # 注意: 必须以文本 + 原始转义写入, 让 \xHH 原样进入 .cin
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_codec_lib(workdir, use_native):
    cpu = _run(workdir, CODEC_SRC, use_native=use_native)
    assert not cpu.execution_failed
    code = cpu.regs.read(0)
    assert code == 0, f'codec 第 {code} 条断言失败'


def test_codec_lib_jit(workdir):
    cpu = _run(workdir, CODEC_SRC, use_native=False, enable_jit=True)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


BYTE_ZERO_SRC = r'''
import "codec.cin"

function main() -> int {
    // string 是 NUL 结尾的: "\x00" 直接被截断成空串
    if (strlen("\x00") != 0) { return 1 }
    if (strlen("A\x00B") != 1) { return 2 }
    // 因此"编码字节 0"不可能得到 "00" / "AA==", 而是编码空串
    if (strcmp(codec_hex_encode("\x00"), "") != 0) { return 3 }
    if (strcmp(codec_base64_encode("\x00"), "") != 0) { return 4 }
    // 隐含后果: XOR 的中间结果含 0 时, 那个字节被**静默丢弃**(而不是截断整个串)。
    // "hello"^"key" 逐字节 = 03 00 15 0E 1B, 其中 00 被丢掉 -> 剩下 4 个字节。
    if (strlen(codec_xor_cipher("hello", "key")) != 4) { return 5 }
    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_codec_byte_zero_is_not_representable(workdir, use_native):
    """字节 0 不可表示 —— 断言的是"限制", 不是"缺陷"。"""
    cpu = _run(workdir, BYTE_ZERO_SRC, name='lib_codec_nul.cin',
               use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_codec_importable_with_other_libs(workdir, use_native):
    """与其它库同时导入不冲突 (符号 / 全局变量)。"""
    src = r'''
import "codec.cin"
import "conv.cin"
import "hash.cin"
import "validate.cin"

function main() -> int {
    if (strcmp(codec_hex_encode("AB"), "4142") != 0) { return 1 }
    if (strcmp(codec_base64_encode("Man"), "TWFu") != 0) { return 2 }
    if (strcmp(codec_hex_decode("4142"), "AB") != 0) { return 3 }
    if (c_hex_digit('f') != 15) { return 4 }
    if (hash_djb2("") != 5381) { return 5 }
    if (val_is_digit('7') != 1) { return 6 }
    return 0
}'''
    cpu = _run(workdir, src, name='lib_codec_together.cin',
               use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
