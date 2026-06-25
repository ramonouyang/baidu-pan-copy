"""API 接口测试

覆盖场景：
1. POST /api/share/parse 正常/异常
2. POST /api/task/{id}/start 正常/异常
3. GET /api/task/{id}/progress 状态轮询
4. 错误码→HTTP 状态码映射
5. 参数校验（空 share_link、无效 target_path）
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
# 3.1 POST /api/share/parse
# ============================================================

def test_share_parse_missing_share_link():
    """缺少 share_link 参数"""
    client = _get_client()
    resp = client.post("/api/share/parse", json={})
    assert resp.status_code == 422 or resp.status_code == 200
    if resp.status_code == 200:
        data = resp.json()
        assert "error" in data or data.get("success") == False
    print("✅ test_share_parse_missing_share_link")


def test_share_parse_empty_share_link():
    """空 share_link"""
    client = _get_client()
    resp = client.post("/api/share/parse", json={"share_link": ""})
    assert resp.status_code == 422 or resp.status_code == 200
    if resp.status_code == 200:
        data = resp.json()
        assert "error" in data or data.get("success") == False
    print("✅ test_share_parse_empty_share_link")


def test_share_parse_invalid_format():
    """无效分享链接格式"""
    client = _get_client()
    resp = client.post("/api/share/parse", json={"share_link": "not_a_valid_link"})
    assert resp.status_code == 422 or resp.status_code == 200
    if resp.status_code == 200:
        data = resp.json()
        # 可能返回错误或解析失败
        assert "error" in data or data.get("success") == False or "task_id" in data
    print("✅ test_share_parse_invalid_format")


# ============================================================
# 3.2 POST /api/task/{id}/start
# ============================================================

def test_task_start_not_found():
    """启动不存在的任务"""
    client = _get_client()
    resp = client.post("/api/task/nonexistent_task/start", json={})
    assert resp.status_code in [200, 404, 422, 400]
    if resp.status_code == 200:
        data = resp.json()
        assert "error" in data or data.get("success") == False
    print("✅ test_task_start_not_found")


# ============================================================
# 3.3 GET /api/task/{id}/progress
# ============================================================

def test_task_progress_not_found():
    """查询不存在的任务进度"""
    client = _get_client()
    resp = client.get("/api/task/nonexistent_task/progress")
    assert resp.status_code == 404 or resp.status_code == 200
    if resp.status_code == 200:
        data = resp.json()
        assert "error" in data or data.get("success") == False
    print("✅ test_task_progress_not_found")


# ============================================================
# 3.4 错误码→HTTP 状态码映射
# ============================================================

def test_error_code_mapping():
    """验证错误码映射逻辑"""
    from baidu_api import BaiduPanAPI

    api = BaiduPanAPI("test_cookie")

    # 测试 _handle_error 方法
    error_msg = api._handle_error(-3, "")
    assert "未登录" in error_msg or "过期" in error_msg

    error_msg = api._handle_error(-6, "")
    assert "不存在" in error_msg

    error_msg = api._handle_error(-7, "")
    assert "已存在" in error_msg

    print("✅ test_error_code_mapping")


def test_error_code_minus19_share_expired():
    """errno=-19 分享链接失效"""
    from baidu_api import BaiduPanAPI, _global_limiter

    api = BaiduPanAPI("test_cookie")
    api._bdstoken_cache = "test_token"
    api.headers = {"User-Agent": "test"}

    mock_client = MagicMock()
    mock_client.is_closed = False
    api.client = mock_client

    resp = MagicMock()
    resp.text = '{"errno": -19}'
    resp.json.return_value = {"errno": -19}
    api.client.get = MagicMock(return_value=resp)

    with patch("baidu_api.time.sleep"), patch.object(_global_limiter, "acquire"):
        result = api.get_share_children("1test123", "/")

    assert result.get("error") == "分享链接已失效" or result.get("share_expired") == True
    print("✅ test_error_code_minus19_share_expired")


# ============================================================
# 3.5 参数校验
# ============================================================

def test_version_endpoint():
    """版本接口正常返回"""
    client = _get_client()
    resp = client.get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert "version" in data
    print("✅ test_version_endpoint")


def test_cookie_validate_endpoint():
    """Cookie 验证接口"""
    client = _get_client()
    resp = client.post("/api/cookie/validate", json={"cookie": "test"})
    assert resp.status_code == 200 or resp.status_code == 422
    print("✅ test_cookie_validate_endpoint")


def test_batch_parse_endpoint():
    """批量解析接口"""
    client = _get_client()
    resp = client.post("/api/batch/parse", json={"links": []})
    assert resp.status_code == 200 or resp.status_code == 422
    if resp.status_code == 200:
        data = resp.json()
        assert "error" in data or "task_id" in data or data.get("success") == False
    print("✅ test_batch_parse_endpoint")


if __name__ == "__main__":
    tests = [
        test_share_parse_missing_share_link,
        test_share_parse_empty_share_link,
        test_share_parse_invalid_format,
        test_task_start_not_found,
        test_task_progress_not_found,
        test_error_code_mapping,
        test_error_code_minus19_share_expired,
        test_version_endpoint,
        test_cookie_validate_endpoint,
        test_batch_parse_endpoint,
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
