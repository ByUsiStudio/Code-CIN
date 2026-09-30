" Vim 语法: Code CIN 语言 (放入 ~/.vim/syntax/cin.vim)
" 粗粒度高亮, 详细文法见 docs/CIN_GUIDE.md
if exists("b:current_syntax") | finish | endif

syntax keyword cinStatement function return if else while for do switch case
syntax keyword cinStatement default break continue
syntax keyword cinType int float bool string void char short long unsigned struct
syntax keyword cinType enum
syntax keyword cinConstant true false
syntax keyword cinBuiltin print println input abs sqrt pow sin cos tan rand
syntax keyword cinBuiltin srand time strlen strcmp strcpy int_to_str float_to_str
syntax keyword cinBuiltin bool_to_str itoa ftoa substr indexof upper lower
syntax keyword cinBuiltin floor ceil round min max idiv atoi trim ltrim rtrim
" 路径与文件系统
syntax keyword cinBuiltin path_join path_basename path_dirname path_abs
syntax keyword cinBuiltin file_copy file_move dir_remove is_dir file_mtime
syntax keyword cinBuiltin temp_dir chdir
" 时间与系统信息
syntax keyword cinBuiltin time_ms sleep_ms cpu_count arch_name mem_info
syntax keyword cinBuiltin is_android
" 网络
syntax keyword cinBuiltin http_get http_post download
" 编码与哈希
syntax keyword cinBuiltin sha256 base64_encode base64_decode
" 桌面集成
syntax keyword cinBuiltin clipboard_get clipboard_set notify open_url
" Android / Termux 扩展
syntax keyword cinBuiltin android_intent termux_call termux_share termux_torch
syntax keyword cinBuiltin termux_volume termux_brightness termux_camera_photo
syntax keyword cinBuiltin termux_fingerprint termux_sensor
syntax match cinNumber "\v<0[xX][0-9a-fA-F_]+>"
syntax match cinNumber "\v<0[bB][01_]+>"
syntax match cinNumber "\v<0[oO][0-7_]+>"
syntax match cinNumber "\v<\d[\d_]*([uUlLfF]*)>"
syntax match cinNumber "\v<\d+\.\d+([eE][+-]?\d+)?[fF]?>"
syntax match cinChar "'\\.'\|'[^\\]'"
syntax region cinString start=+"+ skip=+\\"+ end=+"+
syntax match cinComment "//.*$"
syntax region cinComment start="/\*" end="\*/"

highlight default link cinStatement Keyword
highlight default link cinType Type
highlight default link cinConstant Constant
highlight default link cinBuiltin Function
highlight default link cinNumber Number
highlight default link cinChar Character
highlight default link cinString String
highlight default link cinComment Comment

let b:current_syntax = "cin"
