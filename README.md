<p align="center">
  <h1 align="center">百度网盘批量转存工具</h1>
  <p align="center">Baidu Pan Batch Transfer Tool</p>
  <p align="center">
    <img src="https://img.shields.io/badge/version-1.1.23-blue.svg" alt="version">
    <img src="https://img.shields.io/badge/python-3.8+-green.svg" alt="python">
    <img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="license">
  </p>
</p>

<p align="center">
  <strong>🌐 Language / 语言</strong><br>
  <a href="#中文">中文</a> | <a href="#english">English</a>
</p>

---

# 中文

## ✨ 功能特性

### 核心功能
- ✅ 支持公开分享链接（`pan.baidu.com/s/xxx`）
- ✅ 支持带提取码的分享链接
- ✅ **批量链接支持**（一次处理多个分享链接）
- ✅ 自动分批处理（每批100个文件，智能限流）
- ✅ 自定义目标路径
- ✅ 保持目录结构

### 任务管理
- ✅ 实时进度显示（文件数、速度、耗时）
- ✅ **任务暂停/继续**
- ✅ **断点续传**（服务器重启后可恢复）
- ✅ **智能重复检测**（防止重复转存）
- ✅ 失败自动重试（指数退避）
- ✅ 任务删除（单个/批量）

### 用户体验
- ✅ **深色模式支持**
- ✅ **新手引导教程**
- ✅ **数据统计面板**
- ✅ **中英文双语界面**
- ✅ Cookie 获取书签小工具
- ✅ 日志导出
- ✅ 响应式设计

### 错误处理
- ✅ 智能限流检测（errno -62/-9）
- ✅ Cookie 过期自动检测
- ✅ 友好的错误提示
- ✅ 详细的错误日志

## 🚀 快速开始

### 前置要求

- Python 3.8 或更高版本
- 百度网盘账号

### 安装

#### 方式一：一键安装（推荐）

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

#### 方式二：手动安装

```bash
# 克隆项目
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

# 创建虚拟环境（推荐）
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

### 启动

```bash
# 如果使用一键安装
~/baidu-pan-copy/run.sh        # macOS/Linux
%USERPROFILE%\baidu-pan-copy\run.bat  # Windows（双击或命令行）

# 如果使用手动安装
python start.py
```

服务启动后访问：http://localhost:8080

> 💡 默认端口 8080，如果被占用会自动尝试下一个端口

### 🇨🇳 中国大陆用户

如果安装过程中遇到网络问题，可以使用国内镜像源：

```bash
# 手动安装时使用国内镜像源
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

**常见国内镜像源：**
| 镜像源 | 地址 |
|--------|------|
| 清华大学 | https://pypi.tuna.tsinghua.edu.cn/simple |
| 阿里云 | https://mirrors.aliyun.com/pypi/simple |
| 豆瓣 | https://pypi.douban.com/simple |
| 中科大 | https://pypi.mirrors.ustc.edu.cn/simple |

> 💡 一键安装脚本会自动询问是否使用国内镜像源

### 获取百度网盘 Cookie

1. 打开浏览器，登录 [百度网盘](https://pan.baidu.com)
2. 点击页面上的「获取网盘Cookie」书签（首次使用会显示引导）
3. 按提示获取 Cookie 并粘贴

> 💡 **提示**：首次使用会显示新手引导教程

## 📖 使用说明

### 单个链接模式

1. **粘贴 Cookie** → 点击"验证"
2. **输入分享链接**（如 `https://pan.baidu.com/s/1abc123`）
3. **输入提取码**（如有）
4. **设置目标路径**（如 `/我的资源/转存文件`）
5. 点击"解析分享链接" → 预览文件列表
6. 点击"开始转存" → 等待完成

### 批量链接模式

1. 切换到"批量链接"标签
2. 输入多个链接（每行一个，格式：`链接 提取码`）
3. 选择是否为每个分享创建子目录
4. 点击"解析批量链接" → 预览结果
5. 点击"开始批量转存" → 自动处理

### 任务恢复

如果服务器重启或网络中断，未完成的任务会标记为"可恢复"：

1. 在历史任务列表中找到"可恢复"状态的任务
2. 点击"恢复"按钮
3. 任务会从断点继续执行，已转存的文件不会重复

## 🛠️ 技术架构

```
┌─────────────────────────────────────────┐
│           Web 前端 (HTML/JS)            │
│      TailwindCSS + Alpine.js            │
│      深色模式 + i18n 国际化              │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│         FastAPI 后端 (Python)            │
│  ┌──────────┐  ┌──────────┐  ┌───────┐ │
│  │ 百度API  │  │ 任务队列 │  │ SQLite│ │
│  │  封装    │  │ 分批处理 │  │ 进度  │ │
│  │ 重试机制 │  │ 暂停继续 │  │ 统计  │ │
│  └──────────┘  └──────────┘  └───────┘ │
└─────────────────────────────────────────┘
```

## 📊 分批策略

- 每批最多 **100 个文件**
- 智能限流：1.5 QPS，300 请求/180 秒窗口
- 单文件失败自动重试 **3 次**（指数退避）
- 网络错误自动重试
- 失败文件记录到日志

## ⌨️ 快捷键

| 快捷键 | 功能 |
|--------|------|
| `Ctrl+V` | 粘贴 Cookie 或链接 |
| `Enter` | 确认输入 |
| `Esc` | 关闭弹窗 |

## 📝 注意事项

1. **Cookie 有效期**：约 30 天，过期需重新获取
2. **会员限制**：非会员单次转存可能有容量限制
3. **风控机制**：频繁操作可能触发验证码
4. **网络要求**：需要能访问 pan.baidu.com

## 📁 文件结构

```
baidu-pan-copy/
├── main.py              # FastAPI 主应用
├── baidu_api.py         # 百度网盘 API 封装
├── start.py             # 启动脚本（自动查找端口）
├── requirements.txt     # Python 依赖
├── templates/
│   └── index.html       # Web 前端界面
├── docs/                # 文档
└── README.md            # 本文件
```

## 🔌 API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/cookie/validate` | POST | 验证 Cookie 有效性 |
| `/api/cookie/set` | POST | 设置 Cookie（书签小工具） |
| `/api/share/parse` | POST | 解析单个分享链接 |
| `/api/batch/parse` | POST | 解析批量分享链接 |
| `/api/task/{id}/start` | POST | 启动转存任务 |
| `/api/task/{id}/pause` | POST | 暂停任务 |
| `/api/task/{id}/resume` | POST | 恢复任务 |
| `/api/task/{id}/recover` | POST | 恢复中断任务 |
| `/api/task/{id}/progress` | GET | 获取任务进度 |
| `/api/task/{id}/summary` | GET | 获取任务总结 |
| `/api/task/{id}` | DELETE | 删除任务 |
| `/api/tasks` | GET | 获取历史任务 |
| `/api/tasks/clear` | DELETE | 清空历史任务 |
| `/api/stats` | GET | 获取统计数据 |

## 🐛 故障排查

### Cookie 无效
- 确保 Cookie 完整（包含 BDUSS、STOKEN 等）
- 重新登录百度网盘获取新 Cookie
- 检查 Cookie 是否过期（约 30 天有效期）

### 转存失败
- 检查分享链接是否有效
- 检查提取码是否正确
- 检查目标路径是否存在
- 查看日志了解具体错误原因

### 速度慢
- 百度网盘 API 有频率限制
- 批次间隔会自动调整
- 网络不稳定会自动重试

### 请求过于频繁（errno -62）
- 等待 30-120 秒后重试
- 工具会自动检测限流并保存断点
- 可以稍后恢复任务继续

## 📈 开发说明

```bash
# 安装依赖
pip install -r requirements.txt

# 开发模式运行（自动重载）
uvicorn main:app --reload --port 8080

# 查看日志
tail -f baidu_api.log
```

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解贡献指南。

## 📄 许可证

本项目采用 [MIT 许可证](LICENSE)。

---

# English

## ✨ Features

### Core Features
- ✅ Support public share links (`pan.baidu.com/s/xxx`)
- ✅ Support share links with extraction code
- ✅ **Batch link support** (process multiple share links at once)
- ✅ Automatic batch processing (100 files per batch, smart rate limiting)
- ✅ Custom target path
- ✅ Preserve directory structure

### Task Management
- ✅ Real-time progress display (file count, speed, elapsed time)
- ✅ **Task pause/resume**
- ✅ **Checkpoint recovery** (recoverable after server restart)
- ✅ **Smart duplicate detection** (prevent duplicate transfers)
- ✅ Automatic retry on failure (exponential backoff)
- ✅ Task deletion (single/batch)

### User Experience
- ✅ **Dark mode support**
- ✅ **Beginner tutorial**
- ✅ **Statistics dashboard**
- ✅ **Chinese/English bilingual interface**
- ✅ Cookie bookmarklet
- ✅ Log export
- ✅ Responsive design

### Error Handling
- ✅ Smart rate limit detection (errno -62/-9)
- ✅ Automatic cookie expiration detection
- ✅ Friendly error messages
- ✅ Detailed error logs

## 🚀 Quick Start

### Prerequisites

- Python 3.8 or higher
- Baidu Pan account

### Installation

#### Method 1: One-click Install (Recommended)

**macOS / Linux:**

```bash
# Clone the project
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

# Run installation script
chmod +x install.sh
./install.sh
```

**Windows:**

```cmd
REM Clone the project (Git required)
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

REM Double-click install.bat to run installation script
install.bat
```

#### Method 2: Manual Installation

```bash
# Clone the project
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# or venv\Scripts\activate  # Windows

# Install dependencies
pip install -r requirements.txt
```

### Start

```bash
# If using one-click install
~/baidu-pan-copy/run.sh        # macOS/Linux
%USERPROFILE%\baidu-pan-copy\run.bat  # Windows (double-click or command line)

# If using manual installation
python start.py
```

After the service starts, visit: http://localhost:8080

> 💡 Default port is 8080. If occupied, it will automatically try the next port

### 🇨🇳 China Mainland Users

If you encounter network issues during installation, use a mirror source:

```bash
# Manual installation with mirror source
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

**Common Mirror Sources in China:**
| Mirror | URL |
|--------|-----|
| Tsinghua University | https://pypi.tuna.tsinghua.edu.cn/simple |
| Alibaba Cloud | https://mirrors.aliyun.com/pypi/simple |
| Douban | https://pypi.douban.com/simple |
| USTC | https://pypi.mirrors.ustc.edu.cn/simple |

> 💡 The installation script will automatically ask if you want to use a mirror source

### Get Baidu Pan Cookie

1. Open browser and log in to [Baidu Pan](https://pan.baidu.com)
2. Click the "Get Cookie" bookmarklet on the page (tutorial will show on first use)
3. Follow the instructions to get and paste the Cookie

> 💡 **Tip**: A beginner tutorial will be shown on first use

## 📖 Usage

### Single Link Mode

1. **Paste Cookie** → Click "Verify"
2. **Enter share link** (e.g., `https://pan.baidu.com/s/1abc123`)
3. **Enter extraction code** (if any)
4. **Set target path** (e.g., `/my_resources/transferred`)
5. Click "Parse Share Link" → Preview file list
6. Click "Start Transfer" → Wait for completion

### Batch Link Mode

1. Switch to "Batch Links" tab
2. Enter multiple links (one per line, format: `link extraction_code`)
3. Choose whether to create subdirectory for each share
4. Click "Parse Batch Links" → Preview results
5. Click "Start Batch Transfer" → Automatic processing

### Task Recovery

If the server restarts or network disconnects, incomplete tasks will be marked as "Recoverable":

1. Find the "Recoverable" task in the history list
2. Click the "Recover" button
3. The task will continue from the checkpoint, already transferred files won't be duplicated

## 🛠️ Architecture

```
┌─────────────────────────────────────────┐
│           Web Frontend (HTML/JS)        │
│      TailwindCSS + Alpine.js            │
│      Dark mode + i18n                   │
└─────────────────────────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────┐
│         FastAPI Backend (Python)        │
│  ┌──────────┐  ┌──────────┐  ┌───────┐ │
│  │ Baidu API│  │ Task Queue│  │ SQLite│ │
│  │ Wrapper  │  │ Batch    │  │ Progress│
│  │ Retry    │  │ Pause    │  │ Stats  │ │
│  └──────────┘  └──────────┘  └───────┘ │
└─────────────────────────────────────────┘
```

## 📊 Batch Strategy

- Max **100 files** per batch
- Smart rate limiting: 1.5 QPS, 300 requests/180s window
- Automatic retry on failure **3 times** (exponential backoff)
- Automatic retry on network errors
- Failed files logged

## ⌨️ Keyboard Shortcuts

| Shortcut | Function |
|----------|----------|
| `Ctrl+V` | Paste Cookie or link |
| `Enter` | Confirm input |
| `Esc` | Close modal |

## 📝 Notes

1. **Cookie validity**: About 30 days, need to re-obtain when expired
2. **Member limits**: Non-members may have capacity limits per transfer
3. **Rate limiting**: Frequent operations may trigger CAPTCHA
4. **Network**: Requires access to pan.baidu.com

## 📁 File Structure

```
baidu-pan-copy/
├── main.py              # FastAPI main application
├── baidu_api.py         # Baidu Pan API wrapper
├── start.py             # Startup script (auto port detection)
├── requirements.txt     # Python dependencies
├── templates/
│   └── index.html       # Web frontend
├── docs/                # Documentation
└── README.md            # This file
```

## 🔌 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/cookie/validate` | POST | Validate Cookie |
| `/api/cookie/set` | POST | Set Cookie (bookmarklet) |
| `/api/share/parse` | POST | Parse single share link |
| `/api/batch/parse` | POST | Parse batch share links |
| `/api/task/{id}/start` | POST | Start transfer task |
| `/api/task/{id}/pause` | POST | Pause task |
| `/api/task/{id}/resume` | POST | Resume task |
| `/api/task/{id}/recover` | POST | Recover interrupted task |
| `/api/task/{id}/progress` | GET | Get task progress |
| `/api/task/{id}/summary` | GET | Get task summary |
| `/api/task/{id}` | DELETE | Delete task |
| `/api/tasks` | GET | Get history tasks |
| `/api/tasks/clear` | DELETE | Clear history tasks |
| `/api/stats` | GET | Get statistics |

## 🐛 Troubleshooting

### Invalid Cookie
- Ensure Cookie is complete (contains BDUSS, STOKEN, etc.)
- Re-login to Baidu Pan to get new Cookie
- Check if Cookie has expired (about 30 days validity)

### Transfer Failed
- Check if share link is valid
- Check if extraction code is correct
- Check if target path exists
- View logs for specific error reasons

### Slow Speed
- Baidu Pan API has rate limits
- Batch interval will auto-adjust
- Network instability will auto-retry

### Too Many Requests (errno -62)
- Wait 30-120 seconds before retrying
- The tool will auto-detect rate limits and save checkpoints
- You can resume the task later

## 📈 Development

```bash
# Install dependencies
pip install -r requirements.txt

# Development mode (auto-reload)
uvicorn main:app --reload --port 8080

# View logs
tail -f baidu_api.log
```

## 🤝 Contributing

Issues and Pull Requests are welcome!

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for contributing guidelines.

## 📄 License

This project is licensed under the [MIT License](LICENSE).
