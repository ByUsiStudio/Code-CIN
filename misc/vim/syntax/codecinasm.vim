" Vim 语法: UCPU 汇编 (.asm / PL .pl) (放入 ~/.vim/syntax/ucpuasm.vim)
if exists("b:current_syntax") | finish | endif

syntax match ucpuComment ";.*$" contains=@Spell
syntax match ucpuLabel "^[a-zA-Z_.$][a-zA-Z0-9_.$]*:"
syntax match ucpuDirective "^\s*[.][a-zA-Z][a-zA-Z0-9_.]*"
syntax match ucpuRegister "\v\b[xXrRwW]([0-9]|[12][0-9]|3[01])\b"
syntax match ucpuRegister "\v\b[vV]([0-9]|[12][0-9]|3[01])(\.[0-3])?\b"
syntax keyword ucpuRegister sp fp lr xzr
syntax match ucpuNumber "\v<0[xX][0-9a-fA-F_]+>|\v<0[bB][01_]+>|\v<0[oO][0-7_]+>|\v<\d[\d_]*[uUlLfF]*>"
syntax region ucpuString start=+"+ skip=+\\"+ end=+"+

highlight default link ucpuComment Comment
highlight default link ucpuLabel Label
highlight default link ucpuDirective PreProc
highlight default link ucpuRegister Identifier
highlight default link ucpuNumber Number
highlight default link ucpuString String

let b:current_syntax = "ucpuasm"
