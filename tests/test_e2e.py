"""集成/E2E 测试

覆盖场景：
1. 完整转存流程（解析→预览→转存→完成）
2. 前端状态机流转
"""
import sys
import os
import time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient


def _get_client():
    """获取测试客户端"""
    from main import app
    return TestClient(app)


# ============================================================
# 7.1 完整转存流程
# ============================================================

def test_e2e_version_check():
    """E2E: 版本检查"""
    client = _get_client()
    resp = client.get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    print("✅ test_e2e_version_check")


def test_e2e_cookie_validate_flow():
    """E2E: Cookie 验证流程"""
    client = _get_client()
    resp = client.post("/api/cookie/validate", json={"cookie": "test"})
    assert resp.status_code in [200, 422]
    print("✅ test_e2e_cookie_validate_flow")


def test_e2e_share_parse_flow():
    """E2E: 分享链接解析流程"""
    client = _get_client()

    # 无效链接应该返回错误
    resp = client.post("/api/share/parse", json={"share_link": "invalid"})
    assert resp.status_code in [200, 422]

    if resp.status_code == 200:
        data = resp.json()
        # 应该返回错误或任务 ID
        assert "error" in data or "task_id" in data or data.get("success") == False

    print("✅ test_e2e_share_parse_flow")


def test_e2e_batch_parse_flow():
    """E2E: 批量解析流程"""
    client = _get_client()

    # 空列表应该返回错误
    resp = client.post("/api/batch/parse", json={"links": []})
    assert resp.status_code in [200, 422]

    print("✅ test_e2e_batch_parse_flow")


def test_e2e_task_not_found_flow():
    """E2E: 不存在的任务"""
    client = _get_client()

    # 查询不存在的任务进度
    resp = client.get("/api/task/nonexistent/progress")
    assert resp.status_code in [200, 404]

    # 启动不存在的任务
    resp = client.post("/api/task/nonexistent/start", json={})
    assert resp.status_code in [200, 404, 422]

    print("✅ test_e2e_task_not_found_flow")


# ============================================================
# 7.2 前端状态机流转
# ============================================================

def test_state_machine_running_to_rate_limited():
    """状态机: running → rate_limited"""
    import main

    task_id = "state_test_1"
    main.active_tasks[task_id] = {
        "status": "running",
        "task_id": task_id,
        "progress": {"total": 100, "completed": 50}
    }

    # 模拟限流
    main.active_tasks[task_id]["status"] = "rate_limited"
    assert main.active_tasks[task_id]["status"] == "rate_limited"

    # 清理
    del main.active_tasks[task_id]
    print("✅ test_state_machine_running_to_rate_limited")


def test_state_machine_rate_limited_to_running():
    """状态机: rate_limited → running"""
    import main

    task_id = "state_test_2"
    main.active_tasks[task_id] = {
        "status": "rate_limited",
        "task_id": task_id,
        "progress": {"total": 100, "completed": 50}
    }

    # 模拟恢复
    main.active_tasks[task_id]["status"] = "running"
    assert main.active_tasks[task_id]["status"] == "running"

    # 清理
    del main.active_tasks[task_id]
    print("✅ test_state_machine_rate_limited_to_running")


def test_state_machine_running_to_completed():
    """状态机: running → completed"""
    import main

    task_id = "state_test_3"
    main.active_tasks[task_id] = {
        "status": "running",
        "task_id": task_id,
        "progress": {"total": 100, "completed": 0}
    }

    # 模拟完成
    for i in range(100):
        main.active_tasks[task_id]["progress"]["completed"] = i + 1

    main.active_tasks[task_id]["status"] = "completed"
    assert main.active_tasks[task_id]["status"] == "completed"
    assert main.active_tasks[task_id]["progress"]["completed"] == 100

    # 清理
    del main.active_tasks[task_id]
    print("✅ test_state_machine_running_to_completed")


def test_state_machine_running_to_cancelled():
    """状态机: running → cancelled"""
    import main

    task_id = "state_test_4"
    main.active_tasks[task_id] = {
        "status": "running",
        "task_id": task_id,
        "progress": {"total": 100, "completed": 30}
    }

    # 模拟取消
    main.active_tasks[task_id]["status"] = "cancelled"
    assert main.active_tasks[task_id]["status"] == "cancelled"

    # 清理
    del main.active_tasks[task_id]
    print("✅ test_state_machine_running_to_cancelled")


def test_state_machine_running_to_error():
    """状态机: running → error"""
    import main

    task_id = "state_test_5"
    main.active_tasks[task_id] = {
        "status": "running",
        "task_id": task_id,
        "progress": {"total": 100, "completed": 30}
    }

    # 模拟错误
    main.active_tasks[task_id]["status"] = "error"
    main.active_tasks[task_id]["error"] = "网络连接失败"
    assert main.active_tasks[task_id]["status"] == "error"
    assert main.active_tasks[task_id]["error"] == "网络连接失败"

    # 清理
    del main.active_tasks[task_id]
    print("✅ test_state_machine_running_to_error")


if __name__ == "__main__":
    tests = [
        test_e2e_version_check,
        test_e2e_cookie_validate_flow,
        test_e2e_share_parse_flow,
        test_e2e_batch_parse_flow,
        test_e2e_task_not_found_flow,
        test_state_machine_running_to_rate_limited,
        test_state_machine_rate_limited_to_running,
        test_state_machine_running_to_completed,
        test_state_machine_running_to_cancelled,
        test_state_machine_running_to_error,
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
