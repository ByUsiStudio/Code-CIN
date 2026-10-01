"""官方标准库 codec.cin (纯 CIN 编解码与简单密码) 测试。

每个 CIN 用例的 `main()` 返回 0 表示全部通过, 非 0 是具体失败点编号
(编号在各自用例内从 1 起顺序递增, 便于定位到具体断言)。
两条执行路径 (interp / native) 都必须通过。

黄金用例 (base64 / hex / url / rot13 / caesar / xor) 的期望值先用
Python 标准库独立算出再硬编码, 见 `test_golden_values_match_python_stdlib`。

注意: 下面的 CIN 源码里的 `\xFF` / `\x00` 是 **CIN 的字节转义**,
在 Python 源码中写单个反斜杠即可 (测试文件按 UTF-8 原样写出)。
"""

import os

import pytest

from tests.helpers import run_cin_file


def _run(workdir, source, name='lib_codec_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


# Python 侧独立算出的黄金值 (与下面 CIN 用例中硬编码的值逐一对应)。
GOLDEN = {
    'base64': {
        '': '', 'f': 'Zg==', 'fo': 'Zm8=', 'foo': 'Zm9v',
        'foob': 'Zm9vYg==', 'fooba': 'Zm9vYmE=', 'foobar': 'Zm9vYmFy',
        'Man': 'TWFu', 'Ma': 'TWE=', 'M': 'TQ==',
        'Hello, CIN!': 'SGVsbG8sIENJTiE=', '>>?': 'Pj4/',
    },
    'hex': {'AB': '4142', 'abc': '616263', '0': '30', '12345': '3132333435'},
    'url': {'a b&c': 'a%20b%26c', '/': '%2F', ' ': '%20', '=': '%3D',
            '+': '%2B', '~-_.),': '~-_.%29%2C'},
    'rot13': {'Hello, World!': 'Uryyb, Jbeyq!', 'abcXYZ': 'nopKLM'},
    'caesar5': {'Hello, World!': 'Mjqqt, Btwqi!'},
}


HEX_URL_SRC = '''
import "codec.cin"

function main() -> int {
    // ---- 十六进制编码 ----
    if (strcmp(codec_hex_encode("AB"), "4142") != 0) { return 1 }
    if (strcmp(codec_hex_encode(""), "") != 0) { return 2 }
    if (strcmp(codec_hex_encode("abc"), "616263") != 0) { return 3 }
    if (strcmp(codec_hex_encode("0"), "30") != 0) { return 4 }
    if (strcmp(codec_hex_encode("12345"), "3132333435") != 0) { return 5 }
    if (strcmp(codec_hex_encode("Man"), "4d616e") != 0) { return 6 }
    if (strcmp(codec_hex_encode("~"), "7e") != 0) { return 7 }
    if (strcmp(codec_hex_encode("\xFF"), "ff") != 0) { return 8 }
    if (strcmp(codec_hex_encode("\x00"), "00") != 0) { return 9 }
    if (strcmp(codec_hex_encode("\x80\xFF"), "80ff") != 0) { return 10 }

    // ---- 十六进制解码 ----
    if (strcmp(codec_hex_decode("4142"), "AB") != 0) { return 11 }
    if (strcmp(codec_hex_decode(""), "") != 0) { return 12 }
    if (strcmp(codec_hex_decode("616263"), "abc") != 0) { return 13 }
    if (strcmp(codec_hex_decode("4d616e"), "Man") != 0) { return 14 }
    if (strcmp(codec_hex_decode("4D616E"), "Man") != 0) { return 15 }
    if (strcmp(codec_hex_decode("ff"), "\xFF") != 0) { return 16 }
    if (strcmp(codec_hex_decode("fF"), "\xFF") != 0) { return 17 }
    if (strcmp(codec_hex_decode("00"), "\x00") != 0) { return 18 }
    if (codec_hex_decode("ff")[0] != 255) { return 19 }
    if (codec_hex_decode("00")[0] != 0) { return 20 }
    if (codec_hex_decode("80")[0] != 128) { return 21 }
    if (strcmp(codec_hex_decode("00FF80"), "\x00\xFF\x80") != 0) { return 22 }
    // 非法输入 -> 空串
    if (strcmp(codec_hex_decode("4"), "") != 0) { return 23 }
    if (strcmp(codec_hex_decode("414"), "") != 0) { return 24 }
    if (strcmp(codec_hex_decode("zz"), "") != 0) { return 25 }
    if (strcmp(codec_hex_decode("4G"), "") != 0) { return 26 }
    if (strcmp(codec_hex_decode("41 42"), "") != 0) { return 27 }
    if (strcmp(codec_hex_decode("-1"), "") != 0) { return 28 }
    if (strcmp(codec_hex_decode("0x41"), "") != 0) { return 29 }
    // 往返
    if (strcmp(codec_hex_decode(codec_hex_encode("Code CIN v5.6.0")), "Code CIN v5.6.0") != 0) { return 30 }

    // ---- URL 编码 ----
    if (strcmp(codec_url_encode("a b&c"), "a%20b%26c") != 0) { return 31 }
    if (strcmp(codec_url_encode(""), "") != 0) { return 32 }
    if (strcmp(codec_url_encode("abcXYZ019-_.~"), "abcXYZ019-_.~") != 0) { return 33 }
    if (strcmp(codec_url_encode("/"), "%2F") != 0) { return 34 }
    if (strcmp(codec_url_encode(" "), "%20") != 0) { return 35 }
    if (strcmp(codec_url_encode("="), "%3D") != 0) { return 36 }
    if (strcmp(codec_url_encode("+"), "%2B") != 0) { return 37 }
    if (strcmp(codec_url_encode("~-_.),"), "~-_.%29%2C") != 0) { return 38 }
    if (strcmp(codec_url_encode("\xFF"), "%FF") != 0) { return 39 }
    if (strcmp(codec_url_encode("\x00"), "%00") != 0) { return 40 }
    if (strcmp(codec_url_encode("\x80"), "%80") != 0) { return 41 }

    // ---- URL 解码 ----
    if (strcmp(codec_url_decode("a%20b%26c"), "a b&c") != 0) { return 42 }
    if (strcmp(codec_url_decode(""), "") != 0) { return 43 }
    if (strcmp(codec_url_decode("abcXYZ019-_.~"), "abcXYZ019-_.~") != 0) { return 44 }
    if (strcmp(codec_url_decode("a+b"), "a b") != 0) { return 45 }
    if (strcmp(codec_url_decode("a%2Bb"), "a+b") != 0) { return 46 }
    if (strcmp(codec_url_decode("%41%42"), "AB") != 0) { return 47 }
    if (strcmp(codec_url_decode("%7E"), "~") != 0) { return 48 }
    if (codec_url_decode("%ff")[0] != 255) { return 49 }
    if (codec_url_decode("%00")[0] != 0) { return 50 }
    if (codec_url_decode("%80")[0] != 128) { return 51 }
    // 非法 % 序列 -> 空串
    if (strcmp(codec_url_decode("%"), "") != 0) { return 52 }
    if (strcmp(codec_url_decode("%4"), "") != 0) { return 53 }
    if (strcmp(codec_url_decode("%4G"), "") != 0) { return 54 }
    if (strcmp(codec_url_decode("a%2"), "") != 0) { return 55 }
    if (strcmp(codec_url_decode("%2 0"), "") != 0) { return 56 }
    // 往返 (保留字与需要转义的字符)
    if (strcmp(codec_url_decode(codec_url_encode("a=b&c d/e?f#g")), "a=b&c d/e?f#g") != 0) { return 57 }
    if (strcmp(codec_url_decode(codec_url_encode("~-_.!*'()")), "~-_.!*'()") != 0) { return 58 }
    return 0
}
'''


CIPHER_SRC = '''
import "codec.cin"

function main() -> int {
    // ---- ROT13 ----
    if (strcmp(codec_rot13("Hello, World!"), "Uryyb, Jbeyq!") != 0) { return 1 }
    if (strcmp(codec_rot13(""), "") != 0) { return 2 }
    if (strcmp(codec_rot13("abcXYZ"), "nopKLM") != 0) { return 3 }
    if (strcmp(codec_rot13("12345!@#$%"), "12345!@#$%") != 0) { return 4 }
    if (strcmp(codec_rot13("\xFF\x80"), "\xFF\x80") != 0) { return 5 }
    if (strcmp(codec_rot13(codec_rot13("The Quick Brown Fox")), "The Quick Brown Fox") != 0) { return 6 }

    // ---- 凯撒加密 ----
    if (strcmp(codec_caesar("", 7), "") != 0) { return 7 }
    if (strcmp(codec_caesar("abc", 0), "abc") != 0) { return 8 }
    if (strcmp(codec_caesar("abc", 3), "def") != 0) { return 9 }
    if (strcmp(codec_caesar("abc", 26), "abc") != 0) { return 10 }
    if (strcmp(codec_caesar("abc", 29), "def") != 0) { return 11 }
    if (strcmp(codec_caesar("abc", -3), "xyz") != 0) { return 12 }
    if (strcmp(codec_caesar("abc", -29), "xyz") != 0) { return 13 }
    if (strcmp(codec_caesar("xyz", 3), "abc") != 0) { return 14 }
    if (strcmp(codec_caesar("XYZ", -3), "UVW") != 0) { return 15 }
    if (strcmp(codec_caesar("Hello, World!", 5), "Mjqqt, Btwqi!") != 0) { return 16 }
    if (strcmp(codec_caesar("12345!@#$%", 13), "12345!@#$%") != 0) { return 17 }
    if (strcmp(codec_caesar("aA", 1), "bB") != 0) { return 18 }
    if (strcmp(codec_caesar("\xFF", 3), "\xFF") != 0) { return 19 }

    // ---- 凯撒解密 ----
    if (strcmp(codec_decaesar("def", 3), "abc") != 0) { return 20 }
    if (strcmp(codec_decaesar("abc", -3), "def") != 0) { return 21 }
    if (strcmp(codec_decaesar("", 3), "") != 0) { return 22 }
    if (strcmp(codec_decaesar("Uryyb, Jbeyq!", 13), "Hello, World!") != 0) { return 23 }
    if (strcmp(codec_decaesar(codec_caesar("Secret Message 42", 17), 17), "Secret Message 42") != 0) { return 24 }
    if (strcmp(codec_decaesar(codec_caesar("Secret Message 42", -17), -17), "Secret Message 42") != 0) { return 25 }

    // ---- XOR 循环密钥 ----
    if (strcmp(codec_xor_cipher("abc", ""), "abc") != 0) { return 26 }
    if (strcmp(codec_xor_cipher("", "key"), "") != 0) { return 27 }
    if (strcmp(codec_xor_cipher("", ""), "") != 0) { return 28 }
    if (strcmp(codec_xor_cipher("abc", "k"), "\x08\x0A\x09") != 0) { return 29 }
    if (strcmp(codec_xor_cipher("abc", "kk"), "\x08\x0B\x08") != 0) { return 30 }
    if (codec_xor_cipher("abc", "k")[0] != 8) { return 31 }
    if (codec_xor_cipher("abc", "k")[2] != 9) { return 32 }
    if (codec_xor_cipher("\x00\xFF\x80", "k")[0] != 75) { return 33 }
    if (codec_xor_cipher("\x00\xFF\x80", "k")[1] != 148) { return 34 }
    if (codec_xor_cipher("\x00\xFF\x80", "k")[2] != 235) { return 35 }
    if (strcmp(codec_xor_cipher(codec_xor_cipher("Attack at dawn!", "s3cr3t"), "s3cr3t"), "Attack at dawn!") != 0) { return 36 }
    if (strcmp(codec_xor_cipher(codec_xor_cipher("Code CIN", "longer-key"), "longer-key"), "Code CIN") != 0) { return 37 }
    return 0
}
'''


BASE64_SRC = '''
import "codec.cin"

function main() -> int {
    // ---- 编码: RFC 4648 测试向量 + 自行验算的黄金值 ----
    if (strcmp(codec_base64_encode(""), "") != 0) { return 1 }
    if (strcmp(codec_base64_encode("f"), "Zg==") != 0) { return 2 }
    if (strcmp(codec_base64_encode("fo"), "Zm8=") != 0) { return 3 }
    if (strcmp(codec_base64_encode("foo"), "Zm9v") != 0) { return 4 }
    if (strcmp(codec_base64_encode("foob"), "Zm9vYg==") != 0) { return 5 }
    if (strcmp(codec_base64_encode("fooba"), "Zm9vYmE=") != 0) { return 6 }
    if (strcmp(codec_base64_encode("foobar"), "Zm9vYmFy") != 0) { return 7 }
    if (strcmp(codec_base64_encode("Man"), "TWFu") != 0) { return 8 }
    if (strcmp(codec_base64_encode("Ma"), "TWE=") != 0) { return 9 }
    if (strcmp(codec_base64_encode("M"), "TQ==") != 0) { return 10 }
    if (strcmp(codec_base64_encode(">>?"), "Pj4/") != 0) { return 11 }
    if (strcmp(codec_base64_encode("Hello, CIN!"), "SGVsbG8sIENJTiE=") != 0) { return 12 }
    if (strcmp(codec_base64_encode("\x00"), "AA==") != 0) { return 13 }
    if (strcmp(codec_base64_encode("\xFF\xFF\xFF"), "////") != 0) { return 14 }
    if (strcmp(codec_base64_encode("\x80\x81\x82"), "gIGC") != 0) { return 15 }
    if (strcmp(codec_base64_encode("~-_.+/="), "fi1fLisvPQ==") != 0) { return 16 }

    // ---- 解码 (含 = 填充) ----
    if (strcmp(codec_base64_decode(""), "") != 0) { return 17 }
    if (strcmp(codec_base64_decode("TWFu"), "Man") != 0) { return 18 }
    if (strcmp(codec_base64_decode("TWE="), "Ma") != 0) { return 19 }
    if (strcmp(codec_base64_decode("TQ=="), "M") != 0) { return 20 }
    if (strcmp(codec_base64_decode("Zg=="), "f") != 0) { return 21 }
    if (strcmp(codec_base64_decode("Zm8="), "fo") != 0) { return 22 }
    if (strcmp(codec_base64_decode("Zm9vYmFy"), "foobar") != 0) { return 23 }
    if (strcmp(codec_base64_decode("AA=="), "\x00") != 0) { return 24 }
    if (strcmp(codec_base64_decode("////"), "\xFF\xFF\xFF") != 0) { return 25 }
    if (strcmp(codec_base64_decode("gIGC"), "\x80\x81\x82") != 0) { return 26 }
    if (strlen(codec_base64_decode("TQ==")) != 1) { return 27 }
    if (strlen(codec_base64_decode("TWE=")) != 2) { return 28 }
    if (strlen(codec_base64_decode("TWFu")) != 3) { return 29 }
    if (codec_base64_decode("////")[2] != 255) { return 30 }

    // ---- 非法输入 -> 空串 ----
    if (strcmp(codec_base64_decode("TWF"), "") != 0) { return 31 }
    if (strcmp(codec_base64_decode("TWFu="), "") != 0) { return 32 }
    if (strcmp(codec_base64_decode("TW=u"), "") != 0) { return 33 }
    if (strcmp(codec_base64_decode("T==="), "") != 0) { return 34 }
    if (strcmp(codec_base64_decode("===="), "") != 0) { return 35 }
    if (strcmp(codec_base64_decode("!.!."), "") != 0) { return 36 }
    if (strcmp(codec_base64_decode("TW Fu"), "") != 0) { return 37 }
    if (strcmp(codec_base64_decode("TW-u"), "") != 0) { return 38 }
    if (strcmp(codec_base64_decode("A"), "") != 0) { return 39 }
    if (strcmp(codec_base64_decode("AAAAA"), "") != 0) { return 40 }

    // ---- 往返 (边界长度 / 高字节) ----
    if (strcmp(codec_base64_decode(codec_base64_encode("a")), "a") != 0) { return 41 }
    if (strcmp(codec_base64_decode(codec_base64_encode("ab")), "ab") != 0) { return 42 }
    if (strcmp(codec_base64_decode(codec_base64_encode("abc")), "abc") != 0) { return 43 }
    if (strcmp(codec_base64_decode(codec_base64_encode("abcd")), "abcd") != 0) { return 44 }
    if (strcmp(codec_base64_decode(codec_base64_encode("Code CIN v5.6.0")), "Code CIN v5.6.0") != 0) { return 45 }
    if (strcmp(codec_base64_decode(codec_base64_encode("\x00\x01\x02\xFD\xFE\xFF")), "\x00\x01\x02\xFD\xFE\xFF") != 0) { return 46 }
    if (strcmp(codec_base64_decode(codec_base64_encode("\x00\x00\x00")), "\x00\x00\x00") != 0) { return 47 }
    if (codec_base64_decode(codec_base64_encode("\x00\x00\x00"))[0] != 0) { return 48 }
    if (strcmp(codec_base64_decode(codec_base64_encode("~-_.+/=")), "~-_.+/=") != 0) { return 49 }
    return 0
}
'''


RLE_MORSE_SRC = '''
import "codec.cin"

function main() -> int {
    // ---- RLE 编码 ----
    if (strcmp(codec_rle_encode(""), "") != 0) { return 1 }
    if (strcmp(codec_rle_encode("a"), "a") != 0) { return 2 }
    if (strcmp(codec_rle_encode("ab"), "ab") != 0) { return 3 }
    if (strcmp(codec_rle_encode("aa"), "2a") != 0) { return 4 }
    if (strcmp(codec_rle_encode("aaa"), "3a") != 0) { return 5 }
    if (strcmp(codec_rle_encode("aaabbc"), "3a2bc") != 0) { return 6 }
    if (strcmp(codec_rle_encode("aabbaa"), "2a2b2a") != 0) { return 7 }
    if (strcmp(codec_rle_encode("\xFF\xFF"), "2\xFF") != 0) { return 8 }
    if (strcmp(codec_rle_encode("\x00"), "\x00") != 0) { return 9 }
    if (strcmp(codec_rle_encode("\x00\x00"), "2\x00") != 0) { return 10 }
    // 数字字面量必须转义成 1<digit>, 否则无法与计数区分
    if (strcmp(codec_rle_encode("5"), "15") != 0) { return 11 }
    if (strcmp(codec_rle_encode("55"), "25") != 0) { return 12 }
    if (strcmp(codec_rle_encode("555"), "35") != 0) { return 13 }
    if (strcmp(codec_rle_encode("5a5"), "15a15") != 0) { return 14 }
    if (strcmp(codec_rle_encode("1234567890"), "11213141516171819110") != 0) { return 15 }

    // ---- RLE 解码 ----
    if (strcmp(codec_rle_decode(""), "") != 0) { return 16 }
    if (strcmp(codec_rle_decode("a"), "a") != 0) { return 17 }
    if (strcmp(codec_rle_decode("3a2bc"), "aaabbc") != 0) { return 18 }
    if (strcmp(codec_rle_decode("2a2b2a"), "aabbaa") != 0) { return 19 }
    if (strcmp(codec_rle_decode("15"), "5") != 0) { return 20 }
    if (strcmp(codec_rle_decode("25"), "55") != 0) { return 21 }
    if (strcmp(codec_rle_decode("35"), "555") != 0) { return 22 }
    if (strcmp(codec_rle_decode("15a15"), "5a5") != 0) { return 23 }
    if (strcmp(codec_rle_decode("11213141516171819110"), "1234567890") != 0) { return 24 }
    if (strcmp(codec_rle_decode("10a"), "aaaaaaaaaa") != 0) { return 25 }
    if (strlen(codec_rle_decode("12a")) != 12) { return 26 }
    if (codec_rle_decode("2\xFF")[0] != 255) { return 27 }
    // 展开上限 4096 字节
    if (strlen(codec_rle_decode("4096a")) != 4096) { return 28 }
    if (strcmp(codec_rle_decode("4097a"), "") != 0) { return 29 }
    if (strcmp(codec_rle_decode("99999a"), "") != 0) { return 30 }
    // 非法输入 -> 空串
    if (strcmp(codec_rle_decode("3"), "") != 0) { return 31 }
    if (strcmp(codec_rle_decode("3a2"), "") != 0) { return 32 }
    if (strcmp(codec_rle_decode("0a"), "") != 0) { return 33 }
    if (strcmp(codec_rle_decode("01a"), "") != 0) { return 34 }
    if (strcmp(codec_rle_decode("00"), "") != 0) { return 35 }
    if (strcmp(codec_rle_decode("1"), "") != 0) { return 36 }
    if (strcmp(codec_rle_decode("\xFF\xFF"), "") != 0) { return 37 }
    // 往返
    if (strcmp(codec_rle_decode(codec_rle_encode("aaabbc")), "aaabbc") != 0) { return 38 }
    if (strcmp(codec_rle_decode(codec_rle_encode("The quick brown fox")), "The quick brown fox") != 0) { return 39 }
    if (strcmp(codec_rle_decode(codec_rle_encode("111222333")), "111222333") != 0) { return 40 }
    if (strcmp(codec_rle_decode(codec_rle_encode("\x00\x00\x00\xFF")), "\x00\x00\x00\xFF") != 0) { return 41 }

    // ---- 摩尔斯编码 ----
    if (strcmp(codec_morse_encode(""), "") != 0) { return 42 }
    if (strcmp(codec_morse_encode("SOS"), "... --- ...") != 0) { return 43 }
    if (strcmp(codec_morse_encode("sos"), "... --- ...") != 0) { return 44 }
    if (strcmp(codec_morse_encode("HI"), ".... ..") != 0) { return 45 }
    if (strcmp(codec_morse_encode("A"), ".-") != 0) { return 46 }
    if (strcmp(codec_morse_encode("Z"), "--..") != 0) { return 47 }
    if (strcmp(codec_morse_encode("5"), ".....") != 0) { return 48 }
    if (strcmp(codec_morse_encode("0"), "-----") != 0) { return 49 }
    if (strcmp(codec_morse_encode("A B"), ".- / -...") != 0) { return 50 }
    if (strcmp(codec_morse_encode("A  B"), ".- / -...") != 0) { return 51 }
    if (strcmp(codec_morse_encode(" A "), ".-") != 0) { return 52 }
    if (strcmp(codec_morse_encode("A B C"), ".- / -... / -.-.") != 0) { return 53 }
    if (strcmp(codec_morse_encode("HELLO WORLD"), ".... . .-.. .-.. --- / .-- --- .-. .-.. -..") != 0) { return 54 }
    // 不支持的字节跳过, 且不产生多余空格
    if (strcmp(codec_morse_encode("A,B"), ".- -...") != 0) { return 55 }
    if (strcmp(codec_morse_encode("A!B"), ".- -...") != 0) { return 56 }
    if (strcmp(codec_morse_encode("!@#$%^&*()"), "") != 0) { return 57 }
    if (strcmp(codec_morse_encode("\xFF"), "") != 0) { return 58 }

    // ---- 摩尔斯解码 (未知码字跳过) ----
    if (strcmp(codec_morse_decode(""), "") != 0) { return 59 }
    if (strcmp(codec_morse_decode("... --- ..."), "SOS") != 0) { return 60 }
    if (strcmp(codec_morse_decode(".... . .-.. .-.. ---"), "HELLO") != 0) { return 61 }
    if (strcmp(codec_morse_decode(".- / -..."), "A B") != 0) { return 62 }
    if (strcmp(codec_morse_decode(".-"), "A") != 0) { return 63 }
    if (strcmp(codec_morse_decode("-----"), "0") != 0) { return 64 }
    if (strcmp(codec_morse_decode("........"), "") != 0) { return 65 }
    if (strcmp(codec_morse_decode("x y z"), "") != 0) { return 66 }
    if (strcmp(codec_morse_decode(".... . .-.. .-.. --- / .-- --- .-. .-.. -.."), "HELLO WORLD") != 0) { return 67 }
    if (strcmp(codec_morse_decode(codec_morse_encode("SOS HELP")), "SOS HELP") != 0) { return 68 }
    if (strcmp(codec_morse_decode(codec_morse_encode("A1 B2")), "A1 B2") != 0) { return 69 }

    // ---- 字节反转 ----
    if (strcmp(codec_reverse_bytes(""), "") != 0) { return 70 }
    if (strcmp(codec_reverse_bytes("a"), "a") != 0) { return 71 }
    if (strcmp(codec_reverse_bytes("ab"), "ba") != 0) { return 72 }
    if (strcmp(codec_reverse_bytes("abc"), "cba") != 0) { return 73 }
    if (strcmp(codec_reverse_bytes("Hello!"), "!olleH") != 0) { return 74 }
    if (codec_reverse_bytes("\x00\x80\xFF")[0] != 255) { return 75 }
    if (codec_reverse_bytes("\x00\x80\xFF")[2] != 0) { return 76 }
    if (strcmp(codec_reverse_bytes(codec_reverse_bytes("palindrome")), "palindrome") != 0) { return 77 }

    // ---- 单字节访问 ----
    if (strcmp(codec_char_at("abc", 0), "a") != 0) { return 78 }
    if (strcmp(codec_char_at("abc", 2), "c") != 0) { return 79 }
    if (strcmp(codec_char_at("abc", -1), "") != 0) { return 80 }
    if (strcmp(codec_char_at("abc", 3), "") != 0) { return 81 }
    if (strcmp(codec_char_at("abc", 1000), "") != 0) { return 82 }
    if (strcmp(codec_char_at("", 0), "") != 0) { return 83 }
    if (codec_char_at("A", 0)[0] != 65) { return 84 }
    if (codec_char_at("\xFF", 0)[0] != 255) { return 85 }
    if (strcmp(codec_char_at("\x80\x81", 1), "\x81") != 0) { return 86 }
    return 0
}
'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_codec_hex_and_url(workdir, use_native):
    """hex / url 编解码: 黄金值 + 非法输入 + 往返。"""
    cpu = _run(workdir, HEX_URL_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_codec_ciphers(workdir, use_native):
    """rot13 / caesar / decaesar / xor: 黄金值 + 负移位 + 往返。"""
    cpu = _run(workdir, CIPHER_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_codec_base64(workdir, use_native):
    """base64 纯 CIN 实现: RFC 4648 向量 + 填充 + 非法输入 + 往返。"""
    cpu = _run(workdir, BASE64_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_codec_rle_morse_reverse_charat(workdir, use_native):
    """rle / morse / reverse_bytes / char_at: 边界 + 往返。"""
    cpu = _run(workdir, RLE_MORSE_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_codec_long_input(workdir, use_native):
    """128 字节输入在默认 64 KiB 内存下仍能完成各编解码往返。"""
    src = '''
import "codec.cin"

function main() -> int {
    string blob = ""
    for (int k = 0; k < 2; k = k + 1) {
        blob = blob + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789abcdefghijklmnopqrstuvwxyz-_.+!"
    }
    if (strlen(blob) != 128) { return 1 }
    if (strcmp(codec_hex_decode(codec_hex_encode(blob)), blob) != 0) { return 2 }
    if (strcmp(codec_reverse_bytes(codec_reverse_bytes(blob)), blob) != 0) { return 3 }
    if (strcmp(codec_xor_cipher(codec_xor_cipher(blob, "key"), "key"), blob) != 0) { return 4 }
    if (strcmp(codec_rot13(codec_rot13(blob)), blob) != 0) { return 5 }
    if (strcmp(codec_caesar(codec_decaesar(blob, 7), 7), blob) != 0) { return 6 }
    if (strcmp(codec_rle_decode(codec_rle_encode(blob)), blob) != 0) { return 7 }
    if (strlen(codec_hex_encode(blob)) != 256) { return 8 }
    if (strcmp(codec_base64_decode(codec_base64_encode("Man")), "Man") != 0) { return 9 }
    if (strcmp(codec_url_decode(codec_url_encode("a b&c")), "a b&c") != 0) { return 10 }
    return 0
}
'''
    cpu = _run(workdir, src, name='lib_codec_long.cin', use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_codec_importable_with_other_libs(workdir, use_native):
    """与其它官方库同时导入不冲突 (全局符号与函数名)。"""
    src = '''
import "codec.cin"
import "conv.cin"
import "hash.cin"
import "validate.cin"

function main() -> int {
    if (strcmp(codec_hex_encode("AB"), "4142") != 0) { return 1 }
    if (strcmp(codec_base64_encode("Man"), "TWFu") != 0) { return 2 }
    if (strcmp(codec_url_encode("a b"), "a%20b") != 0) { return 3 }
    if (c_hex_digit('f') != 15) { return 4 }
    if (hash_djb2("") != 5381) { return 5 }
    if (val_is_digit('7') != 1) { return 6 }
    if (strcmp(codec_rot13("Uryyb"), "Hello") != 0) { return 7 }
    if (strcmp(codec_hex_decode("4142"), "AB") != 0) { return 8 }
    return 0
}
'''
    cpu = _run(workdir, src, name='lib_codec_together.cin',
               use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0


def test_golden_values_match_python_stdlib():
    """黄金期望值自检: 用 Python 标准库独立复算, 防止期望值被改错。"""
    import base64
    import urllib.parse

    assert base64.b64encode(b'Man').decode() == GOLDEN['base64']['Man']
    assert base64.b64encode(b'Ma').decode() == GOLDEN['base64']['Ma']
    assert base64.b64encode(b'M').decode() == GOLDEN['base64']['M']
    assert base64.b64encode(b'').decode() == GOLDEN['base64']['']
    assert base64.b64encode(b'f').decode() == GOLDEN['base64']['f']
    assert base64.b64encode(b'fo').decode() == GOLDEN['base64']['fo']
    assert base64.b64encode(b'foo').decode() == GOLDEN['base64']['foo']
    assert base64.b64encode(b'foob').decode() == GOLDEN['base64']['foob']
    assert base64.b64encode(b'fooba').decode() == GOLDEN['base64']['fooba']
    assert base64.b64encode(b'foobar').decode() == GOLDEN['base64']['foobar']
    assert base64.b64encode(b'Hello, CIN!').decode() == GOLDEN['base64']['Hello, CIN!']
    assert base64.b64encode(b'>>?').decode() == GOLDEN['base64']['>>?']
    assert base64.b64decode('TWFu') == b'Man'
    assert base64.b64decode('TWE=') == b'Ma'
    assert base64.b64decode('TQ==') == b'M'

    assert b'AB'.hex() == GOLDEN['hex']['AB']
    assert b'abc'.hex() == GOLDEN['hex']['abc']
    assert b'0'.hex() == GOLDEN['hex']['0']
    assert b'12345'.hex() == GOLDEN['hex']['12345']

    for raw, want in GOLDEN['url'].items():
        got = urllib.parse.quote_from_bytes(raw.encode('latin-1'), safe='-_.~')
        assert got == want, (raw, got, want)

    def caesar(s, k):
        out = []
        for ch in s:
            if 'a' <= ch <= 'z':
                out.append(chr((ord(ch) - 97 + k) % 26 + 97))
            elif 'A' <= ch <= 'Z':
                out.append(chr((ord(ch) - 65 + k) % 26 + 65))
            else:
                out.append(ch)
        return ''.join(out)

    for src, want in GOLDEN['rot13'].items():
        assert caesar(src, 13) == want, (src, caesar(src, 13), want)
    for src, want in GOLDEN['caesar5'].items():
        assert caesar(src, 5) == want, (src, caesar(src, 5), want)
    assert caesar('def', -3) == 'abc'
    assert caesar('abc', 29) == 'def'
