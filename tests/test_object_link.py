"""可重定位对象/链接器测试。"""

import os

import pytest

from codecin.object import (
    ObjectError,
    compile_asm_object,
    compile_cin_object,
    link_inputs,
    link_objects,
    read_object,
    write_archive,
    write_object,
)


def _write(workdir, name, text):
    path = os.path.join(workdir, name)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return path


def _main_calls_helper(workdir, helper='helper'):
    return _write(workdir, 'main.asm', f'''.text
main:
    CALL {helper}
    HALT
''')


def _helper(workdir):
    return _write(workdir, 'lib.asm', '''.text
helper:
    RET
''')


def test_object_roundtrip(workdir):
    path = _write(workdir, 'one.asm', '.text\nmain:\n    HALT\n')
    obj = compile_asm_object(path)
    out = os.path.join(workdir, 'one.co')
    write_object(obj, out)
    loaded = read_object(out)
    assert loaded['labels']['main'] == 0
    assert loaded['instructions'][0][0] == 'HALT'


def test_link_resolves_code_and_data_labels(workdir):
    main = _main_calls_helper(workdir)
    lib = _write(workdir, 'lib.asm', '''.text
helper:
    MOV x0, #msg
    HALT
.data
msg: ASCIZ "hi"
''')
    o1 = compile_asm_object(main)
    o2 = compile_asm_object(lib)
    linked = link_objects([o1, o2])
    assert linked['labels']['helper'] == 2
    # MOV x0, #msg -> 链接后的数据地址
    mov = linked['instructions'][2]
    assert mov[0] == 'MOV'
    assert mov[1][1] == ('imm', linked['data_labels']['msg'])
    assert linked['entry_pc'] == 0


def test_link_resolves_label_arithmetic(workdir):
    main = _write(workdir, 'main.asm', '''.text
main:
    MOV x0, #msg+1
    CALL helper+1
    HALT
''')
    lib = _write(workdir, 'lib.asm', '''.text
helper:
    NOP
    RET
.data
msg: ASCIZ "hi"
''')
    linked = link_objects([compile_asm_object(main),
                           compile_asm_object(lib)])
    assert linked['instructions'][0][1][1] == (
        'imm', linked['data_labels']['msg'] + 1)
    assert linked['instructions'][1][1][0] == (
        'imm', linked['labels']['helper'] + 1)


def test_link_relocates_data_pointer(workdir):
    lib = _write(workdir, 'lib.asm', '''.data
ptr: DQ helper
.text
helper:
    MOV x0, #7
    RET
''')
    o = compile_asm_object(lib)
    linked = link_objects([o])
    data = linked['segments'][0][1]
    at = linked['data_labels']['ptr']
    import struct
    assert struct.unpack_from('<Q', data, at)[0] == linked['labels']['helper']


def test_cin_separate_compile_and_link(workdir):
    """CIN: 无 main 的库对象 + extern 声明的主对象独立编译后链接。"""
    import subprocess

    from codecin import aot
    lib = _write(workdir, 'lib.cin', 'function helper() -> int {\n'
                 '    return 7\n}\n')
    main = _write(workdir, 'main.cin', '''extern function helper() -> int

function main() -> int {
    int x = helper()
    println(int_to_str(x))
    return 0
}
''')
    o_main = compile_cin_object(main)
    o_lib = compile_cin_object(lib)
    assert o_main['has_entry'] is True
    assert o_lib['has_entry'] is False
    # 库对象在前也应被链接器重排, 保证 CIN 主对象数据基址为 0。
    linked = link_objects([o_lib, o_main])
    out = os.path.join(workdir, 'cin_linked.exe')
    built = aot.build_from_ir(
        linked['instructions'], linked['labels'], linked['data_labels'],
        linked['segments'], linked['entry_pc'], linked['mem_size'],
        linked['sp'], linked['heap_base'], out, program_name='linked')
    r = subprocess.run([built], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == '7'


def test_cin_library_data_is_relocated(workdir):
    """CIN 库对象的字符串数据段会随对象数据基址重定位。"""
    import subprocess

    from codecin import aot
    lib = _write(workdir, 'lib.cin', '''function helper() -> int {
    string s = "lib!"
    return strlen(s)
}
''')
    main = _write(workdir, 'main.cin', '''extern function helper() -> int

function main() -> int {
    println("v=" + int_to_str(helper()))
    return 0
}
''')
    linked = link_objects([compile_cin_object(main),
                           compile_cin_object(lib)])
    out = os.path.join(workdir, 'cin_data.exe')
    built = aot.build_from_ir(
        linked['instructions'], linked['labels'], linked['data_labels'],
        linked['segments'], linked['entry_pc'], linked['mem_size'],
        linked['sp'], linked['heap_base'], out, program_name='linked')
    r = subprocess.run([built], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'v=4'


def test_cin_extern_global_link(workdir):
    """extern 全局变量: 定义在库对象, 主对象通过重定位访问。"""
    import subprocess

    from codecin import aot
    lib = _write(workdir, 'lib.cin', '''int counter = 5

function get() -> int {
    return counter
}

function setv(int v) {
    counter = v
}
''')
    main = _write(workdir, 'main.cin', '''extern int counter
extern function get() -> int
extern function setv(int v)

function main() -> int {
    setv(9)
    println(int_to_str(get()))
    return 0
}
''')
    linked = link_objects([compile_cin_object(lib),
                           compile_cin_object(main)])
    assert 'global_counter' in linked['data_labels']
    out = os.path.join(workdir, 'cin_global.exe')
    built = aot.build_from_ir(
        linked['instructions'], linked['labels'], linked['data_labels'],
        linked['segments'], linked['entry_pc'], linked['mem_size'],
        linked['sp'], linked['heap_base'], out, program_name='linked')
    r = subprocess.run([built], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == '9'


def test_cin_struct_global_data_relocation(workdir):
    """库对象中 struct 全局的指针槽也要随数据基址重定位。"""
    import subprocess

    from codecin import aot
    lib = _write(workdir, 'lib.cin', '''struct P { int x }

P p

function init() {
    p.x = 7
}

function getx() -> int {
    return p.x
}
''')
    main = _write(workdir, 'main.cin', '''extern function init()
extern function getx() -> int

function main() -> int {
    init()
    println("v=" + int_to_str(getx()))
    return 0
}
''')
    linked = link_objects([compile_cin_object(lib),
                           compile_cin_object(main)])
    out = os.path.join(workdir, 'cin_struct.exe')
    built = aot.build_from_ir(
        linked['instructions'], linked['labels'], linked['data_labels'],
        linked['segments'], linked['entry_pc'], linked['mem_size'],
        linked['sp'], linked['heap_base'], out, program_name='linked')
    r = subprocess.run([built], capture_output=True, text=True,
                       encoding='utf-8', errors='replace', timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == 'v=7'


def test_archive_selects_only_needed_members(workdir):
    """静态库: 只拉取能解决未定义符号的成员。"""
    main = _main_calls_helper(workdir)
    lib = _helper(workdir)
    lib2 = _write(workdir, 'lib2.asm', '''.text
other:
    MOV x0, #99
    RET
''')
    o_main = compile_asm_object(main)
    o_lib = compile_asm_object(lib)
    o_lib2 = compile_asm_object(lib2)
    main_co = os.path.join(workdir, 'main.co')
    write_object(o_main, main_co)
    archive = os.path.join(workdir, 'lib.colib')
    write_archive([o_lib, o_lib2], archive)
    linked = link_inputs([main_co, archive])
    assert 'helper' in linked['labels']
    assert 'other' not in linked['labels']


def test_gc_sections_removes_unreachable_code(workdir):
    """--gc-sections: 从入口可达性删除未调用函数。"""
    path = _write(workdir, 'gc.asm', '''.text
main:
    CALL f1
    HALT
f1:
    MOV x0, #42
    RET
dead:
    MOV x0, #99
    RET
''')
    obj = compile_asm_object(path)
    linked = link_objects([obj], gc_sections=True)
    assert 'dead' not in linked['labels']
    assert 'f1' in linked['labels']
    assert len(linked['instructions']) == 4


def test_gc_sections_skips_dynamic_call(workdir):
    path = _write(workdir, 'dyn.asm', '''.text
main:
    CALL x5
    HALT
dead:
    RET
''')
    obj = compile_asm_object(path)
    linked = link_objects([obj], gc_sections=True)
    # 动态调用目标未知, 保守保留全部代码。
    assert 'dead' in linked['labels']


def test_link_rejects_duplicate_symbol(workdir):
    lib = _helper(workdir)
    o1 = compile_asm_object(lib)
    o2 = compile_asm_object(lib)
    with pytest.raises(ObjectError):
        link_objects([o1, o2])


def test_link_rejects_undefined_symbol(workdir):
    main = _main_calls_helper(workdir, helper='missing')
    o1 = compile_asm_object(main)
    with pytest.raises(ObjectError):
        link_objects([o1])


def test_link_rejects_bad_object_file(workdir):
    path = _write(workdir, 'bad.co', '{"not": "an object"}')
    with pytest.raises(ObjectError):
        read_object(path)
