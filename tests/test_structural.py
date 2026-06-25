"""白盒/结构测试

覆盖场景：
1. RateLimiter 令牌桶算法验证
2. 关键函数分支覆盖
3. DB 写入失败异常路径
4. 高复杂度函数结构检查
"""
import sys
import os
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 4.1 RateLimiter 令牌桶算法验证
# ============================================================

def test_rate_limiter_initial_tokens():
    """RateLimiter 初始令牌数 = burst"""
    from baidu_api import RateLimiter

    limiter = RateLimiter(rate=2.5, burst=4)
    assert limiter.burst == 4
    assert limiter.rate == 2.5
    assert limiter._tokens == 4.0  # 初始满桶
    print("✅ test_rate_limiter_initial_tokens")


def test_rate_limiter_acquire_consume_token():
    """RateLimiter acquire 消耗令牌"""
    from baidu_api import RateLimiter

    limiter = RateLimiter(rate=2.5, burst=4)
    initial_tokens = limiter._tokens
    limiter.acquire(timeout=1.0)
    assert limiter._tokens < initial_tokens  # 消耗了1个令牌
    print("✅ test_rate_limiter_acquire_consume_token")


def test_rate_limiter_burst_exhaustion():
    """RateLimiter burst 耗尽后阻塞"""
    from baidu_api import RateLimiter

    limiter = RateLimiter(rate=10.0, burst=2)

    # 消耗所有 burst
    limiter.acquire(timeout=0.1)
    limiter.acquire(timeout=0.1)

    # 第3次应该阻塞（但超时后强制放行）
    start = time.time()
    limiter.acquire(timeout=0.2)
    elapsed = time.time() - start

    # 应该阻塞了约 0.2 秒
    assert elapsed >= 0.1
    print("✅ test_rate_limiter_burst_exhaustion")


def test_rate_limiter_token_refill():
    """RateLimiter 令牌随时间补充"""
    from baidu_api import RateLimiter

    limiter = RateLimiter(rate=10.0, burst=2)

    # 消耗所有 burst
    limiter.acquire(timeout=0.1)
    limiter.acquire(timeout=0.1)

    # 等待 0.2 秒，应该补充 ~2 个令牌
    time.sleep(0.2)

    # 现在应该能立即获取令牌
    start = time.time()
    limiter.acquire(timeout=0.1)
    elapsed = time.time() - start

    assert elapsed < 0.1  # 应该立即返回
    print("✅ test_rate_limiter_token_refill")


def test_rate_limiter_report_rate_limit():
    """RateLimiter 限流报告后暂停"""
    from baidu_api import RateLimiter

    limiter = RateLimiter(rate=10.0, burst=4)
    limiter.report_rate_limit(cooldown=0.3)

    # 限流期间应该阻塞
    start = time.time()
    limiter.acquire(timeout=0.1)
    elapsed = time.time() - start

    # 应该阻塞了约 0.1 秒（超时）
    assert elapsed >= 0.05
    print("✅ test_rate_limiter_report_rate_limit")


# ============================================================
# 4.2 关键函数分支覆盖
# ============================================================

def test_safe_json_parse_valid():
    """safe_json_parse 正常 JSON"""
    from baidu_api import safe_json_parse
    import json

    resp = MagicMock()
    resp.text = '{"errno": 0, "list": []}'
    resp.json.return_value = json.loads(resp.text)
    result = safe_json_parse(resp)
    assert result.get("errno") == 0
    assert "list" in result
    print("✅ test_safe_json_parse_valid")


def test_safe_json_parse_invalid():
    """safe_json_parse 无效 JSON"""
    from baidu_api import safe_json_parse

    resp = MagicMock()
    resp.text = '<html>404 Not Found</html>'
    result = safe_json_parse(resp)
    assert "error" in result
    print("✅ test_safe_json_parse_invalid")


def test_safe_json_parse_empty():
    """safe_json_parse 空响应"""
    from baidu_api import safe_json_parse

    resp = MagicMock()
    resp.text = ''
    result = safe_json_parse(resp)
    assert "error" in result
    print("✅ test_safe_json_parse_empty")


# ============================================================
# 4.3 DB 写入失败异常路径
# ============================================================

def test_save_checkpoint():
    """_save_checkpoint 正常保存"""
    import main

    # 正常保存应该不崩溃
    try:
        main._save_checkpoint("test_task_structural", {1, 2, 3}, 1, 10)
        save_success = True
    except Exception as e:
        save_success = False
        print(f"  save failed: {e}")

    assert save_success
    print("✅ test_save_checkpoint")


# ============================================================
# 4.4 高复杂度函数结构检查
# ============================================================

def test_get_share_children_structure():
    """get_share_children 函数结构检查"""
    from baidu_api import BaiduPanAPI
    import inspect

    source = inspect.getsource(BaiduPanAPI.get_share_children)

    # 检查关键结构
    assert "def get_share_children" in source
    assert "timeout" in source.lower()
    assert "retry" in source.lower() or "RETRY" in source
    assert "errno" in source
    assert "rate_limit" in source or "RATE_LIMIT" in source

    print("✅ test_get_share_children_structure")


def test_transfer_files_structure():
    """transfer_files 函数结构检查"""
    from baidu_api import BaiduPanAPI
    import inspect

    source = inspect.getsource(BaiduPanAPI.transfer_files)

    # 检查关键结构
    assert "def transfer_files" in source
    assert "errno" in source
    assert "CDN" in source or "cdn" in source
    assert "timeout" in source.lower()

    print("✅ test_transfer_files_structure")


def test_retry_on_rate_limit_structure():
    """_retry_on_rate_limit 函数结构检查"""
    from main import _retry_on_rate_limit
    import inspect

    source = inspect.getsource(_retry_on_rate_limit)

    # 检查关键结构
    assert "def _retry_on_rate_limit" in source
    assert "POLL_INTERVAL" in source or "poll_interval" in source
    assert "POLL_MAX" in source or "poll_max" in source
    assert "checkpoint" in source
    assert "rate_limited" in source

    print("✅ test_retry_on_rate_limit_structure")


if __name__ == "__main__":
    tests = [
        test_rate_limiter_initial_tokens,
        test_rate_limiter_acquire_consume_token,
        test_rate_limiter_burst_exhaustion,
        test_rate_limiter_token_refill,
        test_rate_limiter_report_rate_limit,
        test_safe_json_parse_valid,
        test_safe_json_parse_invalid,
        test_safe_json_parse_empty,
        test_save_checkpoint,
        test_get_share_children_structure,
        test_transfer_files_structure,
        test_retry_on_rate_limit_structure,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: {e}")
            failed += 1

    print(f"\n{'='*50}")
    print(f"结果: {passed} passed, {failed} failed, {passed+failed} total")
