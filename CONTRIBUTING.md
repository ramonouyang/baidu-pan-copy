# 贡献指南 / Contributing Guide

<p align="center">
  <strong>🌐 Language / 语言</strong><br>
  <a href="#中文">中文</a> | <a href="#english">English</a>
</p>

---

# 中文

感谢你对本项目的关注！我们欢迎任何形式的贡献。

## 如何贡献

### 报告 Bug

1. 在 [Issues](https://github.com/your-username/baidu-pan-copy/issues) 页面创建新 Issue
2. 使用 Bug 报告模板
3. 提供详细的问题描述、复现步骤、期望行为和实际行为
4. 如果可能，附上日志截图或错误信息

### 提交功能建议

1. 在 [Issues](https://github.com/your-username/baidu-pan-copy/issues) 页面创建新 Issue
2. 使用功能建议模板
3. 详细描述你希望添加的功能和使用场景

### 提交代码

1. Fork 本项目
2. 创建你的特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交你的改动 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 开发环境设置

### 前置要求

- Python 3.8 或更高版本
- Git

### 设置步骤

```bash
# 1. Fork 并克隆项目
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动开发服务器
python start.py
```

### 项目结构

```
baidu-pan-copy/
├── main.py              # FastAPI 主应用（路由、任务管理）
├── baidu_api.py         # 百度网盘 API 封装
├── start.py             # 启动脚本
├── requirements.txt     # Python 依赖
├── templates/
│   └── index.html       # Web 前端（Alpine.js + TailwindCSS）
├── docs/                # 文档
└── tests/               # 测试文件
```

## 代码规范

### Python 代码

- 遵循 [PEP 8](https://peps.python.org/pep-0008/) 代码规范
- 使用 4 空格缩进
- 函数和类添加 docstring
- 变量名使用 snake_case
- 常量使用 UPPER_CASE

### 前端代码

- HTML 使用 4 空格缩进
- JavaScript 使用 4 空格缩进
- CSS 类名使用 TailwindCSS 工具类
- 使用 Alpine.js 进行状态管理

### 提交信息

使用语义化提交信息：

```
<type>(<scope>): <subject>

<body>

<footer>
```

类型（type）：
- `feat`: 新功能
- `fix`: 修复 Bug
- `docs`: 文档更新
- `style`: 代码格式调整
- `refactor`: 重构
- `test`: 测试相关
- `chore`: 构建/工具相关

示例：
```
feat(transfer): add batch transfer support

- Support multiple share links in one request
- Auto-create subdirectory for each share
- Progress tracking for batch tasks

Closes #123
```

## 测试

### 运行测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_transfer.py

# 运行带覆盖率的测试
pytest --cov=.
```

### 编写测试

- 为新功能编写单元测试
- 为 Bug 修复编写回归测试
- 测试文件放在 `tests/` 目录
- 测试文件名以 `test_` 开头

## Pull Request 指南

### 提交前检查

- [ ] 代码遵循项目规范
- [ ] 添加了必要的测试
- [ ] 测试全部通过
- [ ] 更新了相关文档
- [ ] 提交信息符合规范

### PR 描述

请在 PR 描述中包含：

1. 改动的目的和背景
2. 改动的具体内容
3. 相关的 Issue 编号
4. 测试结果截图（如适用）

## 行为准则

- 尊重所有参与者
- 接受建设性批评
- 专注于对社区最有利的事情
- 对他人表示同理心

## 问题？

如有任何问题，欢迎在 [Issues](https://github.com/your-username/baidu-pan-copy/issues) 页面提问。

---

# English

Thank you for your interest in this project! We welcome any form of contribution.

## How to Contribute

### Report Bugs

1. Create a new Issue on the [Issues](https://github.com/your-username/baidu-pan-copy/issues) page
2. Use the Bug Report template
3. Provide detailed problem description, steps to reproduce, expected behavior, and actual behavior
4. If possible, attach log screenshots or error messages

### Submit Feature Requests

1. Create a new Issue on the [Issues](https://github.com/your-username/baidu-pan-copy/issues) page
2. Use the Feature Request template
3. Describe the feature you want to add and its use cases in detail

### Submit Code

1. Fork the project
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Create a Pull Request

## Development Environment Setup

### Prerequisites

- Python 3.8 or higher
- Git

### Setup Steps

```bash
# 1. Fork and clone the project
git clone https://github.com/your-username/baidu-pan-copy.git
cd baidu-pan-copy

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# or venv\Scripts\activate  # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start development server
python start.py
```

### Project Structure

```
baidu-pan-copy/
├── main.py              # FastAPI main app (routes, task management)
├── baidu_api.py         # Baidu Pan API wrapper
├── start.py             # Startup script
├── requirements.txt     # Python dependencies
├── templates/
│   └── index.html       # Web frontend (Alpine.js + TailwindCSS)
├── docs/                # Documentation
└── tests/               # Test files
```

## Code Standards

### Python Code

- Follow [PEP 8](https://peps.python.org/pep-0008/) style guide
- Use 4-space indentation
- Add docstrings to functions and classes
- Use snake_case for variable names
- Use UPPER_CASE for constants

### Frontend Code

- Use 4-space indentation for HTML
- Use 4-space indentation for JavaScript
- Use TailwindCSS utility classes for CSS
- Use Alpine.js for state management

### Commit Messages

Use semantic commit messages:

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation update
- `style`: Code formatting
- `refactor`: Refactoring
- `test`: Test related
- `chore`: Build/tool related

Example:
```
feat(transfer): add batch transfer support

- Support multiple share links in one request
- Auto-create subdirectory for each share
- Progress tracking for batch tasks

Closes #123
```

## Testing

### Run Tests

```bash
# Run all tests
pytest

# Run specific test
pytest tests/test_transfer.py

# Run tests with coverage
pytest --cov=.
```

### Write Tests

- Write unit tests for new features
- Write regression tests for bug fixes
- Place test files in `tests/` directory
- Test file names start with `test_`

## Pull Request Guidelines

### Pre-submission Checklist

- [ ] Code follows project standards
- [ ] Added necessary tests
- [ ] All tests pass
- [ ] Updated relevant documentation
- [ ] Commit messages follow conventions

### PR Description

Please include in your PR description:

1. Purpose and background of the changes
2. Specific details of the changes
3. Related Issue numbers
4. Test result screenshots (if applicable)

## Code of Conduct

- Respect all participants
- Accept constructive criticism
- Focus on what is best for the community
- Show empathy towards others

## Questions?

If you have any questions, feel free to ask on the [Issues](https://github.com/your-username/baidu-pan-copy/issues) page.
