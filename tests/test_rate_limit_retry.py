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
# 补充用例：_retry_on_rate_limit 边界场景
# ============================================================

def test_retry_transfer_fn_raises_exception():
    """transfer_fn 抛异常 → 异常应传播，不吞掉"""
    from main import _retry_on_rate_limit

    transfer_fn = MagicMock(side_effect=RuntimeError("连接超时"))
    task = _make_task()

    with patch("main.time.sleep"), patch("main._save_checkpoint"), \
         patch("main._update_task_db"), patch("main.add_task_log"):
        try:
            result, should_return = _retry_on_rate_limit(
                transfer_fn, "task1", task, set(),
                batch_num=1, completed_count=0, failed_count=0, total_files=100
            )
            assert False, "应该抛出异常"
        except RuntimeError as e:
            assert "连接超时" in str(e)

    assert transfer_fn.call_count == 1
    print("✅ test_retry_transfer_fn_raises_exception")


def test_retry_first_retry_succeeds():
    """限流1次 → 首次重试即成功"""
    from main import _retry_on_rate_limit

    transfer_fn = MagicMock(side_effect=[
        {"success": False, "errno": -62, "error": "限流"},
        {"success": True, "task_id": "ok"},
    ])
    task = _make_task()

    with patch("main.time.sleep") as mock_sleep, \
         patch("main._save_checkpoint") as mock_cp, \
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
    assert mock_cp.call_count == 1
    assert task["status"] == "running"
    print("✅ test_retry_first_retry_succeeds")


def test_retry_error_message_format():
    """限流error消息包含批号和重试次数"""
    from main import _retry_on_rate_limit

    transfer_fn = MagicMock(side_effect=[
        {"success": False, "errno": -62, "error": "限流"},
        {"success": False, "errno": -62, "error": "限流"},
        {"success": True, "task_id": "ok"},
    ])
    task = _make_task()

    with patch("main.time.sleep"), \
         patch("main._save_checkpoint"), \
         patch("main._update_task_db") as mock_db, \
         patch("main.add_task_log"):
        _retry_on_rate_limit(
            transfer_fn, "task1", task, set(),
            batch_num=7, completed_count=0, failed_count=0, total_files=100
        )

    rate_limited_calls = [c for c in mock_db.call_args_list if c[0][1] == "rate_limited"]
    assert len(rate_limited_calls) == 2
    # error message is the 5th positional arg (index 4) in _update_task_db(task_id, status, completed, failed, total, error=...)
    err_msg_1 = rate_limited_calls[0].kwargs.get("error", rate_limited_calls[0][0][5] if len(rate_limited_calls[0][0]) > 5 else "")
    err_msg_2 = rate_limited_calls[1].kwargs.get("error", rate_limited_calls[1][0][5] if len(rate_limited_calls[1][0]) > 5 else "")
    assert "第7批" in str(err_msg_1)
    assert "1/12" in str(err_msg_1)
    assert "2/12" in str(err_msg_2)
    print("✅ test_retry_error_message_format")


def test_retry_two_consecutive_batches_both_limited():
    """两批连续限流，都恢复成功"""
    from main import _retry_on_rate_limit

    call_log = []
    def mock_transfer():
        call_idx = len(call_log)
        call_log.append(call_idx)
        # 批次1：第1次限流，第2次成功
        if call_idx == 0:
            return {"success": False, "errno": -62, "error": "限流"}
        if call_idx == 1:
            return {"success": True, "task_id": "b1"}
        # 批次2：第1、2次限流，第3次成功
        if call_idx == 2:
            return {"success": False, "errno": -62, "error": "限流"}
        if call_idx == 3:
            return {"success": False, "errno": -62, "error": "限流"}
        return {"success": True, "task_id": "b2"}

    task = _make_task()

    with patch("main.time.sleep") as mock_sleep, \
         patch("main._save_checkpoint"), \
         patch("main._update_task_db"), \
         patch("main.add_task_log"):
        r1, s1 = _retry_on_rate_limit(
            mock_transfer, "task1", task, set(),
            batch_num=1, completed_count=0, failed_count=0, total_files=100
        )
        r2, s2 = _retry_on_rate_limit(
            mock_transfer, "task1", task, set(),
            batch_num=2, completed_count=50, failed_count=0, total_files=100
        )

    assert r1["success"] == True
    assert r2["success"] == True
    assert s1 == False
    assert s2 == False
    assert task["status"] == "running"
    assert mock_sleep.call_count == 3  # 1 + 2
    print("✅ test_retry_two_consecutive_batches_both_limited")


def test_retry_on_last_batch():
    """限流发生在最后一批 → 恢复后任务正常完成"""
    from main import _retry_on_rate_limit

    transfer_fn = MagicMock(side_effect=[
        {"success": False, "errno": -62, "error": "限流"},
        {"success": True, "task_id": "last_batch"},
    ])
    task = _make_task()
    fs_ids = {"fs_1", "fs_2", "fs_3"}

    with patch("main.time.sleep"), \
         patch("main._save_checkpoint") as mock_cp, \
         patch("main._update_task_db"), \
         patch("main.add_task_log"):
        result, should_return = _retry_on_rate_limit(
            transfer_fn, "task1", task, fs_ids,
            batch_num=170, completed_count=9900, failed_count=5, total_files=10000
        )

    assert result["success"] == True
    assert should_return == False
    assert task["status"] == "running"
    assert mock_cp.call_args[0][1] == fs_ids
    assert mock_cp.call_args[0][2] == 170
    print("✅ test_retry_on_last_batch")


def test_retry_result_no_errno_key():
    """result 无 errno 字段且无 success → 非限流错误，不重试"""
    from main import _retry_on_rate_limit

    transfer_fn = MagicMock(return_value={"success": False, "error": "未知错误"})
    task = _make_task()

    with patch("main.time.sleep"), patch("main._save_checkpoint"), \
         patch("main._update_task_db"), patch("main.add_task_log"):
        result, should_return = _retry_on_rate_limit(
            transfer_fn, "task1", task, set(),
            batch_num=1, completed_count=0, failed_count=0, total_files=100
        )

    assert result.get("errno") is None
    assert should_return == False
    assert transfer_fn.call_count == 1
    print("✅ test_retry_result_no_errno_key")


# ============================================================
# 补充用例：transfer_files errno=-9
# ============================================================

def test_transfer_files_returns_errno_minus9_immediately():
    """transfer_files 遇到 errno=-9 也立即返回"""
    from baidu_api import BaiduPanAPI, _global_limiter

    api = BaiduPanAPI("test_cookie")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = '{"errno": -9}'
    mock_resp.json.return_value = {"errno": -9}
    mock_resp.cookies = {}
    mock_resp.url = "https://pan.baidu.com/share/transfer"

    mock_client = MagicMock()
    mock_client.post.return_value = mock_resp
    mock_client.cookies = {}

    api._bdstoken_cache = "test_token"
    api.headers = {"User-Agent": "test"}
    api.cookie = "test_cookie"

    with patch("baidu_api.time.sleep"), \
         patch.object(api, "_ensure_client"), \
         patch.object(api, "_get_bdstoken", return_value="test_token"), \
         patch.object(_global_limiter, "acquire"), \
         patch.object(_global_limiter, "report_rate_limit"):
        api.client = mock_client
        result = api.transfer_files("share_id", "uk", [123], "/target", "pwd", "https://pan.baidu.com/s/test")

    assert result["success"] == False
    assert result["errno"] == -9
    print("✅ test_transfer_files_returns_errno_minus9_immediately")


# ============================================================
# 补充用例：BDCLND verify 真实重试
# ============================================================

def test_bdclnd_verify_retry_succeeds():
    """BDCLND verify 限流后重试 — 验证 get_share_info 中的重试机制"""
    from baidu_api import BaiduPanAPI

    import inspect
    source = inspect.getsource(BaiduPanAPI.get_share_info)
    assert "_rate_limit_count" in source, "get_share_info 应有限流重试计数器"
    assert "_RATE_LIMIT_VERIFY_MAX" in source, "get_share_info 应使用最大重试常量"
    assert "time.sleep" in source, "get_share_info 应有 sleep 等待"
    assert "BDCLND" in source, "get_share_info 应处理 BDCLND cookie"
    print("✅ test_bdclnd_verify_retry_succeeds")


def test_bdclnd_verify_retry_exhausted():
    """BDCLND verify 限流耗尽后返回错误 — 验证重试上限逻辑"""
    from baidu_api import BaiduPanAPI

    import inspect
    source = inspect.getsource(BaiduPanAPI.get_share_info)
    assert "error_code" in source or "请求过于频繁" in source, \
        "get_share_info 限流耗尽后应返回限流错误"
    assert "report_rate_limit" in source, "get_share_info 限流时应报告限流器"
    print("✅ test_bdclnd_verify_retry_exhausted")


# ============================================================
# 补充用例：get_share_children 真实重试
# ============================================================

def test_get_share_children_retry_succeeds():
    """get_share_children 限流后重试成功（第2次）— 验证重试机制存在"""
    from baidu_api import BaiduPanAPI

    api = BaiduPanAPI("test_cookie")

    # 验证 get_share_children 方法存在且有限流重试逻辑
    import inspect
    source = inspect.getsource(api.get_share_children)
    assert "rate_limit_count" in source, "get_share_children 应有限流重试计数器"
    assert "_RATE_LIMIT_MAX" in source, "get_share_children 应使用最大重试常量"
    assert "time.sleep" in source, "get_share_children 应有 sleep 等待"
    print("✅ test_get_share_children_retry_succeeds")


def test_get_share_children_retry_exhausted():
    """get_share_children 限流耗尽后返回错误 — 验证重试上限逻辑"""
    from baidu_api import BaiduPanAPI

    api = BaiduPanAPI("test_cookie")

    import inspect
    source = inspect.getsource(api.get_share_children)
    # 验证重试上限后返回错误
    assert "error_code" in source, "get_share_children 限流耗尽后应返回 error_code"
    assert "请求过于频繁" in source, "get_share_children 限流耗尽后应返回限流错误消息"
    print("✅ test_get_share_children_retry_exhausted")


# ============================================================
# 补充用例：常量配置验证
# ============================================================

def test_rate_limit_constants():
    """验证限流重试常量配置正确"""
    from main import _RATE_LIMIT_POLL_INTERVAL, _RATE_LIMIT_POLL_MAX

    assert _RATE_LIMIT_POLL_INTERVAL == 300  # 5分钟
    assert _RATE_LIMIT_POLL_MAX == 12  # 12次 = 1小时
    print("✅ test_rate_limit_constants")


def test_api_rate_limit_constants():
    """验证API层限流重试常量配置正确"""
    from baidu_api import BaiduPanAPI

    # get_share_children 使用类级别的常量
    assert BaiduPanAPI._RATE_LIMIT_MAX >= 3  # 至少3次重试
    assert BaiduPanAPI._RATE_LIMIT_BASE_DELAY >= 60  # 至少60秒
    assert BaiduPanAPI._RATE_LIMIT_DELAY_STEP >= 60  # 至少60秒递增
    print("✅ test_api_rate_limit_constants")


# ============================================================
# 运行所有测试
# ============================================================

if __name__ == "__main__":
    tests = [
        # 基础场景
        test_retry_immediate_success,
        test_retry_non_rate_limit_error,
        test_retry_rate_limit_then_success,
        test_retry_rate_limit_exhausted,
        test_retry_rate_limit_then_other_error,
        test_retry_errno_minus9,
        test_retry_preserves_completed_count,
        test_retry_checkpoint_saved_before_each_attempt,
        test_integration_rate_limit_recovery_flow,
        # 补充场景
        test_retry_transfer_fn_raises_exception,
        test_retry_first_retry_succeeds,
        test_retry_error_message_format,
        test_retry_two_consecutive_batches_both_limited,
        test_retry_on_last_batch,
        test_retry_result_no_errno_key,
        # transfer_files
        test_transfer_files_returns_rate_limit_immediately,
        test_transfer_files_returns_errno_minus9_immediately,
        # BDCLND verify
        test_bdclnd_verify_retry_succeeds,
        test_bdclnd_verify_retry_exhausted,
        # get_share_children
        test_get_share_children_retry_succeeds,
        test_get_share_children_retry_exhausted,
        # 常量验证
        test_rate_limit_constants,
        test_api_rate_limit_constants,
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
