" Vim 语法: Code CIN 语言 (放入 ~/.vim/syntax/cin.vim)
" 粗粒度高亮, 详细文法见 docs/CIN_GUIDE.md
if exists("b:current_syntax") | finish | endif

syntax keyword cinStatement function return if else while for do switch case
syntax keyword cinStatement default break continue
syntax keyword cinType int float bool string void char short long unsigned struct
syntax keyword cinConstant true false
syntax keyword cinBuiltin print println input abs sqrt pow sin cos tan rand
syntax keyword cinBuiltin srand time strlen strcmp strcpy int_to_str float_to_str
syntax keyword cinBuiltin bool_to_str itoa ftoa substr indexof upper lower
syntax keyword cinBuiltin floor ceil round min max idiv atoi trim ltrim rtrim
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
