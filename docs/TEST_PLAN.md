# 测试用例建设计划

**项目**: baidu-pan-copy (百度网盘转存工具)
**创建日期**: 2026-06-25
**完成日期**: 2026-06-25
**状态**: ✅ 全部完成

## 执行结果

| 阶段 | 维度 | 用例数 | 状态 |
|------|------|--------|------|
| 阶段一 | 可靠性/韧性 | 10 | ✅ 完成 |
| 阶段二 | 功能正确性 | 12 | ✅ 完成 |
| 阶段三 | API 接口 | 10 | ✅ 完成 |
| 阶段四 | 白盒/结构 | 12 | ✅ 完成 |
| 阶段五 | 性能/并发 | 6 | ✅ 完成 |
| 阶段六 | 压力/长稳 | 3 | ✅ 完成 |
| 阶段七 | 集成/E2E | 10 | ✅ 完成 |
| **合计** | **7 维度** | **63** | **✅ 全部通过** |

## 全量测试结果

```
============================= 106 passed in 4.91s ==============================
```

## 测试文件清单

| 文件 | 用例数 | 覆盖维度 |
|------|--------|---------|
| tests/test_rate_limit_retry.py | 23 | 限流重试机制 |
| tests/test_file_list_persistence.py | 4 | 文件列表持久化 |
| tests/test_recovery_remaining_zero.py | 16 | 断点恢复逻辑 |
| tests/test_reliability.py | 10 | 可靠性/韧性 |
| tests/test_functional.py | 12 | 功能正确性 |
| tests/test_api.py | 10 | API 接口 |
| tests/test_structural.py | 12 | 白盒/结构 |
| tests/test_performance.py | 6 | 性能/并发 |
| tests/test_stress.py | 3 | 压力/长稳 |
| tests/test_e2e.py | 10 | 集成/E2E |

## 经验总结

已归档到全局技能库：`test-case-design-methodology` (software-development 分类)

### 关键发现

1. **内存问题**：10000 文件列表占用约 336MB Python 对象内存，需注意大批量场景
2. **RateLimiter 行为**：acquire() 超时后强制放行（返回 None），不会返回 False
3. **errno=-4 不重试**：当前代码对 errno=-4（超时）不自动重试，直接返回错误
4. **并发安全**：active_tasks 是普通 dict，多线程写入依赖 GIL，实际使用中未出现问题
5. **DB 写入性能**：50 次 checkpoint 保存仅需 0.01 秒，性能充足

### Mock 最佳实践

1. httpx client 需要设置 `is_closed = False` 避免重建
2. safe_json_parse 需要 mock `resp.json()` 返回值
3. RateLimiter.acquire() 返回 None，不是 True/False
4. get_share_children 返回 `{"error": "..."}` 格式，不是 `{"errno": ...}`
