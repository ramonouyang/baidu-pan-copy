# 更新日志 / Changelog

<p align="center">
  <strong>🌐 Language / 语言</strong><br>
  <a href="#中文">中文</a> | <a href="#english">English</a>
</p>

---

# 中文

本项目所有重要更改都将记录在此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且本项目遵循 [语义化版本控制](https://semver.org/lang/zh-CN/)。

## [1.1.27] - 2026-06-25

### Fixed
- 举一反三审计：selected 转存路径补齐 errno 2/12/1504 逐文件重试和 else 日志分支
- 举一反三审计：`get_share_file_list` 补齐 CDN 404 body 检查（与 `get_share_children` 共用同一接口）
- 三个转存路径（lazy/recovery/selected）错误处理完全对齐

## [1.1.26] - 2026-06-25

### Fixed
- CDN 返回 HTTP 200 + 404 HTML body 时未触发重试（之前只检查 `status_code == 404`）
- `get_share_children` 和 `transfer_files` 均增加 body 内容检查：`"404 Not Found" in resp_text`

## [1.1.25] - 2026-06-25

### Fixed
- `recover_orphan_tasks()` 误将 `ready` 状态任务标记为孤儿并设为 `error`
- `ready` = 已解析未转存，不属于孤儿任务；从扫描条件中移除

## [1.1.24] - 2026-06-25

### Fixed
- 重复任务弹窗关闭时 Promise 未 resolve，导致解析按钮永久卡在"解析中"
- 弹窗四个关闭路径（背景点击/X按钮/取消/确定）均补充 `duplicateResolve`

## [1.1.23] - 2026-06-25

### Fixed
- 前端状态机泄漏：取消/删除任务后 `transferring` 和 `progressTimer` 未重置
- `cancelTask`/`deleteTask`/`clearAllTasks` 补充 `transferring=false` + 清除 `progressTimer`
- `progressTimer` 终止条件新增 `cancelled`/`paused` 状态
- `parseShareLink` 开头重置 `transferring` 防御性保护

## [1.1.22] - 2026-06-25

### Added
- 恢复任务跳过 BFS：从 DB 加载已收集文件列表，避免重新扫描 18000+ 文件触发限流
- 文件列表持久化：每 5 批保存一次到 DB
- `_recovery_transfer_from_file_list()`：直接从文件列表转存

### Changed
- 恢复逻辑优先从 DB 加载 `file_list`，为空才走 BFS 扫描

## [1.1.21] - 2026-06-25

### Fixed
- CDN 404 错误不重试导致转存快速失败（原 ef85e808 任务 1142 个文件失败的根因）
- `transfer_files` 遇到 nginx 404 重试 5 次（含 connection 重建 + bdstoken 刷新）
- 区分 CDN 临时 404（重试）和分享链接过期（立即失败，`share_expired=True`）
- `errno=-404` 任务状态设为 `paused`（可恢复），而非 `error`

### Changed
- 限流重试次数从 3 提升到 5（最长等待约 25 分钟：60→120→180→240→300s）
- BDCLND verify 增加 errno -62/-9 限流重试
- 分享链接过期检测：响应 body 包含"页面不存在"/"分享已过期"/"分享已被取消"时立即终止

## [1.1.20] - 2026-06-24

### 新增
- 开源准备：README 中英文双语、CONTRIBUTING、CHANGELOG、LICENSE
- 安装部署脚本：install.sh、install.bat、uninstall.sh
- 后台服务配置：systemd (Linux)、launchd (macOS)
- 中国大陆用户支持：国内镜像源加速安装
- Windows 完整支持：自动安装 Python、创建桌面快捷方式
- 部署指南文档：docs/DEPLOYMENT.md

### 优化
- requirements.txt：移除未使用的 requests 依赖
- .gitignore：排除敏感文件（*.db、*.pem）

## [1.1.19] - 2026-06-23

### 新增
- 智能重复任务检测：防止重复转存
- 任务恢复功能：服务器重启后可恢复中断的任务
- 任务删除功能：支持单个和批量删除
- 任务状态管理：recoverable（可恢复）、cancelled（已取消）状态
- 重复任务处理弹窗：根据历史状态引导用户恢复或重新创建

### 优化
- 数据库迁移：添加 surl、pwd、share_id、uk 字段
- 任务恢复：优先使用 DB 中保存的分享信息，减少 API 调用
- 状态样式：cancelled 状态使用灰色样式

### 修复
- 修复服务器重启后任务状态不一致的问题

## [1.1.18] - 2026-06-22

### 新增
- 任务总结弹窗：显示转存统计信息
- 任务日志：支持导出任务日志
- 数据统计面板：显示全局统计数据

### 优化
- 进度显示：实时显示文件数、速度、耗时
- 错误处理：更友好的错误提示

## [1.1.17] - 2026-06-21

### 新增
- 深色模式支持
- 新手引导教程
- Cookie 获取书签小工具

### 优化
- 响应式设计优化
- 键盘快捷键支持

## [1.1.16] - 2026-06-20

### 新增
- 批量链接支持：一次处理多个分享链接
- 批量任务进度跟踪
- 批量任务子目录支持

### 优化
- 分批策略优化：每批 100 个文件
- 智能限流检测

## [1.1.15] - 2026-06-19

### 新增
- 断点续传功能
- 任务暂停/继续功能
- 速度统计

### 优化
- 重试机制：指数退避
- 错误日志增强

## [1.1.0] - 2026-06-18

### 新增
- 初始版本发布
- 单个链接转存功能
- 实时进度显示
- Cookie 验证
- 自定义目标路径
- 保持目录结构

---

# English

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.27] - 2026-06-25

### Fixed
- Audit: selected transfer path now handles errno 2/12/1504 (per-file retry) and unknown errors
- Audit: `get_share_file_list` now checks CDN 404 body content (shares same endpoint as `get_share_children`)
- All three transfer paths (lazy/recovery/selected) have fully aligned error handling

## [1.1.26] - 2026-06-25

### Fixed
- CDN returning HTTP 200 with 404 HTML body was not detected (previously only checked `status_code == 404`)
- Both `get_share_children` and `transfer_files` now check body content: `"404 Not Found" in resp_text`

## [1.1.25] - 2026-06-25

### Fixed
- `recover_orphan_tasks()` incorrectly marked `ready` status tasks as orphans and set them to `error`
- `ready` = parsed but never transferred, not an orphan; removed from scan conditions

## [1.1.24] - 2026-06-25

### Fixed
- Duplicate task modal Promise never resolved on close, causing parse button to permanently show "解析中"
- All four close paths (backdrop click, X button, cancel, confirm) now call `duplicateResolve`

## [1.1.23] - 2026-06-25

### Fixed
- Frontend state machine leak: `transferring` and `progressTimer` not reset after cancel/delete task
- `cancelTask`/`deleteTask`/`clearAllTasks` now reset `transferring=false` and clear `progressTimer`
- `progressTimer` termination condition extended with `cancelled`/`paused` states
- `parseShareLink` defensively resets `transferring` at entry

## [1.1.22] - 2026-06-25

### Added
- Recovery skips BFS: load file list from DB to avoid re-scanning 18,000+ files (which triggers rate limits)
- File list persistence: saved to DB every 5 batches
- `_recovery_transfer_from_file_list()`: transfer directly from persisted file list

### Changed
- Recovery logic prioritizes loading `file_list` from DB; falls back to BFS scan only when empty

## [1.1.21] - 2026-06-25

### Fixed
- CDN 404 errors not retried, causing rapid transfer failures (root cause of 1,142 file failures in ef85e808)
- `transfer_files` retries nginx 404 up to 5 times (with connection rebuild + bdstoken refresh)
- Distinguish CDN temporary 404 (retry) from share link expired (immediate failure with `share_expired=True`)
- `errno=-404` sets task status to `paused` (recoverable) instead of `error`

### Changed
- Rate limit retry count increased from 3 to 5 (max wait ~25 min: 60→120→180→240→300s)
- BDCLND verify now retries on errno -62/-9 rate limits
- Share link expiration detection: body containing "页面不存在"/"分享已过期"/"分享已被取消" triggers immediate failure

## [1.1.20] - 2026-06-24

### Added
- Open source preparation: bilingual README, CONTRIBUTING, CHANGELOG, LICENSE
- Installation scripts: install.sh, install.bat, uninstall.sh
- Background service config: systemd (Linux), launchd (macOS)
- China mainland support: mirror sources for faster installation
- Full Windows support: auto-install Python, desktop shortcut
- Deployment guide: docs/DEPLOYMENT.md

### Changed
- requirements.txt: remove unused requests dependency
- .gitignore: exclude sensitive files (*.db, *.pem)

## [1.1.19] - 2026-06-23

### Added
- Smart duplicate task detection: prevent duplicate transfers
- Task recovery: recover interrupted tasks after server restart
- Task deletion: support single and batch deletion
- Task status management: recoverable and cancelled states
- Duplicate task handling modal: guide users to recover or recreate based on history status

### Changed
- Database migration: add surl, pwd, share_id, uk fields
- Task recovery: prioritize saved share info from DB, reduce API calls
- Status styling: cancelled status uses gray style

### Fixed
- Fix task status inconsistency after server restart

## [1.1.18] - 2026-06-22

### Added
- Task summary modal: display transfer statistics
- Task logs: support log export
- Statistics dashboard: display global statistics

### Changed
- Progress display: real-time file count, speed, elapsed time
- Error handling: more friendly error messages

## [1.1.17] - 2026-06-21

### Added
- Dark mode support
- Beginner tutorial
- Cookie bookmarklet

### Changed
- Responsive design optimization
- Keyboard shortcuts support

## [1.1.16] - 2026-06-20

### Added
- Batch link support: process multiple share links at once
- Batch task progress tracking
- Batch task subdirectory support

### Changed
- Batch strategy optimization: 100 files per batch
- Smart rate limit detection

## [1.1.15] - 2026-06-19

### Added
- Checkpoint recovery
- Task pause/resume
- Speed statistics

### Changed
- Retry mechanism: exponential backoff
- Enhanced error logs

## [1.1.0] - 2026-06-18

### Added
- Initial release
- Single link transfer
- Real-time progress display
- Cookie validation
- Custom target path
- Preserve directory structure
