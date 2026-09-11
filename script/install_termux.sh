#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[0;37m'
NC='\033[0m'
BOLD='\033[1m'
DIM='\033[2m'
UNDERLINE='\033[4m'

info()    { echo -e "${BLUE}ℹ${NC} $1"; }
success() { echo -e "${GREEN}✔${NC} $1"; }
error()   { echo -e "${RED}✘${NC} $1"; }
warning() { echo -e "${YELLOW}⚠${NC} $1"; }
step()    { echo -e "\n${CYAN}━━━ ${BOLD}[$1]${NC} ${CYAN}$2${NC}"; }
line()    { echo -e "${DIM}═══════════════════════════════════════════════════════════${NC}"; }
thin_line() { echo -e "${DIM}───────────────────────────────────────────────────────────────${NC}"; }

start_time=$(date +%s)

clear
echo -e "${MAGENTA}${BOLD}欢迎使用 UCPU 程序${NC}"
echo -e "${DIM}启动时间: $(date '+%Y-%m-%d %H:%M:%S')${NC}"
line
echo -e "${WHITE}${BOLD}系统环境${NC}"
echo -e "  用户    : ${GREEN}$(whoami)${NC} @ ${YELLOW}$(hostname)${NC}"
echo -e "  工作目录: ${BLUE}$(pwd)${NC}"
echo -e "  系统    : ${CYAN}$(uname -s) $(uname -r) $(uname -m)${NC}"
line

total_steps=8
current_step=0

step_func() {
    ((current_step++))
    step "${current_step}/${total_steps}" "$1"
    thin_line
    step_start=$(date +%s)
}

finish_step() {
    local exit_code=$?
    local step_end=$(date +%s)
    local duration=$((step_end - step_start))
    if [ $exit_code -eq 0 ]; then
        success "$1 完成 (耗时 ${duration}秒)"
    else
        error "$1 失败 (退出码: $exit_code)"
        exit $exit_code
    fi
    thin_line
}

step_func "更新软件包列表"
pkg update -y
finish_step "更新软件包列表"

step_func "安装基础工具 (git, python, pip, uv)"
pkg install -y git python python-pip uv
finish_step "安装基础工具"

step_func "创建工作目录"
WORK_DIR="$HOME/.ByUsi Studio/UCPU"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"
success "工作目录: $(pwd)"
thin_line
true
finish_step "创建工作目录"

step_func "克隆或更新 UCPU 仓库"
if [ -n "$(ls -A .)" ]; then
    warning "当前目录非空，已存在 UCPU 项目文件。"
    if git rev-parse --git-dir >/dev/null 2>&1; then
        echo -n "是否进行拉取更新？ (y/N): "
        read answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            success "用户选择拉取更新"
            git pull
            if [ $? -ne 0 ]; then
                error "拉取更新失败"
                exit 1
            fi
            success "更新完成"
        else
            error "用户取消安装"
            exit 1
        fi
    else
        warning "当前目录不是有效的 git 仓库，无法拉取更新。"
        echo -n "是否清空目录并重新克隆？ (y/N): "
        read answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            success "用户选择清空目录"
            find . -mindepth 1 -delete
            success "目录已清空"
            git clone https://gitee.com/byusistudio/ucpu .
            if [ $? -ne 0 ]; then
                error "克隆失败"
                exit 1
            fi
        else
            error "用户取消安装"
            exit 1
        fi
    fi
else
    info "目录为空，直接克隆"
    git clone https://gitee.com/byusistudio/ucpu .
    if [ $? -ne 0 ]; then
        error "克隆失败"
        exit 1
    fi
fi
finish_step "克隆或更新 UCPU 仓库"

step_func "创建/更新虚拟环境"
if [ ! -d ".venv" ]; then
    info "虚拟环境不存在，正在创建..."
    uv venv
    if [ $? -ne 0 ]; then
        error "创建虚拟环境失败"
        exit 1
    fi
else
    info "虚拟环境已存在，跳过创建。"
fi
finish_step "创建/更新虚拟环境"

step_func "同步 Python 依赖 (uv sync)"
uv sync
if [ $? -ne 0 ]; then
    error "依赖同步失败"
    exit 1
fi
finish_step "同步 Python 依赖"

step_func "生成启动脚本 (ucpu-cli)"
cat > ucpu-cli << 'EOF'
#!/bin/bash
original_pwd="$PWD"
cd "$(dirname "$0")" || exit
args=()
for arg in "$@"; do
    if [[ "$arg" == -* ]]; then
        args+=("$arg")
    elif [[ "$arg" = /* ]]; then
        args+=("$arg")
    elif [[ "$arg" =~ ^[0-9]+$ ]]; then
        args+=("$arg")
    else
        args+=("$original_pwd/$arg")
    fi
done
uv run python cpu.py "${args[@]}"
EOF
chmod +x ucpu-cli
success "启动脚本已创建: $(pwd)/ucpu-cli"
thin_line
true
finish_step "生成启动脚本"

step_func "配置环境变量"
UCPU_INSTALL="$HOME/../usr/etc/profile.d/ucpu_init.sh"
mkdir -p "$(dirname "$UCPU_INSTALL")"
PROJECT_ROOT="$(pwd)"
echo "export PATH=\"\$PATH:$PROJECT_ROOT\"" > "$UCPU_INSTALL"
chmod +x "$UCPU_INSTALL"
success "环境变量已添加到 $UCPU_INSTALL"
thin_line
true
finish_step "配置环境变量"

end_time=$(date +%s)
total_duration=$((end_time - start_time))

line
echo -e "${GREEN}${BOLD}✓ 所有步骤执行完毕！${NC}"
echo -e "${DIM}总耗时: ${total_duration}秒${NC}"
echo -e "${DIM}完成时间: $(date '+%Y-%m-%d %H:%M:%S')${NC}"
line
echo -e "${YELLOW}提示: 请运行以下命令使环境变量生效:${NC}"
echo -e "  ${BOLD}source $UCPU_INSTALL${NC}"
echo -e "或重新打开终端。"
echo -e "之后您可以直接在任意位置执行 ${GREEN}ucpu-cli${NC} 命令。"