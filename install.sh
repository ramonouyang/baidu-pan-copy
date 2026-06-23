#!/bin/bash
# 百度网盘批量转存工具 - 安装脚本
# Baidu Pan Batch Transfer Tool - Installation Script

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检测操作系统
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        echo "linux"
    else
        echo "unknown"
    fi
}

# 检查 Python 版本
check_python() {
    if command -v python3 &> /dev/null; then
        PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
        PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
        
        if [ "$PYTHON_MAJOR" -ge 3 ] && [ "$PYTHON_MINOR" -ge 8 ]; then
            print_success "Python $PYTHON_VERSION 已安装"
            return 0
        else
            print_error "Python 版本过低: $PYTHON_VERSION (需要 3.8+)"
            return 1
        fi
    else
        print_error "未找到 Python3"
        return 1
    fi
}

# 安装 Python（macOS）
install_python_macos() {
    print_info "正在通过 Homebrew 安装 Python..."
    if ! command -v brew &> /dev/null; then
        print_error "未找到 Homebrew，请先安装: https://brew.sh"
        exit 1
    fi
    brew install python@3.11
}

# 安装 Python（Linux）
install_python_linux() {
    print_info "正在安装 Python3..."
    if command -v apt-get &> /dev/null; then
        sudo apt-get update
        sudo apt-get install -y python3 python3-pip python3-venv
    elif command -v yum &> /dev/null; then
        sudo yum install -y python3 python3-pip
    elif command -v dnf &> /dev/null; then
        sudo dnf install -y python3 python3-pip
    else
        print_error "无法自动安装 Python，请手动安装 Python 3.8+"
        exit 1
    fi
}

# 主安装流程
main() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║       百度网盘批量转存工具 - 安装程序                     ║"
    echo "║       Baidu Pan Batch Transfer Tool Installer             ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""
    
    # 获取安装目录
    INSTALL_DIR="${1:-$HOME/baidu-pan-copy}"
    print_info "安装目录: $INSTALL_DIR"
    
    # 检测操作系统
    OS=$(detect_os)
    print_info "操作系统: $OS"
    
    # 检查 Python
    if ! check_python; then
        read -p "是否自动安装 Python? (y/n) " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            if [ "$OS" == "macos" ]; then
                install_python_macos
            else
                install_python_linux
            fi
        else
            print_error "请手动安装 Python 3.8+ 后重试"
            exit 1
        fi
    fi
    
    # 创建安装目录
    if [ -d "$INSTALL_DIR" ]; then
        print_warning "安装目录已存在: $INSTALL_DIR"
        read -p "是否覆盖? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_info "安装已取消"
            exit 0
        fi
    else
        mkdir -p "$INSTALL_DIR"
    fi
    
    # 复制文件
    print_info "正在复制文件..."
    SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
    
    # 复制核心文件
    cp "$SCRIPT_DIR/main.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/baidu_api.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/start.py" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
    cp "$SCRIPT_DIR/bookmarklet_template.js" "$INSTALL_DIR/"
    
    # 复制目录
    cp -r "$SCRIPT_DIR/templates" "$INSTALL_DIR/"
    
    # 创建虚拟环境
    print_info "正在创建虚拟环境..."
    python3 -m venv "$INSTALL_DIR/venv"
    
    # 安装依赖
    print_info "正在安装依赖..."
    "$INSTALL_DIR/venv/bin/pip" install --upgrade pip
    
    # 询问是否使用国内镜像源
    echo ""
    print_info "是否使用国内镜像源加速安装？（中国大陆用户建议选择 y）"
    read -p "使用国内镜像源? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        print_info "使用清华镜像源安装依赖..."
        "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
    else
        "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"
    fi
    
    # 创建启动脚本
    print_info "正在创建启动脚本..."
    cat > "$INSTALL_DIR/run.sh" << 'EOF'
#!/bin/bash
# 百度网盘批量转存工具 - 启动脚本

cd "$(dirname "$0")"

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "错误: 虚拟环境不存在，请重新运行 install.sh"
    exit 1
fi

# 启动服务
echo "正在启动百度网盘批量转存工具..."
./venv/bin/python start.py
EOF
    chmod +x "$INSTALL_DIR/run.sh"
    
    # 创建停止脚本
    cat > "$INSTALL_DIR/stop.sh" << 'EOF'
#!/bin/bash
# 百度网盘批量转存工具 - 停止脚本

echo "正在停止百度网盘批量转存工具..."
pkill -f "uvicorn main:app" 2>/dev/null || true
echo "已停止"
EOF
    chmod +x "$INSTALL_DIR/stop.sh"
    
    # 创建桌面快捷方式（Linux）
    if [ "$OS" == "linux" ]; then
        DESKTOP_DIR="$HOME/.local/share/applications"
        mkdir -p "$DESKTOP_DIR"
        cat > "$DESKTOP_DIR/baidu-pan-copy.desktop" << EOF
[Desktop Entry]
Name=百度网盘转存工具
Comment=Baidu Pan Batch Transfer Tool
Exec=$INSTALL_DIR/run.sh
Icon=folder-download
Terminal=true
Type=Application
Categories=Utility;
EOF
        print_success "已创建桌面快捷方式"
    fi
    
    # 完成
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║                    安装完成！                             ║"
    echo "╠═══════════════════════════════════════════════════════════╣"
    echo "║  安装目录: $INSTALL_DIR"
    echo "║"
    echo "║  启动方式:"
    echo "║    cd $INSTALL_DIR"
    echo "║    ./run.sh"
    echo "║"
    echo "║  或者直接:"
    echo "║    $INSTALL_DIR/run.sh"
    echo "║"
    echo "║  访问地址: http://localhost:8080"
    echo "║"
    echo "║  停止服务:"
    echo "║    $INSTALL_DIR/stop.sh"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""
}

# 运行主函数
main "$@"
