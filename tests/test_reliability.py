"""可靠性/韧性测试

覆盖场景：
1. 网络超时处理（httpx.TimeoutException）
2. 连接断开恢复（httpx.NetworkError）
3. Cookie 过期检测（errno=-3）
4. 分享链接过期（errno=-19/-20）
5. 并发任务安全（多线程写 active_tasks）
"""
import sys
import os
import threading
import time
from unittest.mock import patch, MagicMock, PropertyMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx


def _mock_api():
    """创建 mock 的 BaiduPanAPI 实例"""
    from baidu_api import BaiduPanAPI

    api = BaiduPanAPI("test_cookie")
    api._bdstoken_cache = "test_token"
    api.headers = {"User-Agent": "test"}

    # 创建 mock client，设置 is_closed = False 避免重建
    mock_client = MagicMock()
    mock_client.is_closed = False
    api.client = mock_client

    return api


# ============================================================
# 1.1 网络超时处理
# ============================================================

def test_timeout_get_share_children():
    """get_share_children 超时后重试"""
    from baidu_api import _global_limiter

    api = _mock_api()
    call_count = [0]

    def mock_get(url, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise httpx.TimeoutException("timeout")
        resp = MagicMock()
        resp.text = '{"errno": 0, "list": [{"fs_id": 1, "server_filename": "test.pdf", "isdir": 0, "path": "/test.pdf", "size": 100}]}'
        resp.json.return_value = {"errno": 0, "list": [{"fs_id": 1, "server_filename": "test.pdf", "isdir": 0, "path": "/test.pdf", "size": 100}]}
        return resp

    api.client.get = mock_get

    with patch("baidu_api.time.sleep"), patch.object(_global_limiter, "acquire"):
        result = api.get_share_children("1test123", "/")

    assert "list" in result or result.get("errno") == 0
    assert call_count[0] >= 3  # 至少重试了2次
    print("✅ test_timeout_get_share_children")


def test_timeout_transfer_files():
    """transfer_files 超时后返回错误"""
    from baidu_api import _global_limiter

    api = _mock_api()
    api.client.post = MagicMock(side_effect=httpx.TimeoutException("timeout"))

    with patch("baidu_api.time.sleep"), patch.object(_global_limiter, "acquire"):
        result = api.transfer_files(
            share_id="987654321",
            uk="123456789",
            file_paths=["/test.pdf"],
            target_path="/backup"
        )

    assert result.get("error_code") or result.get("errno") or "timeout" in str(result).lower()
    print("✅ test_timeout_transfer_files")


# ============================================================
# 1.2 连接断开恢复
# ============================================================

def test_network_error_get_share_children():
    """get_share_children 网络断开后重试"""
    from baidu_api import _global_limiter

    api = _mock_api()
    call_count = [0]

    def mock_get(url, **kwargs):
        call_count[0] += 1
        if call_count[0] <= 2:
            raise httpx.NetworkError("connection reset")
        resp = MagicMock()
        resp.text = '{"errno": 0, "list": []}'
        resp.json.return_value = {"errno": 0, "list": []}
        return resp

    api.client.get = mock_get

    with patch("baidu_api.time.sleep"), patch.object(_global_limiter, "acquire"):
        result = api.get_share_children("1test123", "/")

    assert call_count[0] >= 3
    print("✅ test_network_error_get_share_children")


# ============================================================
# 1.3 Cookie 过期检测
# ============================================================

def test_cookie_expired_errno_minus3():
    """errno=-3 表示 Cookie/登录过期"""
    from baidu_api import _global_limiter

    api = _mock_api()
    resp = MagicMock()
    resp.text = '{"errno": -3, "errmsg": "login expired"}'
    resp.json.return_value = {"errno": -3, "errmsg": "login expired"}
    api.client.get = MagicMock(return_value=resp)

    with patch("baidu_api.time.sleep"), patch.object(_global_limiter, "acquire"):
        result = api.get_share_children("1test123", "/")

    assert result.get("error") == "用户未登录" or result.get("errno") == -3
    print("✅ test_cookie_expired_errno_minus3")


def test_cookie_expired_no_retry():
    """Cookie 过期不应重试（重试无意义）"""
    from baidu_api import _global_limiter

    api = _mock_api()
    call_count = [0]

    def mock_get(url, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        resp.text = '{"errno": -3}'
        resp.json.return_value = {"errno": -3}
        return resp

    api.client.get = mock_get

    with patch("baidu_api.time.sleep"), patch.object(_global_limiter, "acquire"):
        result = api.get_share_children("1test123", "/")

    # Cookie 过期应该立即返回，不重试
    assert call_count[0] == 1
    print("✅ test_cookie_expired_no_retry")


# ============================================================
# 1.4 分享链接过期
# ============================================================

def test_share_expired_errno_minus19():
    """errno=-19 表示分享链接已过期"""
    from baidu_api import _global_limiter

    api = _mock_api()
    resp = MagicMock()
    resp.text = '{"errno": -19}'
    resp.json.return_value = {"errno": -19}
    api.client.get = MagicMock(return_value=resp)

    with patch("baidu_api.time.sleep"), patch.object(_global_limiter, "acquire"):
        result = api.get_share_children("1test123", "/")

    assert result.get("error") == "分享链接已失效" or result.get("errno") == -19
    print("✅ test_share_expired_errno_minus19")


def test_share_expired_errno_minus20():
    """errno=-20 表示分享链接已被取消"""
    from baidu_api import _global_limiter

    api = _mock_api()
    resp = MagicMock()
    resp.text = '{"errno": -20}'
    resp.json.return_value = {"errno": -20}
    api.client.get = MagicMock(return_value=resp)

    with patch("baidu_api.time.sleep"), patch.object(_global_limiter, "acquire"):
        result = api.get_share_children("1test123", "/")

    assert result.get("error") == "分享链接已过期" or result.get("errno") == -20
    print("✅ test_share_expired_errno_minus20")


def test_share_expired_no_retry():
    """分享链接过期不应重试（重试无意义）"""
    from baidu_api import _global_limiter

    api = _mock_api()
    call_count = [0]

    def mock_get(url, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        resp.text = '{"errno": -19}'
        resp.json.return_value = {"errno": -19}
        return resp

    api.client.get = mock_get

    with patch("baidu_api.time.sleep"), patch.object(_global_limiter, "acquire"):
        result = api.get_share_children("1test123", "/")

    assert call_count[0] == 1
    print("✅ test_share_expired_no_retry")


# ============================================================
# 1.5 并发任务安全
# ============================================================

def test_concurrent_active_tasks_write():
    """多线程同时写 active_tasks 不崩溃"""
    import main

    # 重置
    main.active_tasks.clear()
    errors = []

    def add_task(task_id):
        try:
            main.active_tasks[task_id] = {
                "status": "running",
                "task_id": task_id,
                "progress": {"total": 0, "completed": 0}
            }
            time.sleep(0.01)  # 模拟工作
            main.active_tasks[task_id]["status"] = "completed"
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(10):
        t = threading.Thread(target=add_task, args=(f"task_{i}",))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0, f"并发写入出错: {errors}"
    assert len(main.active_tasks) == 10
    for i in range(10):
        assert main.active_tasks[f"task_{i}"]["status"] == "completed"
    print("✅ test_concurrent_active_tasks_write")


def test_concurrent_task_status_read_write():
    """多线程同时读写任务状态"""
    import main

    main.active_tasks.clear()
    main.active_tasks["shared_task"] = {
        "status": "running",
        "task_id": "shared_task",
        "progress": {"total": 100, "completed": 0}
    }

    errors = []
    stop = threading.Event()

    def writer():
        try:
            for i in range(100):
                main.active_tasks["shared_task"]["progress"]["completed"] = i
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            while not stop.is_set():
                _ = main.active_tasks.get("shared_task", {}).get("status")
                _ = main.active_tasks.get("shared_task", {}).get("progress", {}).get("completed")
                time.sleep(0.001)
        except Exception as e:
            errors.append(e)

    threads = []
    for _ in range(3):
        threads.append(threading.Thread(target=writer))
        threads.append(threading.Thread(target=reader))

    for t in threads:
        t.start()

    time.sleep(0.5)
    stop.set()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"并发读写出错: {errors}"
    print("✅ test_concurrent_task_status_read_write")


if __name__ == "__main__":
    tests = [
        test_timeout_get_share_children,
        test_timeout_transfer_files,
        test_network_error_get_share_children,
        test_cookie_expired_errno_minus3,
        test_cookie_expired_no_retry,
        test_share_expired_errno_minus19,
        test_share_expired_errno_minus20,
        test_share_expired_no_retry,
        test_concurrent_active_tasks_write,
        test_concurrent_task_status_read_write,
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
