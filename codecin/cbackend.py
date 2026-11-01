"""AOT 后端: 把 UCBC 指令翻译成 C, 再交给 GCC/Clang 生成机器码。

不再经过 Go: 每条 UCBC 指令展开为一条 C 语句 (寄存器数组 + 显式标志位 +
扁平内存), 由 C 编译器做寄存器分配和指令选择。产物是独立可执行文件,
可用 :mod:`codecin.toolchain` 交叉编译到

* ``x86_64-windows`` (PE)
* ``x86_64-linux``  (ELF)
* ``aarch64-linux`` (ELF)

当前实现覆盖整数运算/比较跳转/内存/栈/调用返回/``OUT`` 与少量核心
syscall; 未实现的指令或 syscall 会在运行期以明确信息退出 (码 70), 不会
静默产生错误结果。
"""

import os
import struct
import subprocess
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import toolchain
from .isa import Constants

__all__ = ['CBackendError', 'compile_instructions', 'emit_c', 'supports',
           'c_target']

Instruction = Tuple[str, List[Tuple[Any, ...]]]
Segments = Sequence[Tuple[int, bytes]]

#: 已实现的指令助记符。
SUPPORTED_OPS = frozenset({
    'MOV', 'ADD', 'SUB', 'MUL', 'DIV', 'AND', 'OR', 'XOR', 'SHL', 'SHR',
    'ASR', 'ROR', 'INC', 'DEC', 'MVN', 'CMP',
    'ADDI', 'LSL', 'LSR', 'XORI', 'ORI', 'ANDI',
    'PUSH', 'POP', 'CALL', 'BL', 'RET', 'HALT',
    'LOAD', 'LW', 'LD', 'LB', 'LH', 'LDR',
    'STORE', 'SW', 'STR', 'SD', 'SB', 'SH',
    'JMP', 'JZ', 'JE', 'JNZ', 'JL', 'JG',
    'B', 'CBZ', 'CBNZ', 'OUT', 'SYS',
})

#: 已实现的 SYS 功能号。
SUPPORTED_SYS = frozenset(set(range(0, 39)) | set(range(51, 59))
                          | set(range(61, 68)) | {137, 147, 148, 149, 150,
                                                  157})


class CBackendError(RuntimeError):
    """C 后端无法生成/编译。"""


#: 运行期未实现时的退出码。
UNSUPPORTED_EXIT = 70

#: ARM64 条件码 -> C 运行时编号 (与 isa.Cond / VM 的 condition() 一致)。
_COND_CODES = {
    'EQ': 0, 'NE': 1, 'CS': 2, 'HS': 2, 'CC': 3, 'LO': 3, 'MI': 4, 'PL': 5,
    'VS': 6, 'VC': 7, 'HI': 8, 'LS': 9, 'GE': 10, 'LT': 11, 'GT': 12,
    'LE': 13, 'AL': 14, 'NV': 15,
}

_RUNTIME = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <sys/stat.h>
#include <dirent.h>
#ifdef _WIN32
#include <direct.h>
#else
#include <unistd.h>
#endif

#define CODECIN_UNSUPPORTED 70

static uint64_t R[33];          /* 0..31 通用, 32 = SP */
static uint8_t *MEM;
static uint64_t MEMSZ;
static int ZF, NF, CF, VF;
static uint64_t HEAPBASE;
static uint64_t SYSPTR;
static const char *ERRMSG;
static FILE *OUTP;

static void rt_fail(const char *msg) { if (!ERRMSG) ERRMSG = msg; }

static uint64_t rt_reg(int r) { return R[r]; }
static void rt_set(int r, uint64_t v) { R[r] = v; }

static uint64_t rt_add(uint64_t a, uint64_t b) { return a + b; }
static uint64_t rt_sub(uint64_t a, uint64_t b) { return a - b; }
static uint64_t rt_mul(uint64_t a, uint64_t b) { return a * b; }
static uint64_t rt_sdiv(uint64_t a, uint64_t b) {
    if (!b) { rt_fail("Division by zero"); return 0; }
    return (uint64_t)((int64_t)a / (int64_t)b);
}
static uint64_t rt_smod(uint64_t a, uint64_t b) {
    if (!b) { rt_fail("Division by zero"); return 0; }
    return (uint64_t)((int64_t)a % (int64_t)b);
}
static uint64_t rt_shl(uint64_t a, uint64_t b) { return a << (b & 63); }
static uint64_t rt_shr(uint64_t a, uint64_t b) { return a >> (b & 63); }
static uint64_t rt_asr(uint64_t a, uint64_t b) {
    return (uint64_t)((int64_t)a >> (b & 63));
}
static uint64_t rt_ror(uint64_t a, uint64_t b) {
    unsigned s = (unsigned)(b & 63);
    return s ? ((a >> s) | (a << (64 - s))) : a;
}

/* NZCV 与 VM 的 setFlagsSub 完全一致 */
static void rt_cmp(uint64_t a, uint64_t b) {
    uint64_t res = a - b;
    int64_t sa = (int64_t)a, sb = (int64_t)b, sr = sa - sb;
    ZF = (res == 0);
    NF = (res >> 63) != 0;
    CF = (a >= b);
    VF = (sr > INT64_MAX || sr < INT64_MIN);
}

static int rt_bounds(uint64_t addr, uint64_t width) {
    if (addr > MEMSZ || width > MEMSZ - addr) {
        rt_fail("Memory access out of bounds");
        return 0;
    }
    return 1;
}

static int rt_load(int rd, uint64_t addr, int width, int sign) {
    uint64_t v = 0;
    if (!rt_bounds(addr, (uint64_t)width)) return 0;
    memcpy(&v, MEM + addr, (size_t)width);
    if (sign) {
        if (width == 1 && (v & 0x80)) v |= ~(uint64_t)0xFF;
        if (width == 2 && (v & 0x8000)) v |= ~(uint64_t)0xFFFF;
        if (width == 4 && (v & 0x80000000ULL)) v |= ~(uint64_t)0xFFFFFFFFULL;
    }
    R[rd] = v;
    return 1;
}

static int rt_store(uint64_t v, uint64_t addr, int width) {
    if (!rt_bounds(addr, (uint64_t)width)) return 0;
    memcpy(MEM + addr, &v, (size_t)width);
    return 1;
}

static int rt_push(uint64_t v) {
    uint64_t sp = R[32] - 8;
    R[32] = sp;
    if (!rt_bounds(sp, 8)) return 0;
    memcpy(MEM + sp, &v, 8);
    return 1;
}

static int rt_pop(int rd) {
    uint64_t sp = R[32];
    if (sp + 8 > MEMSZ) { rt_fail("Stack underflow"); return 0; }
    memcpy(&R[rd], MEM + sp, 8);
    R[32] = sp + 8;
    return 1;
}

static uint64_t RETADDR;
static int rt_pop_ret(void) {
    uint64_t sp = R[32];
    if (sp + 8 > MEMSZ) { rt_fail("Stack underflow"); return 0; }
    memcpy(&RETADDR, MEM + sp, 8);
    R[32] = sp + 8;
    return 1;
}

static void rt_out_uint(uint64_t v) {
    if (v == 10) fputc('\n', OUTP);
    else fprintf(OUTP, "%llu", (unsigned long long)v);
}

static void rt_out_str(uint64_t addr) {
    if (addr >= MEMSZ) { rt_fail("String out of bounds"); return; }
    fputs((const char *)(MEM + addr), OUTP);
}

static void rt_out_float(uint64_t bits) {
    double d;
    memcpy(&d, &bits, 8);
    fprintf(OUTP, "%g", d);
}

static void rt_unsupported(const char *what) {
    if (!ERRMSG) ERRMSG = what;
}

static uint64_t HEAPP;

static uint64_t rt_strlen(uint64_t addr) {
    if (addr >= MEMSZ) { rt_fail("String out of bounds"); return 0; }
    return (uint64_t)strlen((const char *)(MEM + addr));
}

static void rt_abort(uint64_t addr) {
    if (addr < MEMSZ) rt_fail((const char *)(MEM + addr));
    else rt_fail("abort");
}

static uint64_t rt_rand_state = 1;
static uint64_t rt_rand(void) {
    rt_rand_state = rt_rand_state * UINT64_C(6364136223846793005) + 1;
    return rt_rand_state >> 1;
}

static int rt_net_init(void);   /* 定义在下方网络段 */

static int rt_init(uint64_t memsz, uint64_t sp, uint64_t heapbase) {
    MEMSZ = memsz;
    HEAPBASE = heapbase;
    HEAPP = heapbase;
    SYSPTR = heapbase > 8192 ? heapbase - 8192 : 512;
    MEM = (uint8_t *)calloc((size_t)memsz, 1);
    if (!MEM) return 0;
    rt_net_init();          /* 网络不可用不致命: 相关 SYS 会返回 -1 */
    R[32] = sp;
    OUTP = stdout;
    return 1;
}

static int rt_cond(int code) {
    switch (code) {
    case 0: return ZF;                    /* EQ */
    case 1: return !ZF;                   /* NE */
    case 2: return CF;                    /* CS */
    case 3: return !CF;                   /* CC */
    case 4: return NF;                    /* MI */
    case 5: return !NF;                   /* PL */
    case 6: return VF;                    /* VS */
    case 7: return !VF;                   /* VC */
    case 8: return CF && !ZF;             /* HI */
    case 9: return !CF || ZF;             /* LS */
    case 10: return NF == VF;             /* GE */
    case 11: return NF != VF;             /* LT */
    case 12: return !ZF && NF == VF;      /* GT */
    case 13: return ZF || NF != VF;       /* LE */
    case 14: return 1;                    /* AL */
    default: return 0;                    /* NV */
    }
}

static int rt_allocframe(uint64_t bytes) {
    int64_t guard = (int64_t)(HEAPBASE + 4096);
    int64_t newsp = (int64_t)R[32] - (int64_t)bytes;
    if (newsp < guard) { rt_fail("Stack overflow (frame allocation)"); return 0; }
    R[32] = (uint64_t)newsp;
    R[0] = R[32];
    return 1;
}

/* ---------------- 堆 / 字符串 ---------------- */

static uint64_t rt_heap(uint64_t size) {
    uint64_t p = HEAPP;
    size = (size + 15) & ~(uint64_t)15;
    if (p + size > R[32] || p + size < p) {
        rt_fail("Heap exhausted");
        return 0;
    }
    HEAPP = p + size;
    return p;
}

static const char *rt_cstr(uint64_t addr) {
    if (addr >= MEMSZ) { rt_fail("String out of bounds"); return ""; }
    return (const char *)(MEM + addr);
}

static uint64_t rt_sysbuf(void) { return SYSPTR; }

static void rt_writesys(const char *s) {
    size_t n = strlen(s);
    if (SYSPTR + n + 1 > MEMSZ) { rt_fail("sysbuf out of bounds"); return; }
    memcpy(MEM + SYSPTR, s, n + 1);
}

static uint64_t rt_dupstr(const char *s) {
    size_t n = strlen(s) + 1;
    uint64_t p = rt_heap((uint64_t)n);
    if (!p) return 0;
    memcpy(MEM + p, s, n);
    return p;
}

static void rt_to_upper(char *s) {
    for (; *s; s++) if (*s >= 'a' && *s <= 'z') *s -= 32;
}

static void rt_to_lower(char *s) {
    for (; *s; s++) if (*s >= 'A' && *s <= 'Z') *s += 32;
}

static int rt_is_space(int c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\v'
        || c == '\f';
}

static uint64_t rt_trim(uint64_t addr, int left, int right) {
    const char *s = rt_cstr(addr);
    size_t n = strlen(s), b = 0, e = n;
    if (left) while (b < e && rt_is_space((unsigned char)s[b])) b++;
    if (right) while (e > b && rt_is_space((unsigned char)s[e - 1])) e--;
    uint64_t p = rt_heap((uint64_t)(e - b) + 1);
    if (!p) return 0;
    memcpy(MEM + p, s + b, e - b);
    MEM[p + (e - b)] = 0;
    return p;
}

static uint64_t rt_substr(uint64_t src, uint64_t start, uint64_t length) {
    const char *s = rt_cstr(src);
    int64_t n = (int64_t)strlen(s);
    int64_t st = (int64_t)start > n ? n : ((int64_t)start > 0 ? (int64_t)start : 0);
    int64_t len = (int64_t)length > 0 ? (int64_t)length : 0;
    int64_t end = st + len;
    if (end > n) end = n;
    uint64_t p = rt_heap((uint64_t)(end - st) + 1);
    if (!p) return 0;
    memcpy(MEM + p, s + st, (size_t)(end - st));
    MEM[p + (uint64_t)(end - st)] = 0;
    return p;
}

static uint64_t rt_itoa(int64_t v) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%lld", (long long)v);
    rt_writesys(buf);
    return SYSPTR;
}

static uint64_t rt_ftoa(uint64_t bits) {
    double d;
    char buf[64];
    memcpy(&d, &bits, 8);
    snprintf(buf, sizeof(buf), "%g", d);
    rt_writesys(buf);
    return SYSPTR;
}

static uint64_t rt_boolstr(uint64_t v) {
    rt_writesys(v ? "true" : "false");
    return SYSPTR;
}

static uint64_t rt_strconcat(uint64_t a, uint64_t b) {
    const char *sa = rt_cstr(a);
    const char *sb = rt_cstr(b);
    size_t na = strlen(sa), nb = strlen(sb);
    uint64_t p = rt_heap((uint64_t)(na + nb + 1));
    if (!p) return 0;
    memcpy(MEM + p, sa, na);
    memcpy(MEM + p + na, sb, nb + 1);
    return p;
}

static uint64_t rt_strcpy(uint64_t dst, uint64_t src, int append) {
    const char *s = rt_cstr(src);
    size_t n = strlen(s);
    uint64_t at = dst;
    if (append) {
        if (at >= MEMSZ) { rt_fail("String out of bounds"); return dst; }
        at += strlen((const char *)(MEM + at));
    }
    if (at + n + 1 > MEMSZ) { rt_fail("String out of bounds"); return dst; }
    memcpy(MEM + at, s, n + 1);
    return dst;
}

static uint64_t rt_strcmp(uint64_t a, uint64_t b) {
    const char *sa = rt_cstr(a);
    const char *sb = rt_cstr(b);
    while (*sa && *sa == *sb) { sa++; sb++; }
    return (uint64_t)(int64_t)((unsigned char)*sa - (unsigned char)*sb);
}

static uint64_t rt_indexof(uint64_t hay, uint64_t needle) {
    const char *found = strstr(rt_cstr(hay), rt_cstr(needle));
    return found ? (uint64_t)(found - (const char *)MEM) : (uint64_t)-1;
}

/* ---------------- 文件系统 ---------------- */

static uint64_t rt_empty(void) {
    uint64_t p = rt_heap(1);
    if (p) MEM[p] = 0;
    return p;
}

static uint64_t rt_readfile(const char *path) {
    FILE *f = fopen(path, "rb");
    long n;
    uint64_t p;
    size_t got;
    if (!f) return rt_empty();
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return rt_empty(); }
    n = ftell(f);
    if (n < 0) { fclose(f); return rt_empty(); }
    rewind(f);
    p = rt_heap((uint64_t)n + 1);
    if (!p) { fclose(f); return rt_empty(); }
    got = fread(MEM + p, 1, (size_t)n, f);
    MEM[p + got] = 0;
    fclose(f);
    return p;
}

static int rt_writefile(const char *path, const char *data, int append) {
    FILE *f = fopen(path, append ? "ab" : "wb");
    size_t n;
    int ok;
    if (!f) return -1;
    n = strlen(data);
    ok = (fwrite(data, 1, n, f) == n);
    if (fclose(f) != 0) ok = 0;
    return ok ? 0 : -1;
}

static uint64_t rt_fileexists(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    fclose(f);
    return 1;
}

static uint64_t rt_filedelete(const char *path) {
    return remove(path) == 0 ? 0 : (uint64_t)-1;
}

static uint64_t rt_filesize(const char *path) {
    FILE *f = fopen(path, "rb");
    long n;
    if (!f) return (uint64_t)-1;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return (uint64_t)-1; }
    n = ftell(f);
    fclose(f);
    return n < 0 ? (uint64_t)-1 : (uint64_t)n;
}

static int rt_mkdir_one(const char *path) {
#ifdef _WIN32
    return _mkdir(path);
#else
    return mkdir(path, 0755);
#endif
}

static uint64_t rt_mkdirs(const char *path) {
    char buf[1024];
    size_t n = strlen(path);
    char *p;
    if (n == 0 || n >= sizeof(buf)) return (uint64_t)-1;
    memcpy(buf, path, n + 1);
    for (p = buf + 1; *p; p++) {
        if (*p == '/' || *p == '\\') {
            char save = *p;
            *p = 0;
            rt_mkdir_one(buf);
            *p = save;
        }
    }
    return rt_mkdir_one(buf) == 0 ? 0 : (uint64_t)-1;
}

/* ---------------- 目录 / 环境 ---------------- */

static int rt_cmp_name(const void *a, const void *b) {
    return strcmp(*(char *const *)a, *(char *const *)b);
}

static uint64_t rt_dirlist(const char *path) {
    DIR *d = opendir(path);
    char **names = NULL;
    size_t count = 0, cap = 0, total = 0, i, at;
    struct dirent *ent;
    uint64_t out;
    if (!d) return rt_empty();
    while ((ent = readdir(d)) != NULL) {
        char full[2048];
        struct stat st;
        size_t n;
        int is_dir = 0;
        if (!strcmp(ent->d_name, ".") || !strcmp(ent->d_name, "..")) continue;
        snprintf(full, sizeof(full), "%s/%s", path, ent->d_name);
        if (stat(full, &st) == 0 && (st.st_mode & S_IFMT) == S_IFDIR) is_dir = 1;
        n = strlen(ent->d_name) + (is_dir ? 2 : 1);
        if (count == cap) {
            cap = cap ? cap * 2 : 16;
            names = (char **)realloc(names, cap * sizeof(char *));
            if (!names) { closedir(d); return rt_empty(); }
        }
        names[count] = (char *)malloc(n);
        if (!names[count]) { closedir(d); return rt_empty(); }
        snprintf(names[count], n, "%s%s", ent->d_name, is_dir ? "/" : "");
        count++;
    }
    closedir(d);
    qsort(names, count, sizeof(char *), rt_cmp_name);
    for (i = 0; i < count; i++) total += strlen(names[i]) + 1;
    out = rt_heap((uint64_t)total + 1);
    if (!out) { for (i = 0; i < count; i++) free(names[i]); free(names); return 0; }
    at = 0;
    for (i = 0; i < count; i++) {
        size_t n = strlen(names[i]);
        if (i) MEM[out + at++] = '\n';
        memcpy(MEM + out + at, names[i], n);
        at += n;
        free(names[i]);
    }
    MEM[out + at] = 0;
    free(names);
    return out;
}

static uint64_t rt_getenv(const char *name) {
    const char *v = getenv(name);
    return rt_dupstr(v ? v : "");
}

static uint64_t rt_setenv(const char *name, const char *value) {
#ifdef _WIN32
    return _putenv_s(name, value) == 0 ? 0 : (uint64_t)-1;
#else
    return setenv(name, value, 1) == 0 ? 0 : (uint64_t)-1;
#endif
}

static uint64_t rt_osname(void) {
#if defined(_WIN32)
    return rt_dupstr("windows");
#elif defined(__APPLE__)
    return rt_dupstr("darwin");
#else
    return rt_dupstr("linux");
#endif
}

static uint64_t rt_hostname(void) {
#if defined(_WIN32)
    const char *name = getenv("COMPUTERNAME");
    return rt_dupstr(name ? name : "");
#else
    char buf[256];
    if (gethostname(buf, sizeof(buf)) != 0) return rt_empty();
    buf[sizeof(buf) - 1] = 0;
    return rt_dupstr(buf);
#endif
}

static uint64_t rt_username(void) {
#if defined(_WIN32)
    /* 与 Go 的 user.Current() 对齐: Windows 返回 DOMAIN\user */
    const char *user = getenv("USERNAME");
    const char *domain = getenv("USERDOMAIN");
    char buf[512];
    if (user && *user) {
        if (domain && *domain) {
            snprintf(buf, sizeof(buf), "%s\\%s", domain, user);
            return rt_dupstr(buf);
        }
        return rt_dupstr(user);
    }
    return rt_empty();
#else
    static const char *keys[] = {"USER", "USERNAME", "LOGNAME"};
    size_t i;
    for (i = 0; i < sizeof(keys) / sizeof(keys[0]); i++) {
        const char *v = getenv(keys[i]);
        if (v && *v) return rt_dupstr(v);
    }
    return rt_empty();
#endif
}

static uint64_t rt_cwd(void) {
    char buf[4096];
    return getcwd(buf, sizeof(buf)) ? rt_dupstr(buf) : rt_empty();
}

static uint64_t rt_homedir(void) {
    const char *home = getenv("HOME");
    if (!home || !*home) home = getenv("USERPROFILE");
    return rt_dupstr(home ? home : "");
}

/* ---------------- 网络 (TCP / DNS) ---------------- */

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET rt_sock_t;
#define RT_BAD_SOCK INVALID_SOCKET
#else
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <netdb.h>
typedef int rt_sock_t;
#define RT_BAD_SOCK (-1)
#endif

#define RT_MAX_SOCKS 64
static rt_sock_t rt_socks[RT_MAX_SOCKS];
static int rt_sock_open[RT_MAX_SOCKS];

static int rt_net_init(void) {
#ifdef _WIN32
    WSADATA w;
    return WSAStartup(MAKEWORD(2, 2), &w) == 0;
#else
    return 1;
#endif
}

static int rt_sock_register(rt_sock_t s) {
    int i;
    for (i = 1; i < RT_MAX_SOCKS; i++) {
        if (!rt_sock_open[i]) {
            rt_socks[i] = s;
            rt_sock_open[i] = 1;
            return i;
        }
    }
    return -1;
}

static rt_sock_t rt_sock_get(uint64_t fd) {
    if (fd == 0 || fd >= RT_MAX_SOCKS || !rt_sock_open[fd]) return RT_BAD_SOCK;
    return rt_socks[fd];
}

static void rt_sock_release(uint64_t fd) {
    if (fd > 0 && fd < RT_MAX_SOCKS && rt_sock_open[fd]) {
#ifdef _WIN32
        closesocket(rt_socks[fd]);
#else
        close(rt_socks[fd]);
#endif
        rt_sock_open[fd] = 0;
    }
}

static uint64_t rt_dns_lookup(const char *host) {
    struct addrinfo hints, *res = NULL, *it;
    char text[64];
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;          /* 与 VM 一致: 优先 IPv4 */
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, NULL, &hints, &res) != 0 || !res) {
        hints.ai_family = AF_UNSPEC;
        if (getaddrinfo(host, NULL, &hints, &res) != 0 || !res) {
            return rt_empty();
        }
    }
    for (it = res; it; it = it->ai_next) {
        if (it->ai_family == AF_INET) {
            struct sockaddr_in *sa = (struct sockaddr_in *)it->ai_addr;
            if (inet_ntop(AF_INET, &sa->sin_addr, text, sizeof(text))) {
                freeaddrinfo(res);
                return rt_dupstr(text);
            }
        }
    }
    for (it = res; it; it = it->ai_next) {
        if (it->ai_family == AF_INET6) {
            struct sockaddr_in6 *sa = (struct sockaddr_in6 *)it->ai_addr;
            if (inet_ntop(AF_INET6, &sa->sin6_addr, text, sizeof(text))) {
                freeaddrinfo(res);
                return rt_dupstr(text);
            }
        }
    }
    freeaddrinfo(res);
    return rt_empty();
}

static uint64_t rt_tcp_dial(const char *host, uint64_t port) {
    struct addrinfo hints, *res = NULL, *it;
    char service[16];
    rt_sock_t s = RT_BAD_SOCK;
    int id;
    if (!host || !*host || port == 0 || port > 65535) return (uint64_t)-1;
    snprintf(service, sizeof(service), "%u", (unsigned)port);
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, service, &hints, &res) != 0 || !res)
        return (uint64_t)-1;
    for (it = res; it; it = it->ai_next) {
        s = socket(it->ai_family, it->ai_socktype, it->ai_protocol);
        if (s == RT_BAD_SOCK) continue;
        if (connect(s, it->ai_addr, (int)it->ai_addrlen) == 0) break;
#ifdef _WIN32
        closesocket(s);
#else
        close(s);
#endif
        s = RT_BAD_SOCK;
    }
    freeaddrinfo(res);
    if (s == RT_BAD_SOCK) return (uint64_t)-1;
    id = rt_sock_register(s);
    if (id < 0) {
#ifdef _WIN32
        closesocket(s);
#else
        close(s);
#endif
        return (uint64_t)-1;
    }
    return (uint64_t)id;
}

static uint64_t rt_tcp_send(uint64_t fd, uint64_t buf, uint64_t n) {
    rt_sock_t s = rt_sock_get(fd);
    size_t sent = 0;
    if (s == RT_BAD_SOCK || n > (16u << 20)) return (uint64_t)-1;
    if (n == 0) return 0;
    if (buf >= MEMSZ || n > MEMSZ - buf) return (uint64_t)-1;
    while (sent < (size_t)n) {
        int k = send(s, (const char *)(MEM + buf) + sent,
                     (int)((size_t)n - sent), 0);
        if (k <= 0) return sent ? (uint64_t)sent : (uint64_t)-1;
        sent += (size_t)k;
    }
    return (uint64_t)sent;
}

static uint64_t rt_tcp_recv(uint64_t fd, uint64_t buf, uint64_t max) {
    rt_sock_t s = rt_sock_get(fd);
    int k;
    if (s == RT_BAD_SOCK || max > (8u << 20)) return (uint64_t)-1;
    if (max == 0) return 0;
    if (buf >= MEMSZ || max > MEMSZ - buf) return 0;
    k = recv(s, (char *)(MEM + buf), (int)max, 0);
    if (k < 0) return (uint64_t)-1;
    return (uint64_t)k;
}

static uint64_t rt_tcp_close(uint64_t fd) {
    if (rt_sock_get(fd) == RT_BAD_SOCK) return (uint64_t)-1;
    rt_sock_release(fd);
    return 0;
}

static double rt_bits2f(uint64_t bits) {    double d;
    memcpy(&d, &bits, 8);
    return d;
}

static uint64_t rt_f2bits(double d) {
    uint64_t bits;
    memcpy(&bits, &d, 8);
    return bits;
}

static int rt_finish(void) {
    fflush(OUTP);
    if (ERRMSG) {
        fprintf(stderr, "runtime error: %s\n", ERRMSG);
        return 1;
    }
    return 0;
}
'''


def _u64(v: Any) -> int:
    return int(v) & 0xFFFFFFFFFFFFFFFF


def _i64(v: Any) -> int:
    v = int(v) & 0xFFFFFFFFFFFFFFFF
    return v - (1 << 64) if v >> 63 else v


def _c_str(text: str) -> str:
    out = []
    for byte in text.encode('utf-8'):
        if byte in (0x22, 0x5C):
            out.append('\\' + chr(byte))
        elif 32 <= byte < 127:
            out.append(chr(byte))
        else:
            out.append('\\%03o' % byte)
    return '"' + ''.join(out) + '"'


class _Emitter:
    def __init__(self,
                 instructions: Sequence[Instruction],
                 labels: Dict[str, int],
                 segments: Segments,
                 entry_pc: int,
                 mem_size: int,
                 sp: int,
                 heap_base: int):
        self.instructions = list(instructions)
        self.labels = dict(labels)
        self.segments = list(segments)
        self.entry_pc = int(entry_pc)
        self.mem_size = int(mem_size)
        self.sp = int(sp)
        self.heap_base = int(heap_base)
        self.lines: List[str] = []
        self._need_dispatch = False

    # ---------- 基础设施 ----------

    def emit(self, text: str = '') -> None:
        self.lines.append(text)

    def code(self, text: str) -> None:
        self.lines.append('\t' + text)

    def fail(self, expr: str) -> None:
        self.code(f'if (!({expr})) {{ goto Lerror; }}')

    def target(self, arg: Tuple[Any, ...]) -> Optional[int]:
        kind = arg[0]
        if kind == 'label':
            pc = self.labels.get(arg[1])
        elif kind == 'imm':
            pc = int(arg[1])
        else:
            return None
        if pc is None or pc < 0 or pc >= len(self.instructions):
            return None
        return int(pc)

    def value(self, arg: Tuple[Any, ...]) -> Optional[str]:
        kind = arg[0]
        if kind == 'reg':
            return f'R[{int(arg[1])}]'
        if kind == 'imm':
            return f'UINT64_C(0x{_u64(arg[1]):x})'
        if kind == 'str':
            return f'UINT64_C(0x{_u64(arg[1]):x})'
        if kind == 'float':
            return f'UINT64_C(0x{struct.unpack("<Q", struct.pack("<d", float(arg[1])))[0]:x})'
        return None

    def address(self, arg: Tuple[Any, ...]) -> Optional[str]:
        kind = arg[0]
        if kind == 'reg':
            return f'R[{int(arg[1])}]'
        if kind == 'imm':
            return f'UINT64_C(0x{_u64(arg[1]):x})'
        if kind == 'mem':
            # [reg + off] 形式的基址寄存器寻址
            base = int(arg[1])
            off = _i64(arg[2])
            if off == 0:
                return f'R[{base}]'
            return f'(R[{base}] + UINT64_C(0x{_u64(off):x}))'
        return None

    def dispatch(self) -> None:
        self._need_dispatch = True

    # ---------- 单条指令 ----------

    def instruction(self, op: str,
                    args: List[Tuple[Any, ...]], idx: int) -> None:
        # ---- 数据搬运 ----
        if op == 'MOV':
            v = self.value(args[1])
            if v is None:
                self.unsupported(op)
            else:
                self.code(f'R[{int(args[0][1])}] = {v};')
            return

        # ---- 二元整数运算 (rd op= rhs) ----
        binary = {
            'ADD': 'R[{rd}] = rt_add(R[{rd}], {v});',
            'SUB': 'R[{rd}] = rt_sub(R[{rd}], {v});',
            'MUL': 'R[{rd}] = rt_mul(R[{rd}], {v});',
            'AND': 'R[{rd}] = R[{rd}] & {v};',
            'OR': 'R[{rd}] = R[{rd}] | {v};',
            'XOR': 'R[{rd}] = R[{rd}] ^ {v};',
            'SHL': 'R[{rd}] = rt_shl(R[{rd}], {v});',
            'SHR': 'R[{rd}] = rt_shr(R[{rd}], {v});',
            'ASR': 'R[{rd}] = rt_asr(R[{rd}], {v});',
            'ROR': 'R[{rd}] = rt_ror(R[{rd}], {v});',
        }
        if op in binary:
            rd = int(args[0][1])
            v = self.value(args[1])
            if v is None:
                self.unsupported(op)
            else:
                self.code(binary[op].format(rd=rd, v=v))
            return
        if op == 'DIV':
            rd = int(args[0][1])
            v = self.value(args[1])
            if v is None:
                self.unsupported(op)
            else:
                self.code(f'R[{rd}] = rt_sdiv(R[{rd}], {v});')
                self.code('if (ERRMSG) goto Lerror;')
            return

        # ---- 三操作数形式: op rd, rs, v ----
        if op in ('ADDI', 'LSL', 'LSR', 'ASR', 'XORI', 'ORI', 'ANDI'):
            rd, rs = int(args[0][1]), int(args[1][1])
            v = self.value(args[2]) if len(args) > 2 else None
            if v is None:
                self.unsupported(op)
                return
            expr = {
                'ADDI': f'rt_add(R[{rs}], {v})',
                'LSL': f'rt_shl(R[{rs}], {v})',
                'LSR': f'rt_shr(R[{rs}], {v})',
                'ASR': f'rt_asr(R[{rs}], {v})',
                'XORI': f'(R[{rs}] ^ {v})',
                'ORI': f'(R[{rs}] | {v})',
                'ANDI': f'(R[{rs}] & {v})',
            }[op]
            self.code(f'R[{rd}] = {expr};')
            return

        if op in ('INC', 'DEC'):
            rd = int(args[0][1])
            delta = '+ 1' if op == 'INC' else '- 1'
            self.code(f'R[{rd}] = R[{rd}] {delta};')
            return
        if op == 'MVN':
            rd = int(args[0][1])
            v = self.value(args[1])
            if v is None:
                self.unsupported(op)
            else:
                self.code(f'R[{rd}] = ~{v};')
            return
        if op == 'CMP':
            a, b = self.value(args[0]), self.value(args[1])
            if a is None or b is None:
                self.unsupported(op)
            else:
                self.code(f'rt_cmp({a}, {b});')
            return

        # ---- 栈 ----
        if op == 'PUSH':
            v = self.value(args[0])
            if v is None:
                self.unsupported(op)
            else:
                self.fail(f'rt_push({v})')
            return
        if op == 'POP':
            self.fail(f'rt_pop({int(args[0][1])})')
            return

        # ---- 内存 ----
        widths = {'LOAD': (4, 0), 'LW': (4, 1), 'LD': (8, 1), 'LB': (1, 1),
                  'LH': (2, 1), 'LDR': (4, 0)}
        if op in widths:
            width, sign = widths[op]
            addr = self.address(args[1])
            if addr is None:
                self.unsupported(op)
            else:
                self.fail(f'rt_load({int(args[0][1])}, {addr}, {width}, {sign})')
            return
        stores = {'STORE': 4, 'SW': 4, 'STR': 4, 'SD': 8, 'SB': 1, 'SH': 2}
        if op in stores:
            addr = self.address(args[1])
            if addr is None:
                self.unsupported(op)
            else:
                self.fail(f'rt_store(R[{int(args[0][1])}], {addr}, '
                          f'{stores[op]})')
            return

        if op == 'B':
            cond_arg = next((a for a in args if a[0] == 'cond'), None)
            target_arg = next((a for a in args if a[0] != 'cond'), None)
            target = self.target(target_arg) if target_arg else None
            if cond_arg is None or target is None:
                self.unsupported(op)
                return
            code = _COND_CODES.get(str(cond_arg[1]).upper())
            if code is None:
                self.unsupported(op)
                return
            self.code(f'if (rt_cond({code})) goto L{target};')
            return
        if op in ('CBZ', 'CBNZ'):
            target = self.target(args[-1])
            if target is None:
                self.unsupported(op)
                return
            test = '==' if op == 'CBZ' else '!='
            self.code(f'if (R[{int(args[0][1])}] {test} 0) goto L{target};')
            return

        # ---- 控制流 ----
        if op == 'JMP':
            target = self.target(args[0])
            if target is None:
                self.unsupported(op)
            else:
                self.code(f'goto L{target};')
            return
        if op in ('JZ', 'JE', 'JNZ', 'JL', 'JG'):
            target = self.target(args[0])
            if target is None:
                self.unsupported(op)
                return
            cond = {'JZ': 'ZF', 'JE': 'ZF', 'JNZ': '!ZF',
                    'JL': '(NF != VF)', 'JG': '(!ZF && NF == VF)'}[op]
            self.code(f'if ({cond}) goto L{target};')
            return
        if op in ('CALL', 'BL'):
            target = self.target(args[0])
            if target is None:
                self.unsupported(op)
                return
            self.fail(f'rt_push(UINT64_C({idx + 1}))')
            self.code(f'goto L{target};')
            return
        if op == 'RET':
            self.fail('rt_pop_ret()')
            self.code('pc = RETADDR; goto Ldispatch;')
            return
        if op == 'HALT':
            self.code('goto Ldone;')
            return

        # ---- I/O ----
        if op == 'OUT':
            kind, value, extra = self.out_operand(args[0])
            self.code(value)
            return
        if op == 'SYS':
            if args[0][0] != 'imm':
                self.unsupported(op)
                return
            code = _u64(args[0][1])
            if code == 137:                     # ALLOCFRAME
                self.fail('rt_allocframe(R[0])')
                return
            simple = {
                0: 'R[0] = rt_f2bits(sqrt(rt_bits2f(R[0])));',          # SQRT
                1: 'R[0] = rt_f2bits(pow(rt_bits2f(R[0]), rt_bits2f(R[1])));',
                2: 'R[0] = (uint64_t)llabs((long long)R[0]);',          # ABS
                3: 'R[0] = rt_f2bits(sin(rt_bits2f(R[0])));',           # SIN
                4: 'R[0] = rt_f2bits(cos(rt_bits2f(R[0])));',           # COS
                5: 'R[0] = rt_f2bits(tan(rt_bits2f(R[0])));',           # TAN
                6: 'R[0] = rt_f2bits(rt_bits2f(R[0]) + rt_bits2f(R[1]));',
                7: 'R[0] = rt_f2bits(rt_bits2f(R[0]) - rt_bits2f(R[1]));',
                8: 'R[0] = rt_f2bits(rt_bits2f(R[0]) * rt_bits2f(R[1]));',
                9: ('if (rt_bits2f(R[1]) == 0.0) { rt_fail("Float division '
                    'by zero (SYS)"); goto Lerror; } '
                    'R[0] = rt_f2bits(rt_bits2f(R[0]) / rt_bits2f(R[1]));'),
                10: ('{ double fa = rt_bits2f(R[0]), fb = rt_bits2f(R[1]); '
                     'R[0] = fa < fb ? (uint64_t)-1 : (fa > fb ? 1 : 0); }'),
                11: 'R[0] = (uint64_t)(int64_t)rt_bits2f(R[0]);',       # FTOI
                12: 'R[0] = rt_f2bits((double)(int64_t)R[0]);',         # ITOF
                13: 'R[0] = rt_rand();',                                # RAND
                14: 'rt_rand_state = R[0] ? R[0] : 1;',                 # SRAND
                15: 'R[0] = (uint64_t)time(NULL);',                     # TIME
                16: 'R[0] = rt_strlen(R[0]);',                          # STRLEN
                17: 'R[0] = rt_strcmp(R[0], R[1]);',                    # STRCMP
                18: 'R[0] = rt_strcpy(R[0], R[1], 0);',                 # STRCPY
                19: 'R[0] = rt_strcpy(R[0], R[1], 1);',                 # STRCAT
                20: 'R[0] = rt_heap(R[0]);',                            # MALLOC
                21: 'rt_out_float(R[0]);',                              # PRINT_FLOAT
                22: 'R[0] = rt_itoa((int64_t)R[0]);',                   # ITOA
                23: 'R[0] = rt_ftoa(R[0]);',                            # FTOA
                24: 'rt_out_str(R[0]);',                                # PRINT_STR
                25: 'R[0] = rt_strconcat(R[0], R[1]);',                 # STR_CONCAT
                26: 'R[0] = rt_boolstr(R[0]);',                         # BOOL_STR
                27: 'rt_abort(R[0]);',                                  # ABORT
                28: 'R[0] = rt_substr(R[0], R[1], R[2]);',              # SUBSTR
                29: 'R[0] = rt_indexof(R[0], R[1]);',                   # INDEXOF
                30: ('R[0] = rt_dupstr(rt_cstr(R[0])); '
                     'if (R[0]) rt_to_upper((char *)(MEM + R[0]));'),   # TOUPPER
                31: ('R[0] = rt_dupstr(rt_cstr(R[0])); '
                     'if (R[0]) rt_to_lower((char *)(MEM + R[0]));'),   # TOLOWER
                32: 'R[0] = rt_f2bits(floor(rt_bits2f(R[0])));',        # FLOOR
                33: 'R[0] = rt_f2bits(ceil(rt_bits2f(R[0])));',         # CEIL
                34: 'R[0] = rt_f2bits(floor(rt_bits2f(R[0]) + 0.5));',  # ROUND
                35: ('{ const char *s = rt_cstr(R[0]); char *end = NULL; '
                     'long long v = strtoll(s, &end, 10); '
                     'R[0] = (end && end != s) ? (uint64_t)v : 0; }'),
                36: 'R[0] = rt_trim(R[0], 1, 1);',                    # TRIM
                37: 'R[0] = rt_trim(R[0], 1, 0);',                    # LTRIM
                38: 'R[0] = rt_trim(R[0], 0, 1);',                    # RTRIM
                51: 'R[0] = rt_readfile(rt_cstr(R[0]));',              # FILE_READ
                52: 'R[0] = (uint64_t)(int64_t)rt_writefile('
                    'rt_cstr(R[0]), rt_cstr(R[1]), 0);',               # WRITE
                53: 'R[0] = (uint64_t)(int64_t)rt_writefile('
                    'rt_cstr(R[0]), rt_cstr(R[1]), 1);',               # APPEND
                54: 'R[0] = rt_fileexists(rt_cstr(R[0]));',            # EXISTS
                55: 'R[0] = rt_filedelete(rt_cstr(R[0]));',            # DELETE
                56: 'R[0] = rt_filesize(rt_cstr(R[0]));',              # SIZE
                57: 'R[0] = rt_mkdirs(rt_cstr(R[0]));',                # MKDIR
                58: 'R[0] = rt_dirlist(rt_cstr(R[0]));',               # DIRLIST
                61: 'R[0] = rt_getenv(rt_cstr(R[0]));',                # GETENV
                62: 'R[0] = rt_setenv(rt_cstr(R[0]), rt_cstr(R[1]));', # SETENV
                63: 'R[0] = rt_osname();',                             # OSNAME
                64: 'R[0] = rt_hostname();',                           # HOSTNAME
                65: 'R[0] = rt_username();',                           # USERNAME
                66: 'R[0] = rt_cwd();',                                # CWD
                67: 'R[0] = rt_homedir();',                            # HOMEDIR
                147: 'R[0] = rt_tcp_dial(rt_cstr(R[0]), R[1]);',       # TCP_DIAL
                148: 'R[0] = rt_tcp_send(R[0], R[1], R[2]);',          # TCP_SEND
                149: 'R[0] = (uint64_t)(int64_t)rt_tcp_recv('
                     'R[0], R[1], R[2]);',                             # TCP_RECV
                150: 'R[0] = rt_tcp_close(R[0]);',                      # TCP_CLOSE
                157: 'R[0] = rt_dns_lookup(rt_cstr(R[0]));',           # DNS_LOOKUP
            }
            if code in simple:
                self.code(simple[code])
                self.code('if (ERRMSG) goto Lerror;')
                return
            self.code(f'rt_unsupported("SYS {code} 未在 C 后端实现");')
            self.code('goto Lerror;')
            return

        self.unsupported(op)

    def out_operand(self, arg: Tuple[Any, ...]) -> Tuple[str, str, str]:
        kind = arg[0]
        if kind == 'reg':
            return 'int', f'rt_out_uint(R[{int(arg[1])}]);', ''
        if kind == 'imm':
            return 'int', f'rt_out_uint(UINT64_C(0x{_u64(arg[1]):x}));', ''
        if kind == 'str':
            return 'str', f'rt_out_str(UINT64_C(0x{_u64(arg[1]):x}));', ''
        if kind == 'float':
            bits = struct.unpack('<Q', struct.pack('<d', float(arg[1])))[0]
            return 'float', f'rt_out_float(UINT64_C(0x{bits:x}));', ''
        raise CBackendError(f'不支持的 OUT 操作数: {kind}')

    def unsupported(self, op: str) -> None:
        self.code(f'rt_unsupported("instruction {op} 未在 C 后端实现");')
        self.code('goto Lerror;')

    # ---------- 整程序 ----------

    def generate(self) -> str:
        body: List[str] = []
        self.lines = body
        for idx, (op, args) in enumerate(self.instructions):
            self.emit(f'L{idx}:;')
            try:
                self.instruction(op, list(args), idx)
            except CBackendError as e:
                self.code(f'rt_unsupported({_c_str(str(e))});')
                self.code('goto Lerror;')

        data: List[str] = []
        for addr, blob in self.segments:
            if not blob:
                continue
            data.append(f'static const unsigned char seg_{addr:x}[] = {{'
                        + ','.join(str(b) for b in blob) + '};')
        out: List[str] = [_RUNTIME]
        out.extend(data)
        out.append('int main(void) {')
        out.append('\tif (!rt_init(UINT64_C({}), UINT64_C({}), '
                   'UINT64_C({}))) {{'.format(
                       self.mem_size, _u64(self.sp), _u64(self.heap_base)))
        out.append('\t\tfprintf(stderr, "runtime error: cannot allocate '
                   f'{self.mem_size} bytes\\n");')
        out.append('\t\treturn 1;')
        out.append('\t}')
        out.append('\tuint64_t pc = 0;')
        for addr, blob in self.segments:
            if blob:
                out.append(f'\tmemcpy(MEM + UINT64_C(0x{addr:x}), '
                           f'seg_{addr:x}, sizeof(seg_{addr:x}));')
        out.append('\tgoto L{};'.format(self.entry_pc))
        out.extend(body)
        out.append('Ldispatch:;')
        out.append('\tswitch (pc) {')
        for idx in range(len(self.instructions)):
            out.append(f'\tcase {idx}: goto L{idx};')
        out.append('\tdefault: rt_unsupported("bad jump target"); '
                   'goto Lerror;')
        out.append('\t}')
        out.append('Ldone:;')
        out.append('\treturn rt_finish();')
        out.append('Lerror:;')
        out.append('\treturn rt_finish();')
        out.append('}')
        return '\n'.join(out) + '\n'


def emit_c(instructions: Sequence[Instruction], labels: Dict[str, int],
           segments: Segments, entry_pc: int, mem_size: int, sp: int,
           heap_base: int) -> str:
    """生成 C 源码 (纯函数, 便于单测)。"""
    return _Emitter(instructions, labels, segments, entry_pc, mem_size, sp,
                    heap_base).generate()


def compile_instructions(instructions: Sequence[Instruction],
                         labels: Dict[str, int],
                         segments: Segments,
                         entry_pc: int,
                         mem_size: int,
                         sp: int,
                         heap_base: int,
                         out: str,
                         target: Optional[str] = None,
                         optimize: int = 2,
                         keep_c: bool = False) -> str:
    """把指令流编译成独立可执行文件, 返回产物路径。"""
    target_name = target or _host_target_name()
    spec = toolchain.get_target(target_name)
    info = toolchain.require(spec)
    source = emit_c(instructions, labels, segments, entry_pc, mem_size, sp,
                    heap_base)
    out = os.path.abspath(out)
    if spec.exe and not out.lower().endswith(spec.exe):
        out += spec.exe
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    workdir = tempfile.mkdtemp(prefix='.aotc-')
    try:
        c_path = os.path.join(workdir, 'program.c')
        with open(c_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(source)
        if keep_c:
            import shutil
            shutil.copyfile(c_path, out + '.c')
        cmd = [info.path, f'-O{max(0, min(3, int(optimize)))}',
               '-std=c11', '-o', out, c_path]
        if spec.fmt != 'pe':
            cmd.append('-lm')          # Linux/ELF 的 libm 需显式链接
        else:
            cmd.append('-lws2_32')     # Windows 的 Winsock
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=300, check=False)
        except (OSError, subprocess.SubprocessError) as e:
            raise CBackendError(f'调用 C 编译器失败: {e}') from e
        if result.returncode != 0:
            raise CBackendError(
                f'C 编译失败 ({info.path}):\n'
                f'{(result.stderr or result.stdout).strip()[:4000]}')
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    return out


def _host_target_name() -> str:
    import platform
    system = platform.system()
    machine = platform.machine().lower()
    if system == 'Windows':
        return 'x86_64-windows'
    if machine in ('arm64', 'aarch64'):
        return 'aarch64-linux'
    return 'x86_64-linux'


def c_target(goos: str, goarch: str = 'amd64') -> str:
    """把 AOT 的 (GOOS, GOARCH) 映射成 C 后端目标名。"""
    goos = (goos or '').lower()
    goarch = (goarch or 'amd64').lower()
    if goos == 'windows' and goarch in ('amd64', 'x86_64'):
        return 'x86_64-windows'
    if goos == 'linux':
        return 'aarch64-linux' if goarch in ('arm64', 'aarch64') \
            else 'x86_64-linux'
    raise CBackendError(
        f'C 后端暂不支持目标 {goos}/{goarch} (可选: windows/amd64, '
        f'linux/amd64, linux/arm64)')


def supports(instructions: Sequence[Instruction]) -> Tuple[bool, str]:
    """指令流能否由 C 后端完整翻译; 不支持时返回原因。"""
    missing = sorted({str(op) for op, _args in instructions
                      if str(op) not in SUPPORTED_OPS})
    if missing:
        return False, '未实现的指令: ' + ', '.join(missing)
    for op, args in instructions:
        if str(op) != 'SYS' or not args:
            continue
        if args[0][0] != 'imm':
            return False, 'SYS 功能号必须是常量'
        code = int(args[0][1])
        if code not in SUPPORTED_SYS:
            return False, f'未实现的 SYS {code}'
    return True, ''
