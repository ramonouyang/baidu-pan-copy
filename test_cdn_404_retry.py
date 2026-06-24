#!/usr/bin/env python3
"""测试 CDN 404 重试逻辑"""
import unittest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from baidu_api import BaiduPanAPI, safe_json_parse


class TestCDN404Retry(unittest.TestCase):
    """测试 CDN 404 重试逻辑"""
    
    def setUp(self):
        self.api = BaiduPanAPI(cookie="test_cookie")
        self.api._ensure_client = MagicMock()
        self.api.client = MagicMock()
        self.api._share_headers = MagicMock(return_value={"Cookie": "test"})
        self.api._children_cache = {}
        # Mock rate limiter
        import baidu_api
        self._orig_limiter = baidu_api._global_limiter
        baidu_api._global_limiter = MagicMock()
    
    def tearDown(self):
        import baidu_api
        baidu_api._global_limiter = self._orig_limiter
    
    def test_404_retry_then_success(self):
        """HTTP 404 重试后成功"""
        mock_404 = MagicMock(status_code=404, url='')
        mock_404.text = '<html><head><title>404 Not Found</title></head></html>'
        
        mock_ok = MagicMock(status_code=200)
        mock_ok.text = '{"errno": 0, "list": [{"path": "/a.mp4", "isdir": 0, "fs_id": 1}]}'
        mock_ok.json.return_value = {"errno": 0, "list": [{"path": "/a.mp4", "isdir": 0, "fs_id": 1}]}
        
        self.api.client.get.side_effect = [mock_404, mock_ok]
        
        with patch('time.sleep'):
            result = self.api.get_share_children("surl", "/dir")
        
        self.assertIn("list", result)
        self.assertEqual(len(result["list"]), 1)
        self.assertEqual(self.api.client.get.call_count, 2)
        # 验证连接被重建
        self.api.client.close.assert_called()
    
    def test_404_exhausted(self):
        """HTTP 404 重试次数用尽"""
        mock_404 = MagicMock(status_code=404, url='')
        mock_404.text = '<html><head><title>404 Not Found</title></head></html>'
        
        self.api.client.get.return_value = mock_404
        
        with patch('time.sleep'):
            result = self.api.get_share_children("surl", "/dir")
        
        self.assertIn("error", result)
        self.assertIn("CDN 404", result["error"])
        # 初始调用 + 5 次重试 = 6 次
        self.assertEqual(self.api.client.get.call_count, 6)
    
    def test_404_share_expired(self):
        """分享链接失效 - 页面包含'页面不存在'"""
        mock_404 = MagicMock(status_code=404)
        mock_404.text = '<html><head><title>页面不存在</title></head><body>啊哦，你所访问的页面不存在了。</body></html>'
        mock_404.url = ''
        
        self.api.client.get.return_value = mock_404
        
        result = self.api.get_share_children("surl", "/dir")
        
        self.assertIn("error", result)
        self.assertIn("分享链接已失效", result["error"])
        self.assertTrue(result.get("share_expired"))
        # 分享失效不应重试
        self.assertEqual(self.api.client.get.call_count, 1)
    
    def test_normal_api_error_no_retry(self):
        """普通 API 错误不重试"""
        mock_resp = MagicMock(status_code=200)
        mock_resp.text = '{"errno": -21, "errmsg": "提取码错误"}'
        mock_resp.json.return_value = {"errno": -21, "errmsg": "提取码错误"}
        
        self.api.client.get.return_value = mock_resp
        
        with patch('time.sleep'):
            result = self.api.get_share_children("surl", "/dir")
        
        self.assertIn("error", result)
        self.assertEqual(self.api.client.get.call_count, 1)
    
    def test_server_error_retry(self):
        """HTTP 500 服务器错误重试"""
        mock_500 = MagicMock(status_code=500, text='Internal Server Error')
        
        mock_ok = MagicMock(status_code=200)
        mock_ok.text = '{"errno": 0, "list": []}'
        mock_ok.json.return_value = {"errno": 0, "list": []}
        
        self.api.client.get.side_effect = [mock_500, mock_ok]
        
        with patch('time.sleep'):
            result = self.api.get_share_children("surl", "/dir")
        
        self.assertIn("list", result)
        self.assertEqual(self.api.client.get.call_count, 2)


class TestSafeJsonParse(unittest.TestCase):
    """测试 safe_json_parse 函数"""
    
    def test_html_response(self):
        mock_resp = MagicMock()
        mock_resp.text = '<html><head><title>404 Not Found</title></head></html>'
        mock_resp.url = 'https://pan.baidu.com/share/list'
        
        result = safe_json_parse(mock_resp)
        self.assertIn("error", result)
    
    def test_json_response(self):
        mock_resp = MagicMock()
        mock_resp.text = '{"errno": 0}'
        mock_resp.json.return_value = {"errno": 0}
        
        result = safe_json_parse(mock_resp)
        self.assertEqual(result["errno"], 0)
    
    def test_empty_response(self):
        mock_resp = MagicMock()
        mock_resp.text = ''
        mock_resp.url = ''
        
        result = safe_json_parse(mock_resp)
        self.assertIn("error", result)


if __name__ == '__main__':
    unittest.main()
