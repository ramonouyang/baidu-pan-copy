1|# 更新日志 / Changelog
2|
3|<p align="center">
4|  <strong>🌐 Language / 语言</strong><br>
5|  <a href="#中文">中文</a> | <a href="#english">English</a>
6|</p>
7|
8|---
9|
# 中文

本项目所有重要更改都将记录在此文件。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
并且本项目遵循 [语义化版本控制](https://semver.org/lang/zh-CN/)。

## [1.1.28] - 2026-06-25

### Fixed
- 恢复转存时 `files_found` 未定义错误（从DB加载文件列表时变量未初始化）

## [1.1.27] - 2026-06-25
18|
19|### Fixed
20|- 举一反三审计：selected 转存路径补齐 errno 2/12/1504 逐文件重试和 else 日志分支
21|- 举一反三审计：`get_share_file_list` 补齐 CDN 404 body 检查（与 `get_share_children` 共用同一接口）
22|- 三个转存路径（lazy/recovery/selected）错误处理完全对齐
23|
24|## [1.1.26] - 2026-06-25
25|
26|### Fixed
27|- CDN 返回 HTTP 200 + 404 HTML body 时未触发重试（之前只检查 `status_code == 404`）
28|- `get_share_children` 和 `transfer_files` 均增加 body 内容检查：`"404 Not Found" in resp_text`
29|
30|## [1.1.25] - 2026-06-25
31|
32|### Fixed
33|- `recover_orphan_tasks()` 误将 `ready` 状态任务标记为孤儿并设为 `error`
34|- `ready` = 已解析未转存，不属于孤儿任务；从扫描条件中移除
35|
36|## [1.1.24] - 2026-06-25
37|
38|### Fixed
39|- 重复任务弹窗关闭时 Promise 未 resolve，导致解析按钮永久卡在"解析中"
40|- 弹窗四个关闭路径（背景点击/X按钮/取消/确定）均补充 `duplicateResolve`
41|
42|## [1.1.23] - 2026-06-25
43|
44|### Fixed
45|- 前端状态机泄漏：取消/删除任务后 `transferring` 和 `progressTimer` 未重置
46|- `cancelTask`/`deleteTask`/`clearAllTasks` 补充 `transferring=false` + 清除 `progressTimer`
47|- `progressTimer` 终止条件新增 `cancelled`/`paused` 状态
48|- `parseShareLink` 开头重置 `transferring` 防御性保护
49|
50|## [1.1.22] - 2026-06-25
51|
52|### Added
53|- 恢复任务跳过 BFS：从 DB 加载已收集文件列表，避免重新扫描 18000+ 文件触发限流
54|- 文件列表持久化：每 5 批保存一次到 DB
55|- `_recovery_transfer_from_file_list()`：直接从文件列表转存
56|
57|### Changed
58|- 恢复逻辑优先从 DB 加载 `file_list`，为空才走 BFS 扫描
59|
60|## [1.1.21] - 2026-06-25
61|
62|### Fixed
63|- CDN 404 错误不重试导致转存快速失败（原 ef85e808 任务 1142 个文件失败的根因）
64|- `transfer_files` 遇到 nginx 404 重试 5 次（含 connection 重建 + bdstoken 刷新）
65|- 区分 CDN 临时 404（重试）和分享链接过期（立即失败，`share_expired=True`）
66|- `errno=-404` 任务状态设为 `paused`（可恢复），而非 `error`
67|
68|### Changed
69|- 限流重试次数从 3 提升到 5（最长等待约 25 分钟：60→120→180→240→300s）
70|- BDCLND verify 增加 errno -62/-9 限流重试
71|- 分享链接过期检测：响应 body 包含"页面不存在"/"分享已过期"/"分享已被取消"时立即终止
72|
73|## [1.1.20] - 2026-06-24
74|
75|### 新增
76|- 开源准备：README 中英文双语、CONTRIBUTING、CHANGELOG、LICENSE
77|- 安装部署脚本：install.sh、install.bat、uninstall.sh
78|- 后台服务配置：systemd (Linux)、launchd (macOS)
79|- 中国大陆用户支持：国内镜像源加速安装
80|- Windows 完整支持：自动安装 Python、创建桌面快捷方式
81|- 部署指南文档：docs/DEPLOYMENT.md
82|
83|### 优化
84|- requirements.txt：移除未使用的 requests 依赖
85|- .gitignore：排除敏感文件（*.db、*.pem）
86|
87|## [1.1.19] - 2026-06-23
88|
89|### 新增
90|- 智能重复任务检测：防止重复转存
91|- 任务恢复功能：服务器重启后可恢复中断的任务
92|- 任务删除功能：支持单个和批量删除
93|- 任务状态管理：recoverable（可恢复）、cancelled（已取消）状态
94|- 重复任务处理弹窗：根据历史状态引导用户恢复或重新创建
95|
96|### 优化
97|- 数据库迁移：添加 surl、pwd、share_id、uk 字段
98|- 任务恢复：优先使用 DB 中保存的分享信息，减少 API 调用
99|- 状态样式：cancelled 状态使用灰色样式
100|
101|### 修复
102|- 修复服务器重启后任务状态不一致的问题
103|
104|## [1.1.18] - 2026-06-22
105|
106|### 新增
107|- 任务总结弹窗：显示转存统计信息
108|- 任务日志：支持导出任务日志
109|- 数据统计面板：显示全局统计数据
110|
111|### 优化
112|- 进度显示：实时显示文件数、速度、耗时
113|- 错误处理：更友好的错误提示
114|
115|## [1.1.17] - 2026-06-21
116|
117|### 新增
118|- 深色模式支持
119|- 新手引导教程
120|- Cookie 获取书签小工具
121|
122|### 优化
123|- 响应式设计优化
124|- 键盘快捷键支持
125|
126|## [1.1.16] - 2026-06-20
127|
128|### 新增
129|- 批量链接支持：一次处理多个分享链接
130|- 批量任务进度跟踪
131|- 批量任务子目录支持
132|
133|### 优化
134|- 分批策略优化：每批 100 个文件
135|- 智能限流检测
136|
137|## [1.1.15] - 2026-06-19
138|
139|### 新增
140|- 断点续传功能
141|- 任务暂停/继续功能
142|- 速度统计
143|
144|### 优化
145|- 重试机制：指数退避
146|- 错误日志增强
147|
148|## [1.1.0] - 2026-06-18
149|
150|### 新增
151|- 初始版本发布
152|- 单个链接转存功能
153|- 实时进度显示
154|- Cookie 验证
155|- 自定义目标路径
156|- 保持目录结构
157|
---

# English

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.28] - 2026-06-25

### Fixed
- Recovery transfer: `files_found` undefined error when loading file list from DB (variable not initialized)

## [1.1.27] - 2026-06-25

### Fixed
- Audit: selected transfer path now handles errno 2/12/1504 (per-file retry) and unknown errors
171|- Audit: `get_share_file_list` now checks CDN 404 body content (shares same endpoint as `get_share_children`)
172|- All three transfer paths (lazy/recovery/selected) have fully aligned error handling
173|
174|## [1.1.26] - 2026-06-25
175|
176|### Fixed
177|- CDN returning HTTP 200 with 404 HTML body was not detected (previously only checked `status_code == 404`)
178|- Both `get_share_children` and `transfer_files` now check body content: `"404 Not Found" in resp_text`
179|
180|## [1.1.25] - 2026-06-25
181|
182|### Fixed
183|- `recover_orphan_tasks()` incorrectly marked `ready` status tasks as orphans and set them to `error`
184|- `ready` = parsed but never transferred, not an orphan; removed from scan conditions
185|
186|## [1.1.24] - 2026-06-25
187|
188|### Fixed
189|- Duplicate task modal Promise never resolved on close, causing parse button to permanently show "解析中"
190|- All four close paths (backdrop click, X button, cancel, confirm) now call `duplicateResolve`
191|
192|## [1.1.23] - 2026-06-25
193|
194|### Fixed
195|- Frontend state machine leak: `transferring` and `progressTimer` not reset after cancel/delete task
196|- `cancelTask`/`deleteTask`/`clearAllTasks` now reset `transferring=false` and clear `progressTimer`
197|- `progressTimer` termination condition extended with `cancelled`/`paused` states
198|- `parseShareLink` defensively resets `transferring` at entry
199|
200|## [1.1.22] - 2026-06-25
201|
202|### Added
203|- Recovery skips BFS: load file list from DB to avoid re-scanning 18,000+ files (which triggers rate limits)
204|- File list persistence: saved to DB every 5 batches
205|- `_recovery_transfer_from_file_list()`: transfer directly from persisted file list
206|
207|### Changed
208|- Recovery logic prioritizes loading `file_list` from DB; falls back to BFS scan only when empty
209|
210|## [1.1.21] - 2026-06-25
211|
212|### Fixed
213|- CDN 404 errors not retried, causing rapid transfer failures (root cause of 1,142 file failures in ef85e808)
214|- `transfer_files` retries nginx 404 up to 5 times (with connection rebuild + bdstoken refresh)
215|- Distinguish CDN temporary 404 (retry) from share link expired (immediate failure with `share_expired=True`)
216|- `errno=-404` sets task status to `paused` (recoverable) instead of `error`
217|
218|### Changed
219|- Rate limit retry count increased from 3 to 5 (max wait ~25 min: 60→120→180→240→300s)
220|- BDCLND verify now retries on errno -62/-9 rate limits
221|- Share link expiration detection: body containing "页面不存在"/"分享已过期"/"分享已被取消" triggers immediate failure
222|
223|## [1.1.20] - 2026-06-24
224|
225|### Added
226|- Open source preparation: bilingual README, CONTRIBUTING, CHANGELOG, LICENSE
227|- Installation scripts: install.sh, install.bat, uninstall.sh
228|- Background service config: systemd (Linux), launchd (macOS)
229|- China mainland support: mirror sources for faster installation
230|- Full Windows support: auto-install Python, desktop shortcut
231|- Deployment guide: docs/DEPLOYMENT.md
232|
233|### Changed
234|- requirements.txt: remove unused requests dependency
235|- .gitignore: exclude sensitive files (*.db, *.pem)
236|
237|## [1.1.19] - 2026-06-23
238|
239|### Added
240|- Smart duplicate task detection: prevent duplicate transfers
241|- Task recovery: recover interrupted tasks after server restart
242|- Task deletion: support single and batch deletion
243|- Task status management: recoverable and cancelled states
244|- Duplicate task handling modal: guide users to recover or recreate based on history status
245|
246|### Changed
247|- Database migration: add surl, pwd, share_id, uk fields
248|- Task recovery: prioritize saved share info from DB, reduce API calls
249|- Status styling: cancelled status uses gray style
250|
251|### Fixed
252|- Fix task status inconsistency after server restart
253|
254|## [1.1.18] - 2026-06-22
255|
256|### Added
257|- Task summary modal: display transfer statistics
258|- Task logs: support log export
259|- Statistics dashboard: display global statistics
260|
261|### Changed
262|- Progress display: real-time file count, speed, elapsed time
263|- Error handling: more friendly error messages
264|
265|## [1.1.17] - 2026-06-21
266|
267|### Added
268|- Dark mode support
269|- Beginner tutorial
270|- Cookie bookmarklet
271|
272|### Changed
273|- Responsive design optimization
274|- Keyboard shortcuts support
275|
276|## [1.1.16] - 2026-06-20
277|
278|### Added
279|- Batch link support: process multiple share links at once
280|- Batch task progress tracking
281|- Batch task subdirectory support
282|
283|### Changed
284|- Batch strategy optimization: 100 files per batch
285|- Smart rate limit detection
286|
287|## [1.1.15] - 2026-06-19
288|
289|### Added
290|- Checkpoint recovery
291|- Task pause/resume
292|- Speed statistics
293|
294|### Changed
295|- Retry mechanism: exponential backoff
296|- Enhanced error logs
297|
298|## [1.1.0] - 2026-06-18
299|
300|### Added
301|- Initial release
302|- Single link transfer
303|- Real-time progress display
304|- Cookie validation
305|- Custom target path
306|- Preserve directory structure
307|