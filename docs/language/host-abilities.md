---
description: "CIN 宿主能力：2D 画布导出 PNG、联网音频播放、文件/进程/环境访问与 Termux API，均需 Go 原生运行时。"
---

# 宿主能力

除了纯计算, CIN 还能直接调用宿主: 画布绘图 (导出 PNG)、联网音频播放、文件与进程操作、
系统信息, 以及在 Android Termux 下调用 Termux API。

::: danger 这些能力只有 Go 原生实现
宿主能力内建**没有纯 Python 实现**。用 `--no-native` (或原生库加载失败) 时调用它们会直接
报错并以退出码 1 结束:

```text
ERROR    Execution error: host builtins (GUI/audio/system/Termux) require the
         native Go runtime (run without --no-native)
+------------------------------ Execution Error ------------------------------+
| host builtins (GUI/audio/system/Termux) require the native Go runtime (run  |
| without --no-native)                                                        |
+-----------------------------------------------------------------------------+
```
:::

除此之外的所有语言功能 (含全部纯 CIN 标准库) 在三路径下行为一致。

## 2D 绘图画布

CIN 内置一个跨平台的 2D 画布 (Go 标准库实现, 可导出 PNG)。同一时刻存在一个“当前画布”
与一个“当前画笔颜色”(默认黑色, 背景白色)。

| 函数 | 签名 | 说明 |
|------|------|------|
| `canvas(w, h)` | void | 新建 `w×h` 画布 (白底) |
| `set_color(rgb)` | void | 设置画笔颜色 `0xRRGGBB` |
| `fill_rect(x, y, w, h)` | void | 填充矩形 |
| `fill_circle(cx, cy, r)` | void | 填充圆 |
| `draw_line(x0, y0, x1, y1)` | void | 画线 (Bresenham) |
| `draw_text(x, y, s)` | void | 绘制文本 (内置 5×7 点阵字库; 小写自动转大写) |
| `save_png(path)` | int | 导出 PNG, `0` 成功 / `-1` 失败 |
| `show_canvas()` | int | 存到临时 PNG 并用系统查看器打开 (跨平台“窗口”) |

```c
function draw() -> int {
    canvas(200, 100)
    set_color(0xFF0000)            // 红
    fill_rect(0, 0, 80, 100)
    set_color(0x0000FF)            // 蓝
    fill_circle(140, 50, 40)
    set_color(0x00FF00)            // 绿
    draw_line(0, 0, 199, 99)
    set_color(0x000000)            // 黑
    draw_text(4, 4, "HELLO")
    return save_png("out.png")     // 0 成功
}
```

> 交互式**控件**版 (按钮/输入框) 依赖桌面图形库, 目前未提供; 画布 + PNG 导出 +
> 系统查看器窗口已覆盖大部分可视化需求。更高层的绘图辅助函数 (`g_rect_outline` /
> `g_bar_chart` / `g_line_chart` / `g_grid`) 见标准库 `gui.cin`
> ([标准库参考](/stdlib/reference))。

## 联网音频

| 函数 | 签名 | 说明 |
|------|------|------|
| `audio_play(url)` | int | 下载 `http/https` URL 或读取本地文件并播放 (WAV/PCM), `0` 成功 / `-1` 失败 |
| `audio_stop()` | void | 停止当前播放 |
| `audio_volume(v)` | void | 设置音量 `0..100` (支持则生效, 否则忽略) |
| `audio_wait()` | void | 阻塞到当前播放结束 (按 WAV 头时长估算) |

```c
function music() -> int {
    int ok = audio_play("https://example.com/tone.wav")
    if (ok == 0) {
        audio_volume(80)
        audio_wait()
    }
    return ok
}
```

- 目前**只支持 WAV(PCM)**; MP3/OGG 需要额外解码库, 尚未纳入;
- 播放后端按平台选择 (Windows `winmm`, 其它平台 `afplay` / `aplay` / `paplay` / `ffplay`),
  无可用后端时返回 `-1`。

## 文件系统

| 函数 | 签名 | 说明 |
|------|------|------|
| `file_read(path)` | string | 读文件内容 (失败为空串) |
| `file_write(path, s)` | int | 覆盖写, `0` 成功 / `-1` 失败 |
| `file_append(path, s)` | int | 追加写 |
| `file_exists(path)` | int | `1` 存在 / `0` 不存在 |
| `file_delete(path)` | int | 删除文件或空目录 |
| `file_size(path)` | int | 字节数 / `-1` |
| `mkdir(path)` | int | 递归创建目录 |
| `dir_list(path)` | string | 换行分隔条目 (目录名带 `/`) |

实测示例 (Windows, 原生路径):

```c
function main() -> int {
    string f = "cin_io.txt"
    file_write(f, "hello")
    file_append(f, " world")
    println(file_read(f))
    println("size = " + int_to_str(file_size(f)))
    println("exists = " + int_to_str(file_exists(f)))
    return 0
}
```

```text
hello world
size = 11
exists = 1
```

## 进程与环境

| 函数 | 签名 | 说明 |
|------|------|------|
| `exec(cmd)` | int | 执行 shell 命令, 返回退出码 |
| `exec_output(cmd)` | string | 执行并返回 stdout |
| `getenv(name)` | string | 读环境变量 (未设置为空串) |
| `setenv(name, value)` | int | 设置环境变量 |
| `os_name()` | string | `"windows"` / `"darwin"` / `"linux"` |
| `hostname()` | string | 主机名 |
| `username()` | string | 用户名 |
| `cwd()` | string | 当前工作目录 |
| `home_dir()` | string | 用户主目录 |

```c
println("os = " + os_name())            // os = windows
println("cwd = " + cwd())               // cwd = D:\ByUsi\Projects\UCPU
setenv("MY_VAR", "42")
println(getenv("MY_VAR"))               // 42
println(exec_output("echo hi"))         // hi
```

::: warning 这是真实的宿主权限
`file_write` / `file_delete` / `exec` 具备当前进程的文件与进程权限, 不会沙箱化。
运行不可信程序时用 `--sandbox` (限制宿主访问) 与 `--no-io` (禁止 `IN`/`OUT` 与宿主 I/O),
或者根本不要在原生路径下运行。
:::

## Termux API (Android)

在 Termux 中 `pkg install termux-api` 并安装 **Termux:API** 应用后可用; 非 Termux 环境下
所有调用会优雅失败 (返回 `-1` 或空串)。

| 函数 | 签名 | 说明 |
|------|------|------|
| `termux_available()` | int | `1` 可用 / `0` 不可用 |
| `termux_notify(title, content)` | int | 系统通知 |
| `termux_toast(msg)` | int | Toast 提示 |
| `termux_clipboard_get()` | string | 读剪贴板 |
| `termux_clipboard_set(s)` | int | 写剪贴板 |
| `termux_battery()` | string | 电池状态 (JSON) |
| `termux_vibrate(ms)` | int | 振动指定毫秒 |
| `termux_tts(text)` | int | 文字转语音 |
| `termux_location()` | string | 定位信息 (JSON) |
| `termux_wifi_info()` | string | WiFi 连接信息 (JSON) |
| `termux_dialog(title)` | string | 弹出输入对话框 (JSON) |
| `termux_sms_send(number, text)` | int | 发送短信 |

```c
if (termux_available() == 1) {
    termux_notify("Code CIN", "任务完成")
    termux_toast("hello from CIN")
    termux_vibrate(200)
    termux_tts("done")
    println(termux_battery())
    println(termux_clipboard_get())
}
```

更高的封装 (`tx_notify` / `tx_battery_level` / `tx_latitude` …) 见标准库 `termux.cin`
与 [标准库参考](/stdlib/reference); 一键安装脚本见仓库 `script/install_termux.sh`。

## 能力矩阵

| 能力 | 纯 Python / JIT | Go 原生 | 说明 |
|------|:---------------:|:-------:|------|
| 画布 / PNG 导出 / 查看器 | ❌ | ✅ | `canvas` 系列 |
| 联网音频 | ❌ | ✅ | 仅 WAV(PCM) |
| 文件 / 目录 | ❌ | ✅ | 真实文件系统权限 |
| 进程 / 环境变量 / 系统信息 | ❌ | ✅ | `exec` / `getenv` / `os_name` |
| Termux API | ❌ | ✅ | 需 Termux:API |
| 纯 CIN 标准库 (`math`/`sort`/`hash`…) | ✅ | ✅ | 三路径一致 |

## 相关页面

- [执行路径](/guide/execution-paths) — 为什么宿主能力必须走原生
- [Go 原生运行时](/runtime/native) — 原生库加载与宿主调用区段
- [内建函数](/language/builtins) — 宿主内建的完整清单
- [标准库参考](/stdlib/reference) — `io` / `gui` / `termux` 库的封装函数
