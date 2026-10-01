"""官方标准库 path.cin (纯字符串路径工具) 测试。

覆盖: 每个公开函数至少一个用例 + 边界 (空串 / 只有分隔符 / "." / ".." /
结尾带分隔符 / 双分隔符 / Windows 盘符 / 越界下标)。
黄金用例: path_str_basename("/a/b/c.txt") = "c.txt",
          path_normalize("a//b/./c/../d") = "a/b/d"。

命名说明: path_join / path_basename / path_dirname 是**宿主能力内建名**, CIN 编译器
优先分派内建 (--no-native 下直接报 "host builtins ... require the native Go runtime"),
同名用户函数永远不会被调用, 因此纯字符串版本命名为
path_str_join / path_str_basename / path_str_dirname。

CIN 侧 main() 返回 0 表示全部通过, 非 0 为失败点编号。
字符串断言一律用 strcmp (CIN 的 == 比较的是指针)。
同一份用例分别跑解释路径 (interp) 与 Go 原生 VM (native)。
"""

import os

import pytest

from tests.helpers import run_cin_file


def _run(workdir, source, name='lib_path_test.cin', **cfg):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(source)
    return run_cin_file(path, **cfg)


PATH_SRC = '''
import "path.cin"

function main() -> int {
    // ---- path_sep_posix / path_sep_win / path_is_sep / path_max_parts ----
    if (strcmp(path_sep_posix(), "/") != 0) { return 1 }
    if (strlen(path_sep_win()) != 1) { return 2 }
    if (path_sep_win()[0] != 92) { return 3 }
    if (path_is_sep(47) != 1) { return 4 }
    if (path_is_sep(92) != 1) { return 5 }
    if (path_is_sep(65) != 0) { return 6 }
    if (path_is_sep(0) != 0) { return 7 }
    if (path_max_parts() != 128) { return 8 }

    // ---- path_str_basename ----
    if (strcmp(path_str_basename("/a/b/c.txt"), "c.txt") != 0) { return 9 }
    if (strcmp(path_str_basename("a/b"), "b") != 0) { return 10 }
    if (strcmp(path_str_basename("a"), "a") != 0) { return 11 }
    if (strcmp(path_str_basename(""), "") != 0) { return 12 }
    if (strcmp(path_str_basename("/"), "") != 0) { return 13 }
    if (strcmp(path_str_basename("a/b/"), "b") != 0) { return 14 }
    if (strcmp(path_str_basename("a//b"), "b") != 0) { return 15 }
    if (strcmp(path_str_basename("C:\\\\x\\\\y.txt"), "y.txt") != 0) { return 16 }
    if (strcmp(path_str_basename("."), ".") != 0) { return 17 }
    if (strcmp(path_str_basename(".."), "..") != 0) { return 18 }

    // ---- path_str_dirname ----
    if (strcmp(path_str_dirname("/a/b/c.txt"), "/a/b") != 0) { return 19 }
    if (strcmp(path_str_dirname("a/b"), "a") != 0) { return 20 }
    if (strcmp(path_str_dirname("a"), "") != 0) { return 21 }
    if (strcmp(path_str_dirname(""), "") != 0) { return 22 }
    if (strcmp(path_str_dirname("/"), "") != 0) { return 23 }
    if (strcmp(path_str_dirname("/a"), "/") != 0) { return 24 }
    if (strcmp(path_str_dirname("a/b/"), "a") != 0) { return 25 }
    if (strcmp(path_str_dirname("C:\\\\a\\\\b"), "C:/a") != 0) { return 26 }

    // ---- path_ext ----
    if (strcmp(path_ext("/a/b/c.txt"), ".txt") != 0) { return 27 }
    if (strcmp(path_ext("a.tar.gz"), ".gz") != 0) { return 28 }
    if (strcmp(path_ext("a"), "") != 0) { return 29 }
    if (strcmp(path_ext(""), "") != 0) { return 30 }
    if (strcmp(path_ext("a/b/"), "") != 0) { return 31 }
    if (strcmp(path_ext(".gitignore"), "") != 0) { return 32 }
    if (strcmp(path_ext("a."), "") != 0) { return 33 }
    if (strcmp(path_ext("."), "") != 0) { return 34 }
    if (strcmp(path_ext(".."), "") != 0) { return 35 }
    if (strcmp(path_ext("dir.d/file"), "") != 0) { return 36 }

    // ---- path_stem ----
    if (strcmp(path_stem("a/b/c.txt"), "c") != 0) { return 37 }
    if (strcmp(path_stem("a.tar.gz"), "a.tar") != 0) { return 38 }
    if (strcmp(path_stem("a/b"), "b") != 0) { return 39 }
    if (strcmp(path_stem(""), "") != 0) { return 40 }
    if (strcmp(path_stem("a/b/"), "b") != 0) { return 41 }
    if (strcmp(path_stem(".gitignore"), ".gitignore") != 0) { return 42 }
    if (strcmp(path_stem("a."), "a") != 0) { return 43 }
    if (strcmp(path_stem("."), ".") != 0) { return 44 }
    if (strcmp(path_stem(".."), "..") != 0) { return 45 }

    // ---- path_str_join ----
    if (strcmp(path_str_join("a", "b"), "a/b") != 0) { return 46 }
    if (strcmp(path_str_join("a/", "b"), "a/b") != 0) { return 47 }
    if (strcmp(path_str_join("a\\\\", "b"), "a\\\\b") != 0) { return 48 }
    if (strcmp(path_str_join("", "b"), "b") != 0) { return 49 }
    if (strcmp(path_str_join("a", ""), "a") != 0) { return 50 }
    if (strcmp(path_str_join("", ""), "") != 0) { return 51 }
    if (strcmp(path_str_join("a", "/b"), "/b") != 0) { return 52 }
    if (strcmp(path_str_join("a/b", "c/d"), "a/b/c/d") != 0) { return 53 }
    if (strcmp(path_str_join("/", "b"), "/b") != 0) { return 54 }
    if (strcmp(path_str_join("a", "C:\\\\x"), "C:\\\\x") != 0) { return 55 }

    // ---- path_normalize (字符串层面折叠, 不访问文件系统) ----
    if (strcmp(path_normalize("a//b/./c/../d"), "a/b/d") != 0) { return 56 }
    if (strcmp(path_normalize(""), ".") != 0) { return 57 }
    if (strcmp(path_normalize("."), ".") != 0) { return 58 }
    if (strcmp(path_normalize("./"), ".") != 0) { return 59 }
    if (strcmp(path_normalize("/"), "/") != 0) { return 60 }
    if (strcmp(path_normalize("///"), "/") != 0) { return 61 }
    if (strcmp(path_normalize("a//b///c"), "a/b/c") != 0) { return 62 }
    if (strcmp(path_normalize("a/b/"), "a/b") != 0) { return 63 }
    if (strcmp(path_normalize("//a/b/"), "/a/b") != 0) { return 64 }
    if (strcmp(path_normalize("a/b/../c"), "a/c") != 0) { return 65 }
    if (strcmp(path_normalize("a/b/.."), "a") != 0) { return 66 }
    if (strcmp(path_normalize("../a"), "../a") != 0) { return 67 }
    if (strcmp(path_normalize("../../a"), "../../a") != 0) { return 68 }
    if (strcmp(path_normalize("a/../../b"), "../b") != 0) { return 69 }
    if (strcmp(path_normalize("/../a"), "/a") != 0) { return 70 }
    if (strcmp(path_normalize("/.."), "/") != 0) { return 71 }
    if (strcmp(path_normalize("a/.."), ".") != 0) { return 72 }
    if (strcmp(path_normalize("a\\\\b\\\\..\\\\c"), "a/c") != 0) { return 73 }
    if (strcmp(path_normalize("C:\\\\a\\\\..\\\\b"), "C:/b") != 0) { return 74 }
    if (strcmp(path_normalize("./a/./b/."), "a/b") != 0) { return 75 }

    // ---- path_is_absolute ----
    if (path_is_absolute("") != 0) { return 76 }
    if (path_is_absolute("/") != 1) { return 77 }
    if (path_is_absolute("/a/b") != 1) { return 78 }
    if (path_is_absolute("a/b") != 0) { return 79 }
    if (path_is_absolute(".") != 0) { return 80 }
    if (path_is_absolute("..") != 0) { return 81 }
    if (path_is_absolute("C:\\\\a") != 1) { return 82 }
    if (path_is_absolute("C:/a") != 1) { return 83 }
    if (path_is_absolute("C:") != 1) { return 84 }
    if (path_is_absolute("\\\\srv\\\\share") != 1) { return 85 }
    if (path_is_absolute("a:") != 1) { return 86 }

    // ---- path_trim_sep ----
    if (strcmp(path_trim_sep("/a/b/"), "a/b") != 0) { return 87 }
    if (strcmp(path_trim_sep("a"), "a") != 0) { return 88 }
    if (strcmp(path_trim_sep(""), "") != 0) { return 89 }
    if (strcmp(path_trim_sep("/"), "") != 0) { return 90 }
    if (strcmp(path_trim_sep("///"), "") != 0) { return 91 }
    if (strcmp(path_trim_sep("\\\\a\\\\"), "a") != 0) { return 92 }
    if (strcmp(path_trim_sep("a/b"), "a/b") != 0) { return 93 }

    // ---- path_split_count / path_split_get ----
    if (path_split_count("a/b/c") != 3) { return 94 }
    if (path_split_count("") != 0) { return 95 }
    if (path_split_count("/") != 0) { return 96 }
    if (path_split_count("///") != 0) { return 97 }
    if (path_split_count("a") != 1) { return 98 }
    if (path_split_count("a//b") != 2) { return 99 }
    if (path_split_count("/a/b/") != 2) { return 100 }
    if (path_split_count("a\\\\b\\\\c") != 3) { return 101 }
    if (strcmp(path_split_get("a/b/c", 0), "a") != 0) { return 102 }
    if (strcmp(path_split_get("a/b/c", 1), "b") != 0) { return 103 }
    if (strcmp(path_split_get("a/b/c", 2), "c") != 0) { return 104 }
    if (strcmp(path_split_get("a/b/c", 3), "") != 0) { return 105 }
    if (strcmp(path_split_get("a/b/c", -1), "") != 0) { return 106 }
    if (strcmp(path_split_get("", 0), "") != 0) { return 107 }
    if (strcmp(path_split_get("/a/b/", 1), "b") != 0) { return 108 }

    // ---- path_common_prefix ----
    if (strcmp(path_common_prefix("/a/b/c", "/a/b/d"), "/a/b") != 0) { return 109 }
    if (strcmp(path_common_prefix("/a/b", "/a/b"), "/a/b") != 0) { return 110 }
    if (strcmp(path_common_prefix("a/b", "a"), "a") != 0) { return 111 }
    if (strcmp(path_common_prefix("x/y", "z/y"), "") != 0) { return 112 }
    if (strcmp(path_common_prefix("foo", "foobar"), "") != 0) { return 113 }
    if (strcmp(path_common_prefix("a/b/c", "a/b/c/d/e"), "a/b/c") != 0) { return 114 }
    if (strcmp(path_common_prefix("/a", "a"), "") != 0) { return 115 }
    if (strcmp(path_common_prefix("", "a"), "") != 0) { return 116 }
    if (strcmp(path_common_prefix("/", "/a"), "/") != 0) { return 117 }
    if (strcmp(path_common_prefix("C:\\\\a\\\\b", "C:\\\\a\\\\c"), "C:/a") != 0) { return 118 }

    // ---- path_within ----
    if (path_within("/a/b", "/a/b/c.txt") != 1) { return 119 }
    if (path_within("/a/b", "/a/b") != 1) { return 120 }
    if (path_within("/a/b", "/a/bc") != 0) { return 121 }
    if (path_within("/a/b", "/a") != 0) { return 122 }
    if (path_within("/a/b", "/x/y") != 0) { return 123 }
    if (path_within("", "a/b") != 0) { return 124 }
    if (path_within("/", "/a/b") != 1) { return 125 }
    if (path_within("/", "a/b") != 0) { return 126 }
    if (path_within("a", "a/b") != 1) { return 127 }
    if (path_within("/a/b/c", "/a/b/c/d") != 1) { return 128 }

    // ---- path_change_ext ----
    if (strcmp(path_change_ext("a/b/c.txt", "md"), "a/b/c.md") != 0) { return 129 }
    if (strcmp(path_change_ext("a/b/c.txt", ".md"), "a/b/c.md") != 0) { return 130 }
    if (strcmp(path_change_ext("a/b/c.txt", ""), "a/b/c") != 0) { return 131 }
    if (strcmp(path_change_ext("a", "txt"), "a.txt") != 0) { return 132 }
    if (strcmp(path_change_ext("a.tar.gz", "zip"), "a.tar.zip") != 0) { return 133 }
    if (strcmp(path_change_ext("", "txt"), ".txt") != 0) { return 134 }
    if (strcmp(path_change_ext("/a/b/", "txt"), "/a/b.txt") != 0) { return 135 }
    if (strcmp(path_change_ext(".gitignore", "txt"), ".gitignore.txt") != 0) { return 136 }
    if (strcmp(path_change_ext(".", "txt"), ".") != 0) { return 137 }
    if (strcmp(path_change_ext("..", "txt"), "..") != 0) { return 138 }

    // ---- 组合使用: 规范化后取各段 ----
    string n = path_normalize("/srv/www/../www/./site//index.html")
    if (strcmp(n, "/srv/www/site/index.html") != 0) { return 139 }
    if (strcmp(path_ext(n), ".html") != 0) { return 140 }
    if (strcmp(path_stem(n), "index") != 0) { return 141 }
    if (strcmp(path_str_basename(n), "index.html") != 0) { return 142 }
    if (strcmp(path_str_dirname(n), "/srv/www/site") != 0) { return 143 }
    if (path_within("/srv/www", n) != 1) { return 144 }
    if (path_split_count(n) != 4) { return 145 }
    if (strcmp(path_change_ext(n, "htm"), "/srv/www/site/index.htm") != 0) { return 146 }

    return 0
}'''


@pytest.mark.parametrize('use_native', (False, True), ids=('interp', 'native'))
def test_path_lib(workdir, use_native):
    cpu = _run(workdir, PATH_SRC, use_native=use_native)
    assert not cpu.execution_failed
    assert cpu.regs.read(0) == 0
