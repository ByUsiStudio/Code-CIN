" Vim 语法: Code CIN 汇编 (.asm / PL .pl) (放入 ~/.vim/syntax/codecinasm.vim)
if exists("b:current_syntax") | finish | endif

syntax match codecinComment ";.*$" contains=@Spell
syntax match codecinLabel "^[a-zA-Z_.$][a-zA-Z0-9_.$]*:"
syntax match codecinDirective "^\s*[.][a-zA-Z][a-zA-Z0-9_.]*"
syntax match codecinRegister "\v\b[xXrRwW]([0-9]|[12][0-9]|3[01])\b"
syntax match codecinRegister "\v\b[vV]([0-9]|[12][0-9]|3[01])(\.[0-3])?\b"
syntax keyword codecinRegister sp fp lr xzr
syntax match codecinNumber "\v<0[xX][0-9a-fA-F_]+>|\v<0[bB][01_]+>|\v<0[oO][0-7_]+>|\v<\d[\d_]*[uUlLfF]*>"
syntax region codecinString start=+"+ skip=+\\"+ end=+"+

highlight default link codecinComment Comment
highlight default link codecinLabel Label
highlight default link codecinDirective PreProc
highlight default link codecinRegister Identifier
highlight default link codecinNumber Number
highlight default link codecinString String

let b:current_syntax = "codecinasm"
