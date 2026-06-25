# 百度网盘转存工具 — 测试用例建设计划

## 项目概况

| 项目 | 值 |
|------|-----|
| 项目名 | baidu-pan-copy |
| 代码量 | baidu_api.py 1976行 + main.py 2547行 = 4523行 |
| 当前用例 | 36 个（3 个测试文件） |
| 测试框架 | unittest + mock（无 pytest） |
| 参考方法论 | `test-case-design-methodology` skill |

## 现有用例清单

### tests/test_rate_limit_retry.py（23 个）
| 维度 | 用例 | 状态 |
|------|------|------|
| 可靠性 | retry_immediate_success / non_rate_limit_error / rate_limit_then_success / rate_limit_exhausted / rate_limit_then_other_error / errno_minus9 | ✅ |
| 可靠性 | transfer_fn_raises_exception / first_retry_succeeds / two_consecutive_batches / on_last_batch / result_no_errno_key | ✅ |
| 可靠性 | preserves_completed_count / checkpoint_saved_before_each_attempt / error_message_format | ✅ |
| API层 | transfer_files_returns_rate_limit_immediately / returns_errno_minus9_immediately | ✅ |
| API层 | bdclnd_verify_retry_succeeds / bdclnd_verify_retry_exhausted | ✅ 结构验证 |
| API层 | get_share_children_retry_succeeds / get_share_children_retry_exhausted | ✅ 结构验证 |
| 白盒 | rate_limit_constants / api_rate_limit_constants | ✅ |
| 集成 | integration_rate_limit_recovery_flow | ✅ |

### tests/test_file_list_persistence.py（4 个）
| 维度 | 用例 | 状态 |
|------|------|------|
| 功能 | save_load_file_list / load_empty / load_empty_string / incremental | ✅ |

### test_transfer_changes.py（9 个）
| 维度 | 用例 | 状态 |
|------|------|------|
| 白盒 | syntax / import / method_signatures | ✅ |
| API层 | transfer_endpoint / referer / fsidlist_parameter / share_page_tokens / verify_share | ✅ |
| 功能 | batch_transfer_manager | ✅ |

---

## 建设计划（按优先级）

### Phase 1：功能正确性 + 可靠性补全（P0）

**目标**：覆盖核心业务路径 + 线上高频故障场景

| # | 维度 | 用例名 | 测试内容 | 预估 |
|---|------|--------|---------|------|
| 1 | 功能 | test_func_transfer_single_file | 单文件转存成功 | 30min |
| 2 | 功能 | test_func_transfer_nested_dirs | 嵌套目录转存保留结构 | 30min |
| 3 | 功能 | test_func_transfer_batch_100 | 批量100文件分组转存 | 30min |
| 4 | 功能 | test_func_resume_skips_transferred | 断点恢复跳过已转存文件 | 30min |
| 5 | 功能 | test_func_batch_parse_multi_links | 批量解析多分享链接 | 30min |
| 6 | 功能 | test_func_edge_empty_file_list | 空文件列表处理 | 15min |
| 7 | 功能 | test_func_edge_long_filename | 超长文件名(errno=2)降级逐个转存 | 30min |
| 8 | 功能 | test_func_edge_special_chars_path | 特殊字符路径处理 | 15min |
| 9 | 功能 | test_func_errno4_timeout_retry | errno=4 超时重试5次 | 30min |
| 10 | 功能 | test_func_errno2_fallback_single | errno=2 降级逐个转存 | 30min |
| 11 | 功能 | test_func_share_expired | 分享链接过期(errno=-19/-20)检测 | 15min |
| 12 | 功能 | test_func_cdn404_retry | CDN 404 重试5次 | 30min |
| 13 | 可靠性 | test_retry_network_timeout | httpx.TimeoutException 重试 | 30min |
| 14 | 可靠性 | test_retry_connection_error | httpx.NetworkError 重建client | 30min |
| 15 | 可靠性 | test_retry_cookie_expired_errno3 | Cookie过期(errno=-3)检测+提示 | 15min |
| 16 | 可靠性 | test_retry_concurrent_task_write | 多线程写 active_tasks 安全性 | 30min |

**小计**：16 个用例，约 7 小时

### Phase 2：API 接口 + 白盒补全（P1）

**目标**：验证 HTTP 端点契约 + 代码分支覆盖

| # | 维度 | 用例名 | 测试内容 | 预估 |
|---|------|--------|---------|------|
| 17 | API | test_api_post_parse_share | POST /api/share/parse 正常 | 30min |
| 18 | API | test_api_post_parse_invalid_link | POST /api/share/parse 无效链接 | 15min |
| 19 | API | test_api_post_start_task | POST /api/task/{id}/start 正常 | 30min |
| 20 | API | test_api_get_progress_status | GET /api/task/{id}/progress 状态轮询 | 30min |
| 21 | API | test_api_post_pause_task | POST /api/task/{id}/pause | 15min |
| 22 | API | test_api_post_cancel_task | POST /api/task/{id}/cancel | 15min |
| 23 | API | test_api_error_code_mapping | 错误码→HTTP状态码映射 | 30min |
| 24 | API | test_api_param_validation | 参数校验（空/无效/越界） | 30min |
| 25 | 白盒 | test_struct_rate_limiter_token_bucket | RateLimiter 令牌桶算法正确性 | 30min |
| 26 | 白盒 | test_struct_rate_limiter_penalty | 限流惩罚期降速行为 | 15min |
| 27 | 白盒 | test_struct_rate_limiter_budget | 预算窗口耗尽行为 | 15min |
| 28 | 白盒 | test_struct_all_errno_branches | 所有 errno 分支覆盖 | 30min |
| 29 | 白盒 | test_struct_db_write_failure | DB 写入异常处理 | 15min |
| 30 | 白盒 | test_struct_dead_code_check | BatchTransferManager 死代码检测 | 15min |

**小计**：14 个用例，约 6 小时

### Phase 3：性能 + 压力 + 集成（P2）

**目标**：验证系统负载能力和端到端流程

| # | 维度 | 用例名 | 测试内容 | 预估 |
|---|------|--------|---------|------|
| 31 | 性能 | test_perf_memory_large_file_list | 10000文件内存 < 200MB | 30min |
| 32 | 性能 | test_perf_db_checkpoint_io | 每批checkpoint写入延迟 < 100ms | 15min |
| 33 | 压力 | test_stress_concurrent_3_tasks | 3任务并发无竞争 | 30min |
| 34 | 压力 | test_stress_rapid_create_cancel | 100次快速创建+取消无泄漏 | 30min |
| 35 | 压力 | test_stress_rate_limiter_burst | burst=4瞬间10请求不丢 | 15min |
| 36 | 压力 | test_stress_db_high_freq_write | 高频checkpoint不锁死 | 15min |
| 37 | 集成 | test_e2e_parse_to_complete | 解析→预览→转存→完成 | 45min |
| 38 | 集成 | test_e2e_interrupt_and_resume | 中断→恢复→完成 | 45min |
| 39 | 集成 | test_e2e_frontend_status_machine | running→rate_limited→running→completed | 30min |

**小计**：9 个用例，约 5 小时

### Phase 4：长稳 + 混沌（P3）

**目标**：验证长时间运行稳定性和极端场景

| # | 维度 | 用例名 | 测试内容 | 预估 |
|---|------|--------|---------|------|
| 40 | 长稳 | test_endurance_memory_stable | 1h运行内存波动 < 50MB | 30min |
| 41 | 长稳 | test_endurance_task_cleanup | 旧任务自动清理不堆积 | 15min |
| 42 | 长稳 | test_endurance_connection_pool | client 10000次复用不退化 | 15min |
| 43 | 长稳 | test_endurance_log_rotation | 日志不超限 | 15min |
| 44 | 长稳 | test_endurance_cookie_expiry | BDCLND过期自动续期 | 15min |
| 45 | 混沌 | test_chaos_random_failures | 10%随机失败下系统行为 | 30min |
| 46 | 混沌 | test_chaos_resource_exhaustion | 磁盘满/内存不足降级 | 15min |
| 47 | 混沌 | test_adversarial_malicious_input | 超长URL/SQL注入/特殊字符 | 15min |

**小计**：8 个用例，约 2.5 小时

---

## 汇总

| 阶段 | 维度 | 用例数 | 工时 | 优先级 |
|------|------|--------|------|--------|
| Phase 1 | 功能 + 可靠性 | 16 | 7h | P0 |
| Phase 2 | API + 白盒 | 14 | 6h | P1 |
| Phase 3 | 性能 + 压力 + 集成 | 9 | 5h | P2 |
| Phase 4 | 长稳 + 混沌 | 8 | 2.5h | P3 |
| **合计** | | **47** | **20.5h** | |
| 现有 | | 36 | — | — |
| **总计** | | **83** | | |

## 测试文件组织

```
baidu-pan-copy/
├── tests/
│   ├── test_rate_limit_retry.py        # ✅ 23 个（可靠性）
│   ├── test_file_list_persistence.py   # ✅ 4 个（功能）
│   ├── test_functional.py              # 🆕 Phase 1 功能用例
│   ├── test_reliability.py             # 🆕 Phase 1 可靠性用例
│   ├── test_api_contract.py            # 🆕 Phase 2 API 用例
│   ├── test_structural.py              # 🆕 Phase 2 白盒用例
│   ├── test_stress.py                  # 🆕 Phase 3 压力用例
│   ├── test_endurance.py               # 🆕 Phase 4 长稳用例
│   └── test_e2e.py                     # 🆕 Phase 3 集成用例
├── test_transfer_changes.py            # ✅ 9 个（白盒+API）
└── docs/
    └── TEST_PLAN.md                    # 本文档
```

## 执行策略

**mock 加速**：所有测试使用 mock，不调用真实百度 API。长稳测试用模拟时间窗口（`time.sleep` mock + 时间推进）。

**CI 集成**：Phase 1-2 纳入 CI 每次提交运行；Phase 3 纳入 nightly；Phase 4 纳入 weekly。

**退出标准**：
- Phase 1-2：100% 通过才能合并
- Phase 3：性能指标达标才能上线
- Phase 4：无崩溃、无内存泄漏才能发布
