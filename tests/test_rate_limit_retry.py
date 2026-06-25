"""限流自动重试机制测试

覆盖场景：
1. _retry_on_rate_limit() 包装函数（main.py）
2. transfer_files 限流立即返回（baidu_api.py）
"""
import sys
import os
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_task():
    return {"status": "running", "error": ""}


# ============================================================
# _retry_on_rate_limit 测试
# ============================================================

def test_retry_immediate_success():
    """转存立即成功 → 不重试"""
    from main import _retry_on_rate_limit
    
    transfer_fn = MagicMock(return_value={"success": True, "task_id": "abc"})
    task = _make_task()
    
    with patch("main.time.sleep"), patch("main._save_checkpoint"), \
         patch("main._update_task_db"), patch("main.add_task_log"):
        result, should_return = _retry_on_rate_limit(
            transfer_fn, "task1", task, set(),
            batch_num=1, completed_count=0, failed_count=0, total_files=100
        )
    
    assert result["success"] == True
    assert should_return == False
    assert transfer_fn.call_count == 1
    assert task["status"] == "running"
    print("✅ test_retry_immediate_success")


def test_retry_non_rate_limit_error():
    """非限流错误(errno=-3) → 不重试，直接返回"""
    from main import _retry_on_rate_limit
    
    transfer_fn = MagicMock(return_value={"success": False, "errno": -3, "error": "未登录"})
    task = _make_task()
    
    with patch("main.time.sleep"), patch("main._save_checkpoint"), \
         patch("main._update_task_db"), patch("main.add_task_log"):
        result, should_return = _retry_on_rate_limit(
            transfer_fn, "task1", task, set(),
            batch_num=1, completed_count=0, failed_count=0, total_files=100
        )
    
    assert result["errno"] == -3
    assert should_return == False
    assert transfer_fn.call_count == 1
    print("✅ test_retry_non_rate_limit_error")


def test_retry_rate_limit_then_success():
    """限流2次后成功 → 重试成功，恢复running状态"""
    from main import _retry_on_rate_limit
    
    transfer_fn = MagicMock(side_effect=[
        {"success": False, "errno": -62, "error": "限流"},
        {"success": False, "errno": -62, "error": "限流"},
        {"success": True, "task_id": "abc"},
    ])
    task = _make_task()
    fs_ids = {"123", "456"}
    
    with patch("main.time.sleep") as mock_sleep, \
         patch("main._save_checkpoint") as mock_cp, \
         patch("main._update_task_db") as mock_db, \
         patch("main.add_task_log"):
        result, should_return = _retry_on_rate_limit(
            transfer_fn, "task1", task, fs_ids,
            batch_num=5, completed_count=10, failed_count=0, total_files=100
        )
    
    assert result["success"] == True
    assert should_return == False
    assert transfer_fn.call_count == 3
    assert task["status"] == "running"
    assert task["error"] == ""
    assert mock_sleep.call_count == 2
    mock_sleep.assert_called_with(300)
    
    # 验证中间状态变为 rate_limited
    status_calls = [c for c in mock_db.call_args_list if c[0][1] == "rate_limited"]
    assert len(status_calls) == 2
    
    # 验证断点保存2次（每次重试前）
    assert mock_cp.call_count == 2
    
    print("✅ test_retry_rate_limit_then_success")


def test_retry_rate_limit_exhausted():
    """限流持续12次 → 放弃，返回error状态"""
    from main import _retry_on_rate_limit, _RATE_LIMIT_POLL_MAX
    
    transfer_fn = MagicMock(return_value={"success": False, "errno": -62, "error": "限流"})
    task = _make_task()
    
    with patch("main.time.sleep") as mock_sleep, \
         patch("main._save_checkpoint") as mock_cp, \
         patch("main._update_task_db") as mock_db, \
         patch("main.add_task_log"):
        result, should_return = _retry_on_rate_limit(
            transfer_fn, "task1", task, set(),
            batch_num=10, completed_count=50, failed_count=0, total_files=100
        )
    
    assert result["errno"] == -62
    assert should_return == True
    assert transfer_fn.call_count == 1 + _RATE_LIMIT_POLL_MAX
    assert task["status"] == "error"
    assert "限流持续" in task["error"]
    assert mock_sleep.call_count == _RATE_LIMIT_POLL_MAX
    assert mock_cp.call_count == _RATE_LIMIT_POLL_MAX + 1  # 12 in loop + 1 after exhaustion
    
    print("✅ test_retry_rate_limit_exhausted")


def test_retry_rate_limit_then_other_error():
    """限流后遇到非限流错误(如errno=-3) → 停止重试，返回该错误"""
    from main import _retry_on_rate_limit
    
    transfer_fn = MagicMock(side_effect=[
        {"success": False, "errno": -62, "error": "限流"},
        {"success": False, "errno": -62, "error": "限流"},
        {"success": False, "errno": -3, "error": "未登录"},
    ])
    task = _make_task()
    
    with patch("main.time.sleep") as mock_sleep, \
         patch("main._save_checkpoint"), \
         patch("main._update_task_db"), \
         patch("main.add_task_log"):
        result, should_return = _retry_on_rate_limit(
            transfer_fn, "task1", task, set(),
            batch_num=3, completed_count=20, failed_count=0, total_files=100
        )
    
    assert result["errno"] == -3
    assert should_return == False
    assert transfer_fn.call_count == 3
    assert mock_sleep.call_count == 2
    
    print("✅ test_retry_rate_limit_then_other_error")


def test_retry_errno_minus9():
    """errno=-9 也触发限流重试"""
    from main import _retry_on_rate_limit
    
    transfer_fn = MagicMock(side_effect=[
        {"success": False, "errno": -9, "error": "请求过于频繁"},
        {"success": True, "task_id": "abc"},
    ])
    task = _make_task()
    
    with patch("main.time.sleep") as mock_sleep, \
         patch("main._save_checkpoint"), \
         patch("main._update_task_db"), \
         patch("main.add_task_log"):
        result, should_return = _retry_on_rate_limit(
            transfer_fn, "task1", task, set(),
            batch_num=1, completed_count=0, failed_count=0, total_files=50
        )
    
    assert result["success"] == True
    assert should_return == False
    assert transfer_fn.call_count == 2
    assert mock_sleep.call_count == 1
    
    print("✅ test_retry_errno_minus9")


def test_retry_preserves_completed_count():
    """重试过程中 completed_count 正确传递到 DB"""
    from main import _retry_on_rate_limit
    
    transfer_fn = MagicMock(side_effect=[
        {"success": False, "errno": -62, "error": "限流"},
        {"success": True, "task_id": "abc"},
    ])
    task = _make_task()
    
    with patch("main.time.sleep"), \
         patch("main._save_checkpoint"), \
         patch("main._update_task_db") as mock_db, \
         patch("main.add_task_log"):
        result, should_return = _retry_on_rate_limit(
            transfer_fn, "task1", task, set(),
            batch_num=1, completed_count=42, failed_count=3, total_files=100
        )
    
    rate_limited_calls = [c for c in mock_db.call_args_list if c[0][1] == "rate_limited"]
    assert len(rate_limited_calls) == 1
    assert rate_limited_calls[0][0][2] == 42  # completed_count
    assert rate_limited_calls[0][0][3] == 3   # failed_count
    
    print("✅ test_retry_preserves_completed_count")


def test_retry_checkpoint_saved_before_each_attempt():
    """每次重试前都保存断点"""
    from main import _retry_on_rate_limit, _RATE_LIMIT_POLL_MAX
    
    transfer_fn = MagicMock(return_value={"success": False, "errno": -62, "error": "限流"})
    task = _make_task()
    fs_ids = {"fs_1", "fs_2"}
    
    with patch("main.time.sleep"), \
         patch("main._save_checkpoint") as mock_cp, \
         patch("main._update_task_db"), \
         patch("main.add_task_log"):
        _retry_on_rate_limit(
            transfer_fn, "task1", task, fs_ids,
            batch_num=7, completed_count=0, failed_count=0, total_files=50
        )
    
    # 12次重试（每次重试前保存断点）+ 1次放弃后保存 = 13
    assert mock_cp.call_count == _RATE_LIMIT_POLL_MAX + 1
    for c in mock_cp.call_args_list:
        assert c[0][1] == fs_ids
        assert c[0][2] == 7       # batch_num
        assert c[0][3] == 50      # total_files
    
    print("✅ test_retry_checkpoint_saved_before_each_attempt")


def test_integration_rate_limit_recovery_flow():
    """端到端：限流 → rate_limited → 重试成功 → running → 继续"""
    from main import _retry_on_rate_limit
    
    batch_results = [
        {"success": True, "task_id": "b1"},
        {"success": False, "errno": -62, "error": "限流"},
        {"success": False, "errno": -62, "error": "限流"},
        {"success": True, "task_id": "b2"},
        {"success": True, "task_id": "b3"},
    ]
    
    call_idx = [0]
    def mock_transfer():
        result = batch_results[call_idx[0]]
        call_idx[0] += 1
        return result
    
    task = _make_task()
    
    with patch("main.time.sleep") as mock_sleep, \
         patch("main._save_checkpoint"), \
         patch("main._update_task_db"), \
         patch("main.add_task_log"):
        results = []
        for batch_num in range(1, 4):
            result, should_return = _retry_on_rate_limit(
                mock_transfer, "task1", task, set(),
                batch_num=batch_num, completed_count=0, failed_count=0, total_files=100
            )
            results.append((result, should_return))
    
    assert results[0][0]["success"] == True
    assert results[1][0]["success"] == True
    assert results[2][0]["success"] == True
    assert task["status"] == "running"
    assert call_idx[0] == 5
    assert mock_sleep.call_count == 2
    
    print("✅ test_integration_rate_limit_recovery_flow")


# ============================================================
# transfer_files 限流立即返回测试
# ============================================================

def test_transfer_files_returns_rate_limit_immediately():
    """transfer_files 遇到限流立即返回（不重试，由调用方重试）"""
    from baidu_api import BaiduPanAPI, _global_limiter
    
    api = BaiduPanAPI("test_cookie")
    
    # Mock the HTTP response for rate limit
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"errno": -62}'
    mock_resp.json.return_value = {"errno": -62}
    mock_resp.cookies = {}
    mock_resp.url = "https://pan.baidu.com/share/transfer"
    
    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    mock_client.cookies = {}
    
    api._bdstoken_cache = "test_token"
    api.headers = {"User-Agent": "test"}
    api.cookie = "test_cookie"
    
    with patch("baidu_api.time.sleep"),          patch.object(api, "_ensure_client"),          patch.object(api, "_get_bdstoken", return_value="test_token"),          patch.object(_global_limiter, "acquire"),          patch.object(_global_limiter, "report_rate_limit"):
        api.client = mock_client  # Set AFTER _ensure_client is mocked
        result = api.transfer_files("share_id", "uk", [123], "/target", "pwd", "https://pan.baidu.com/s/test")
    
    assert result["success"] == False
    assert result["errno"] == -62
    print("✅ test_transfer_files_returns_rate_limit_immediately")


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    tests = [
        test_retry_immediate_success,
        test_retry_non_rate_limit_error,
        test_retry_rate_limit_then_success,
        test_retry_rate_limit_exhausted,
        test_retry_rate_limit_then_other_error,
        test_retry_errno_minus9,
        test_retry_preserves_completed_count,
        test_retry_checkpoint_saved_before_each_attempt,
        test_integration_rate_limit_recovery_flow,
        test_transfer_files_returns_rate_limit_immediately,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    
    print(f"\n{'='*50}")
    print(f"结果: {passed} passed, {failed} failed, {len(tests)} total")
    
    if failed > 0:
        sys.exit(1)
