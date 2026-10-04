; 汇编语法增强: .equ 常量、表达式立即数与符号算术
; 运行: codecin examples/asm_constants.asm
;   结果: x0 = 63, mem[buf + 8*3] = 42 (见下方注释)

.equ ROWS, 4
.equ COLS, 8
.equ CELLS, ROWS * COLS        ; 32
.equ LIMIT, CELLS * 2 - 1      ; 63

.text
main:
    mov x0, LIMIT              ; x0 = 63
    addi x0, x0, 0             ; 占位 (表达式立即数演示在下一行)
    mov x1, buf
    mov x2, 42
    sd x2, [x1, (COLS - 5) * 8]  ; mem[buf + 24] = 42
    halt

.data
buf: .dq 0, 0, 0, 0
