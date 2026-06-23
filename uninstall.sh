#!/bin/bash
# 百度网盘批量转存工具 - 卸载脚本
# Baidu Pan Batch Transfer Tool - Uninstall Script

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

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

# 主卸载流程
main() {
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║       百度网盘批量转存工具 - 卸载程序                     ║"
    echo "║       Baidu Pan Batch Transfer Tool Uninstaller           ║"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""
    
    # 获取安装目录
    INSTALL_DIR="${1:-$HOME/baidu-pan-copy}"
    
    # 检查安装目录是否存在
    if [ ! -d "$INSTALL_DIR" ]; then
        print_warning "安装目录不存在: $INSTALL_DIR"
        print_info "可能已经卸载或安装在其他位置"
        exit 0
    fi
    
    print_info "安装目录: $INSTALL_DIR"
    
    # 检测操作系统
    OS=$(detect_os)
    
    # 停止服务
    print_info "正在停止服务..."
    pkill -f "uvicorn main:app" 2>/dev/null || true
    sleep 1
    
    # 确认卸载
    echo ""
    print_warning "此操作将删除以下内容:"
    echo "  - $INSTALL_DIR"
    echo "  - 所有程序文件、虚拟环境、数据库、日志"
    echo ""
    read -p "确认卸载? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "卸载已取消"
        exit 0
    fi
    
    # 删除桌面快捷方式（Linux）
    if [ "$OS" == "linux" ]; then
        DESKTOP_FILE="$HOME/.local/share/applications/baidu-pan-copy.desktop"
        if [ -f "$DESKTOP_FILE" ]; then
            rm -f "$DESKTOP_FILE"
            print_success "已删除桌面快捷方式"
        fi
    fi
    
    # 删除 systemd 服务（Linux）
    if [ "$OS" == "linux" ]; then
        SERVICE_FILE="$HOME/.config/systemd/user/baidu-pan-copy.service"
        if [ -f "$SERVICE_FILE" ]; then
            systemctl --user stop baidu-pan-copy 2>/dev/null || true
            systemctl --user disable baidu-pan-copy 2>/dev/null || true
            rm -f "$SERVICE_FILE"
            systemctl --user daemon-reload
            print_success "已删除 systemd 服务"
        fi
    fi
    
    # 删除 launchd 服务（macOS）
    if [ "$OS" == "macos" ]; then
        PLIST_FILE="$HOME/Library/LaunchAgents/com.baidupancopy.plist"
        if [ -f "$PLIST_FILE" ]; then
            launchctl unload "$PLIST_FILE" 2>/dev/null || true
            rm -f "$PLIST_FILE"
            print_success "已删除 launchd 服务"
        fi
    fi
    
    # 删除安装目录
    print_info "正在删除安装目录..."
    rm -rf "$INSTALL_DIR"
    
    # 完成
    echo ""
    echo "╔═══════════════════════════════════════════════════════════╗"
    echo "║                    卸载完成！                             ║"
    echo "╠═══════════════════════════════════════════════════════════╣"
    echo "║  百度网盘批量转存工具已成功卸载                           ║"
    echo "║"
    echo "║  如需重新安装，请运行:"
    echo "║    ./install.sh"
    echo "╚═══════════════════════════════════════════════════════════╝"
    echo ""
}

# 运行主函数
main "$@"
