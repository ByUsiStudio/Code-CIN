"""内置标准库清单 (stdlib manifest): 哪些库可用于哪些执行路径。

``codecin/lib/`` 下的官方标准库按**执行路径要求**分三类:

  * 纯 CIN          —— 只调用语言内建, 原生 / AOT 全部行为一致;
  * 兼容层 (C/C++/Go) —— 纯 CIN 实现的三语言标准库兼容层 (cstd/cppstd/gostd),
    让 C、C++、Go 程序员以惯用名操作 CIN; 除标注的原生依赖外全路径可用;
  * 需原生运行时     —— 调用了宿主能力内建 (音频/GUI/文件/网络/键盘/Termux...),
    沙箱模式下被拦截。

分类由源码扫描得出 (是否引用 HOST_BUILTINS 内建名), 与库文件头注释保持
单一事实来源, 避免文档漂移。对外接口::

    library_report()       -> List[Dict]  (name/category/note/desc)
    format_library_report() -> 多行纯文本表格 (无 ANSI, 可重定向)
"""

import os
import re
from typing import Dict, List

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib')

#: C/C++/Go 三语言兼容层库 (纯 CIN, 语义对齐三语言标准库的惯用名)。
COMPAT_LIBS = {
    'cstd.cin': 'C 兼容层',
    'cppstd.cin': 'C++ 兼容层',
    'gostd.cin': 'Go 兼容层',
}

#: 分类文案。
CAT_PURE = '纯 CIN'
CAT_COMPAT = '兼容层'
CAT_NATIVE = '需原生运行时'


def _first_comment(text: str) -> str:
    """返回首个 `//` 注释行的内容 (库的一句话描述)。"""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('//'):
            return s[2:].strip()
        if s:                                # 先出现的是代码: 无头注释
            return ''
    return ''


def _clean_desc(desc: str) -> str:
    """去掉头注释里的通用前缀与自引用路径, 留下 "xxx (模块名)" 式短描述。"""
    for prefix in ('Code CIN 官方标准库:', 'Code CIN 纯 CIN ', 'Code CIN '):
        if desc.startswith(prefix):
            desc = desc[len(prefix):].strip()
            break
    # 去掉尾部 "(codecin/lib/x.cin)" 自引用
    return re.sub(r'\s*\(codecin/lib/[^)]*\)\s*$', '', desc, flags=re.I).strip()


def library_report() -> List[Dict[str, str]]:
    """扫描内置标准库目录, 返回按名称排序的清单。

    每项字段: ``name`` (文件名) / ``category`` (三类之一) / ``note``
    (兼容层库的原生依赖标注或空) / ``desc`` (头注释一句话描述)。
    """
    # 惰性导入: HOST_BUILTINS 来自编译器模块, 只在真正生成清单时才需要。
    from .cin import HOST_BUILTINS

    names = sorted(n for n in os.listdir(LIB_DIR) if n.endswith('.cin'))
    report: List[Dict[str, str]] = []
    for name in names:
        path = os.path.join(LIB_DIR, name)
        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError:
            continue

        used = [b for b in sorted(HOST_BUILTINS)
                if re.search(r'\b%s\s*\(' % re.escape(b), text)]
        if name in COMPAT_LIBS:
            category = CAT_COMPAT
            note = ('go_os_args_* 依赖原生'
                    if used else '')
        elif used:
            category = CAT_NATIVE
            note = f'{len(used)} 个宿主内建'
        else:
            category = CAT_PURE
            note = ''
        report.append({
            'name': name,
            'category': category,
            'note': note,
            'desc': _first_comment(text),
        })
    return report


def format_library_report() -> str:
    """把清单渲染成无 ANSI 的对齐文本 (与 --build-info 的输出风格一致)。"""
    rows = library_report()
    w_name = max([len(r['name']) for r in rows] + [len('模块')])
    w_cat = max([len(r['category']) for r in rows] + [len('类别')])
    # 中文描述按"显示宽度"对齐太复杂, 简单用字符数即可 (末列无需对齐)
    lines = [
        "Code CIN 内置标准库清单",
        "",
        f"  {'模块'.ljust(w_name)}  {'类别'.ljust(w_cat)}  说明",
        f"  {'-' * w_name}  {'-' * w_cat}  ----",
    ]
    for r in rows:
        note = f" [{r['note']}]" if r['note'] else ''
        desc = _clean_desc(r['desc']) + note
        lines.append(f"  {r['name'].ljust(w_name)}  "
                     f"{r['category'].ljust(w_cat)}  {desc}")
    lines += [
        "",
        "  类别说明:",
        f"    {CAT_PURE}   —— 原生/AOT 全路径行为一致",
        f"    {CAT_COMPAT} —— C/C++/Go 标准库兼容层 (纯 CIN; 兼容层内容见库头注释)",
        f"    {CAT_NATIVE} —— 调用宿主能力内建 (--sandbox 下被拦截)",
    ]
    return '\n'.join(lines)
