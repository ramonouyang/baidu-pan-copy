# 部署指南 / Deployment Guide

<p align="center">
  <strong>🌐 Language / 语言</strong><br>
  <a href="#中文">中文</a> | <a href="#english">English</a>
</p>

---

# 中文

## 快速安装

### 方式一：一键安装（推荐）

**macOS / Linux:**

```bash
# 克隆项目
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

# 运行安装脚本
chmod +x install.sh
./install.sh
```

**Windows:**

```cmd
REM 克隆项目（需要先安装 Git）
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

REM 双击 install.bat 运行安装脚本
install.bat
```

安装脚本会自动：
- 检查 Python 环境
- 创建虚拟环境
- 安装依赖
- 创建启动/停止脚本
- 创建桌面快捷方式（Windows）

### 方式二：手动安装

```bash
# 克隆项目
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 启动服务
python start.py
```

## 启动服务

### 前台运行

```bash
cd ~/baidu-pan-copy
./run.sh
```

### 后台运行（Linux systemd）

```bash
# 复制服务文件
mkdir -p ~/.config/systemd/user
cp deploy/baidu-pan-copy.service ~/.config/systemd/user/

# 编辑服务文件，将 USERNAME 替换为你的用户名
sed -i "s/USERNAME/$(whoami)/g" ~/.config/systemd/user/baidu-pan-copy.service

# 启用并启动服务
systemctl --user enable baidu-pan-copy
systemctl --user start baidu-pan-copy

# 查看状态
systemctl --user status baidu-pan-copy

# 查看日志
journalctl --user -u baidu-pan-copy -f
```

### 后台运行（macOS launchd）

```bash
# 复制 plist 文件
cp deploy/com.baidupancopy.plist ~/Library/LaunchAgents/

# 编辑 plist 文件，将 USERNAME 替换为你的用户名
sed -i "" "s/USERNAME/$(whoami)/g" ~/Library/LaunchAgents/com.baidupancopy.plist

# 创建日志目录
mkdir -p ~/baidu-pan-copy/logs

# 加载服务
launchctl load ~/Library/LaunchAgents/com.baidupancopy.plist

# 查看状态
launchctl list | grep baidupancopy

# 停止服务
launchctl unload ~/Library/LaunchAgents/com.baidupancopy.plist
```

## 停止服务

### 前台运行

按 `Ctrl+C` 停止

### 后台运行

```bash
# 使用停止脚本
./stop.sh

# 或者手动停止
pkill -f "uvicorn main:app"
```

## 卸载

```bash
# 运行卸载脚本
chmod +x uninstall.sh
./uninstall.sh
```

卸载脚本会：
- 停止服务
- 删除 systemd/launchd 服务（如果存在）
- 删除安装目录

## 更新

```bash
# 进入安装目录
cd ~/baidu-pan-copy

# 拉取最新代码
git pull

# 激活虚拟环境
source venv/bin/activate

# 更新依赖
pip install -r requirements.txt

# 重启服务
./stop.sh
./run.sh
```

## 配置

### 端口配置

默认端口：8080

如果端口被占用，程序会自动尝试下一个端口（8081, 8082, ...）

要指定端口，修改 `start.py` 中的 `PORT` 变量。

### 数据目录

- 数据库：`~/baidu-pan-copy/tasks.db`
- 日志：`~/baidu-pan-copy/baidu_api.log`

## 故障排查

### 端口被占用

```bash
# 查看占用端口的进程
lsof -i :8080

# 杀掉进程
kill -9 <PID>
```

### 权限问题

```bash
# 确保脚本有执行权限
chmod +x install.sh uninstall.sh run.sh stop.sh
```

### Python 版本问题

```bash
# 检查 Python 版本
python3 --version

# 如果版本低于 3.8，请升级
# macOS: brew install python@3.11
# Ubuntu: sudo apt install python3.11
```

## 🇨🇳 中国大陆部署指南

### 网络问题备选方案

#### 1. pip 安装使用国内镜像源

```bash
# 临时使用
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 永久配置
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

**推荐镜像源：**
- 清华大学：https://pypi.tuna.tsinghua.edu.cn/simple
- 阿里云：https://mirrors.aliyun.com/pypi/simple
- 豆瓣：https://pypi.douban.com/simple
- 中科大：https://pypi.mirrors.ustc.edu.cn/simple

#### 2. Python 下载（Windows）

如果自动下载失败，可以从以下地址手动下载：
- 官方：https://www.python.org/downloads/
- 国内镜像：https://registry.npmmirror.com/binary.html?path=python/

#### 3. Git 克隆加速

```bash
# 使用 GitHub 镜像站
git clone https://ghproxy.com/https://github.com/your-username/baidu-pan-copy.git

# 或使用 Gitee 镜像（如果已配置）
git clone https://gitee.com/your-username/baidu-pan-copy.git
```

#### 4. 设置 pip 全局镜像源

```bash
# 创建/编辑 pip 配置文件
# Linux/macOS: ~/.pip/pip.conf
# Windows: %APPDATA%\pip\pip.ini

[global]
index-url = https://pypi.tuna.tsinghua.edu.cn/simple
trusted-host = pypi.tuna.tsinghua.edu.cn
```

---

# English

## Quick Installation

### Method 1: One-click Install (Recommended)

```bash
# Clone the project
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

# Run installation script
chmod +x install.sh
./install.sh
```

The installation script will automatically:
- Check Python environment
- Create virtual environment
- Install dependencies
- Create start/stop scripts

### Method 2: Manual Installation

```bash
# Clone the project
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start service
python start.py
```

## Start Service

### Foreground

```bash
cd ~/baidu-pan-copy
./run.sh
```

### Background (Linux systemd)

```bash
# Copy service file
mkdir -p ~/.config/systemd/user
cp deploy/baidu-pan-copy.service ~/.config/systemd/user/

# Edit service file, replace USERNAME with your username
sed -i "s/USERNAME/$(whoami)/g" ~/.config/systemd/user/baidu-pan-copy.service

# Enable and start service
systemctl --user enable baidu-pan-copy
systemctl --user start baidu-pan-copy

# Check status
systemctl --user status baidu-pan-copy

# View logs
journalctl --user -u baidu-pan-copy -f
```

### Background (macOS launchd)

```bash
# Copy plist file
cp deploy/com.baidupancopy.plist ~/Library/LaunchAgents/

# Edit plist file, replace USERNAME with your username
sed -i "" "s/USERNAME/$(whoami)/g" ~/Library/LaunchAgents/com.baidupancopy.plist

# Create logs directory
mkdir -p ~/baidu-pan-copy/logs

# Load service
launchctl load ~/Library/LaunchAgents/com.baidupancopy.plist

# Check status
launchctl list | grep baidupancopy

# Stop service
launchctl unload ~/Library/LaunchAgents/com.baidupancopy.plist
```

## Stop Service

### Foreground

Press `Ctrl+C` to stop

### Background

```bash
# Use stop script
./stop.sh

# Or manually
pkill -f "uvicorn main:app"
```

## Uninstall

```bash
# Run uninstall script
chmod +x uninstall.sh
./uninstall.sh
```

The uninstall script will:
- Stop service
- Remove systemd/launchd service (if exists)
- Remove installation directory

## Update

```bash
# Enter installation directory
cd ~/baidu-pan-copy

# Pull latest code
git pull

# Activate virtual environment
source venv/bin/activate

# Update dependencies
pip install -r requirements.txt

# Restart service
./stop.sh
./run.sh
```

## Configuration

### Port Configuration

Default port: 8080

If the port is occupied, the program will automatically try the next port (8081, 8082, ...)

To specify a port, modify the `PORT` variable in `start.py`.

### Data Directory

- Database: `~/baidu-pan-copy/tasks.db`
- Logs: `~/baidu-pan-copy/baidu_api.log`

## Troubleshooting

### Port Occupied

```bash
# Check which process is using the port
lsof -i :8080

# Kill the process
kill -9 <PID>
```

### Permission Issues

```bash
# Ensure scripts have execute permission
chmod +x install.sh uninstall.sh run.sh stop.sh
```

### Python Version Issues

```bash
# Check Python version
python3 --version

# If version is below 3.8, please upgrade
# macOS: brew install python@3.11
# Ubuntu: sudo apt install python3.11
```
