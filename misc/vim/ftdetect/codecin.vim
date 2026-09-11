" 文件类型检测: CIN / UCPU 汇编 (放入 ~/.vim/ftdetect/ 或 packadd)
au BufRead,BufNewFile *.cin  setfiletype cin
au BufRead,BufNewFile *.pl,*.asm setfiletype ucpuasm
