"""百度网盘 API 封装 - 增强版（限流防护）"""
import httpx
import time
import json
import asyncio
import threading
import logging
import collections
from typing import Optional, List
from urllib.parse import quote, unquote
from functools import wraps

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('BaiduPanAPI')


def safe_json_parse(resp):
    """安全解析 JSON 响应，处理空响应和非 JSON 内容"""
    try:
        text = resp.text.strip()
        if not text:
            logger.warning(f"API返回空响应: URL={resp.url}, HTTP {resp.status_code}")
            return {"error": f"API返回空响应 (HTTP {resp.status_code})"}
        # 检查是否以 { 或 [ 开头（JSON 格式）
        if not (text.startswith('{') or text.startswith('[')):
            logger.warning(f"API返回非JSON内容: URL={resp.url}, HTTP {resp.status_code}, body={text[:100]}")
            return {"error": f"API返回非JSON内容: {text[:100]}..."}
        return resp.json()
    except json.JSONDecodeError as e:
        logger.error(f"JSON解析失败: URL={resp.url}, error={str(e)}, body={resp.text[:200]}")
        return {"error": f"JSON解析失败: {str(e)}, 响应内容: {resp.text[:200]}..."}
    except Exception as e:
        logger.error(f"解析响应失败: URL={resp.url}, error={str(e)}")
        return {"error": f"解析响应失败: {str(e)}"}


def retry_on_error(max_retries=3, delay=1, backoff=2):
    """重试装饰器（仅用于网络错误，不用于API错误）"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    retries += 1
                    if retries == max_retries:
                        logger.error(f"网络错误，已重试{max_retries}次: {e}")
                        raise
                    wait_time = delay * (backoff ** (retries - 1))
                    logger.warning(f"网络错误，{wait_time}秒后重试 ({retries}/{max_retries}): {e}")
                    time.sleep(wait_time)
                except Exception as e:
                    logger.error(f"未知错误: {e}")
                    raise
        return wrapper
    return decorator


class RateLimiter:
    """令牌桶速率控制器 — 限制全局API请求频率，避免触发百度限流
    
    百度网盘限流机制：
    1. errno -62/-9 表示请求过于频繁
    2. 账号级限流，持续 30-60+ 分钟
    3. 重试会延长冷却期（雪崩效应）
    4. 不仅限QPS，还限总请求量（滑动窗口）
    
    设计原则：
    1. 所有API调用必须通过此限流器
    2. 遇到 errno -62/-9 立即返回，不重试
    3. 动态降速：被限流后临时降低速率
    4. 请求预算：每个时间窗口限制总请求数
    """
    
    # 请求预算：每 N 秒最多 M 次请求
    BUDGET_WINDOW = 300    # 5 分钟滑动窗口
    BUDGET_LIMIT = 80      # 每窗口最多 80 次请求（百度安全阈值约 100）
    
    def __init__(self, rate: float = 1.5, burst: int = 2):
        """
        Args:
            rate: 每秒补充的令牌数（即平均QPS上限）
            burst: 突发请求上限（桶容量）
        """
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        # 动态降速：被限流后临时降低速率
        self._penalty_until = 0.0
        # 请求预算（滑动窗口）
        self._request_timestamps: list = []
        self._budget_lock = threading.Lock()
        # 统计信息
        self._total_requests = 0
        self._total_waits = 0
        self._budget_waits = 0
    
    def _refill(self):
        """补充令牌"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        # 被惩罚期间速率降为 1/3
        effective_rate = self.rate / 3 if now < self._penalty_until else self.rate
        self._tokens = min(self.burst, self._tokens + elapsed * effective_rate)
        self._last_refill = now
    
    def _check_budget(self):
        """检查请求预算，如果超限则等待"""
        now = time.monotonic()
        cutoff = now - self.BUDGET_WINDOW
        
        with self._budget_lock:
            # 清理过期的时间戳
            self._request_timestamps = [t for t in self._request_timestamps if t > cutoff]
            
            if len(self._request_timestamps) >= self.BUDGET_LIMIT:
                # 预算用完，等到最早的请求过期
                oldest = self._request_timestamps[0]
                wait_time = oldest - cutoff + 1.0  # +1s 安全余量
                self._budget_waits += 1
                logger.warning(f"请求预算已满 ({len(self._request_timestamps)}/{self.BUDGET_LIMIT})，等待 {wait_time:.0f}s")
                return wait_time
            return 0.0
    
    def _record_request(self):
        """记录一次请求"""
        with self._budget_lock:
            self._request_timestamps.append(time.monotonic())
    
    def acquire(self, timeout: float = 60.0):
        """获取一个令牌，阻塞直到可用（含预算检查）"""
        deadline = time.monotonic() + timeout
        
        # 先检查请求预算
        budget_wait = self._check_budget()
        if budget_wait > 0:
            if budget_wait > timeout:
                logger.warning(f"请求预算等待 {budget_wait:.0f}s 超过超时 {timeout}s，跳过")
                return
            logger.info(f"[DIAG] RateLimiter 预算等待 {budget_wait:.1f}s（期间连接可能过期）")
            time.sleep(budget_wait)
        
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    self._total_requests += 1
                    self._record_request()
                    return
            # 没有令牌了，等待
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("速率控制器等待超时，强制放行")
                self._record_request()
                return
            self._total_waits += 1
            time.sleep(0.5)
    
    def report_rate_limit(self, cooldown: float = 120.0):
        """被百度限流了，触发惩罚期（速率降为 1/3 + 窗口暂停）"""
        self._penalty_until = time.monotonic() + cooldown
        # 被限流时清空令牌，强制等待
        with self._lock:
            self._tokens = 0.0
        logger.warning(f"触发限流惩罚：未来 {cooldown}s 内请求速率降为 1/3，令牌清空")
    
    @property
    def penalty_active(self) -> bool:
        return time.monotonic() < self._penalty_until
    
    def get_stats(self) -> dict:
        """获取统计信息"""
        now = time.monotonic()
        cutoff = now - self.BUDGET_WINDOW
        with self._budget_lock:
            recent = len([t for t in self._request_timestamps if t > cutoff])
        return {
            "total_requests": self._total_requests,
            "total_waits": self._total_waits,
            "budget_waits": self._budget_waits,
            "penalty_active": self.penalty_active,
            "current_tokens": round(self._tokens, 1),
            "recent_requests": recent,
            "budget_remaining": max(0, self.BUDGET_LIMIT - recent)
        }


# 全局速率控制器实例
# 1.5 QPS + 突发2 — 保守设置，配合请求预算避免触发百度限流
_global_limiter = RateLimiter(rate=1.5, burst=2)


class BaiduPanAPIError(Exception):
    """百度网盘API错误基类"""
    pass


class CookieExpiredError(BaiduPanAPIError):
    """Cookie过期错误"""
    pass


class ShareLinkError(BaiduPanAPIError):
    """分享链接错误"""
    pass


class TransferError(BaiduPanAPIError):
    """转存错误"""
    pass


class BaiduPanAPI:
    """百度网盘内部API封装（限流防护版）"""
    
    BASE_URL = "https://pan.baidu.com"
    APP_ID = "250528"
    
    # API endpoints
    LIST_URL = f"{BASE_URL}/rest/2.0/xpan/file?method=list"
    SHARE_VERIFY_URL = f"{BASE_URL}/share/verify"
    SHARE_LIST_URL = f"{BASE_URL}/share/list"
    # ⚠️ 2026-06-16 确认：/share/transfer 返回 404，使用 /rest/2.0/xpan/share?method=transfer
    TRANSFER_URL = f"{BASE_URL}/rest/2.0/xpan/share?method=transfer"
    CREATE_DIR_URL = f"{BASE_URL}/api/create"
    FILE_METAS_URL = f"{BASE_URL}/rest/2.0/xpan/multimedia?method=filemetas"
    UINFO_URL = f"{BASE_URL}/rest/2.0/xpan/nas?method=uinfo"
    
    # 错误码映射
    ERROR_CODES = {
        0: "成功",
        2: "文件名无效或包含非法字符",
        -1: "系统错误",
        -2: "参数错误",
        -3: "用户未登录",
        -4: "Cookie无效或已过期",
        -6: "文件不存在",
        -7: "文件已存在",
        -8: "文件被锁定",
        -9: "空间不足",
        -10: "验证码错误",
        -12: "需要验证码（请在浏览器中访问分享链接完成验证后重试）",
        -19: "分享链接已失效",
        -20: "分享链接已过期",
        -21: "提取码错误",
        -62: "请求过于频繁",
    }
    
    def __init__(self, cookie: str):
        self.cookie = cookie
        self.bdclnd = ""  # Set after share verify
        self.headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://pan.baidu.com/"
        }
        self.client = httpx.Client(headers=self.headers, timeout=30.0)
        # 递归遍历安全限制
        self._max_depth = 15        # 最大递归深度
        self._max_requests = 800    # 最大API请求数（提升以支持更大分享）
        self._request_count = 0     # 当前请求计数
        self._total_files_found = 0 # 累计找到的文件数
        self._traversal_cancelled = False  # 取消标志
        # 进度回调
        self._progress_callback = None  # callback(dirs_scanned, files_found, status_msg)
        # 异步并发控制
        self._concurrency = 5      # 并发请求数（降低以避免触发百度限流）
        # BDCLND 缓存
        self._bdclnd_cache = {}  # surl -> bdclnd
        self._children_cache = {}  # "surl:dir_path" -> children list
        self._bdstoken_cache = ""  # 缓存 bdstoken
    
    def _ensure_client(self):
        """确保 httpx client 可用，如果已关闭则重新创建"""
        if self.client.is_closed:
            logger.info("HTTP client 已关闭，重新创建")
            self.client = httpx.Client(headers=self.headers, timeout=30.0)
    
    def _share_headers(self) -> dict:
        """返回包含 BDCLND cookie 的请求头"""
        h = self.headers.copy()
        if self.bdclnd:
            h["Cookie"] = f"{self.cookie}; BDCLND={self.bdclnd}"
        return h
    
    def _get_bdstoken(self) -> str:
        """获取 bdstoken（CSRF token），转存 API 必需"""
        if self._bdstoken_cache:
            return self._bdstoken_cache
        try:
            self._ensure_client()
            _global_limiter.acquire(timeout=10.0)
            resp = self.client.get(
                "https://pan.baidu.com/api/gettemplatevariable",
                params={"fields": '["bdstoken"]'},
                headers=self.headers,
            )
            data = safe_json_parse(resp)
            token = data.get("result", {}).get("bdstoken", "")
            if token:
                self._bdstoken_cache = token
                logger.info(f"获取 bdstoken 成功: {token[:8]}...")
            else:
                logger.warning(f"获取 bdstoken 失败: {data}")
            return token
        except Exception as e:
            logger.warning(f"获取 bdstoken 异常: {e}")
            return ""
    
    def _handle_error(self, errno: int, errmsg: str = "") -> str:
        """处理错误码，返回友好的错误信息"""
        if errno in self.ERROR_CODES:
            return self.ERROR_CODES[errno]
        return errmsg or f"未知错误 (errno={errno})"
    
    def _check_rate_limit(self, data: dict) -> bool:
        """检查是否被限流，如果是则返回True"""
        errno = data.get("errno", 0)
        if errno in (-62, -9):
            _global_limiter.report_rate_limit(cooldown=60.0)
            return True
        return False
    
    def _extract_surl(self, share_link: str) -> str:
        """从分享链接中提取 surl"""
        if "surl=" in share_link:
            # 私有分享链接: /init?surl=xxx
            return share_link.split("surl=")[1].split("&")[0]
        elif "/s/" in share_link:
            # 公开分享链接: /s/xxx
            return share_link.split("/s/")[1].split("?")[0]
        return ""
    
    def validate_cookie(self) -> dict:
        """验证Cookie有效性，返回用户信息"""
        self._ensure_client()
        try:
            _global_limiter.acquire(timeout=30.0)
            resp = self.client.get(self.UINFO_URL)
            data = safe_json_parse(resp)
            if "error" in data:
                return {"valid": False, "error": data["error"]}
            if data.get("errno") == 0:
                return {
                    "valid": True,
                    "username": data.get("username", data.get("baidu_name", "")),
                    "uk": data.get("uk", ""),
                    "total": data.get("total", 0),
                    "used": data.get("used", 0),
                    "vip_type": data.get("vip_type", 0)
                }
            error_msg = self._handle_error(data.get("errno", -1))
            return {"valid": False, "error": error_msg}
        except httpx.TimeoutException:
            return {"valid": False, "error": "请求超时，请检查网络连接"}
        except Exception as e:
            logger.error(f"验证Cookie失败: {e}")
            return {"valid": False, "error": str(e)}
    
    def get_share_info(self, share_link: str, pwd: str = "", force_refresh: bool = False) -> dict:
        """获取分享链接信息（通过 verify + list 两步完成）"""
        self._ensure_client()
        surl = self._extract_surl(share_link)
        if not surl:
            return {"error": "无效的分享链接"}
        
        try:
            # Step 1: 验证分享链接（设置 BDCLND cookie）
            import time as _time
            
            # 检查 BDCLND 缓存（force_refresh 时跳过缓存）
            if not force_refresh and surl in self._bdclnd_cache:
                self.bdclnd = self._bdclnd_cache[surl]
                # 同步到 httpx client cookie jar
                self.client.cookies.set("BDCLND", self.bdclnd, domain=".baidu.com", path="/")
                logger.info(f"[BDCLND] 使用缓存: surl={surl}, bdclnd={self.bdclnd[:20]}...")
            else:
                reason = "force_refresh" if force_refresh else "缓存未命中"
                old_bdclnd = self._bdclnd_cache.get(surl, "")
                logger.info(f"[BDCLND] 重新verify: surl={surl}, 原因={reason}, 旧值={old_bdclnd[:20] if old_bdclnd else '无'}...")
                _global_limiter.acquire(timeout=30.0)
                verify_params = {
                    "app_id": self.APP_ID,
                    "surl": surl,
                    "channel": "chunlei",
                    "web": "1",
                    "bdstoken": "",
                    "logid": "",
                    "clienttype": "0",
                    "t": str(int(_time.time() * 1000))
                }
                verify_resp = self.client.post(
                    self.SHARE_VERIFY_URL,
                    params=verify_params,
                    data={"pwd": pwd},
                    headers=self.headers,
                    follow_redirects=True
                )
                verify_data = safe_json_parse(verify_resp)
                
                # 提取 BDCLND cookie
                self.bdclnd = verify_resp.cookies.get("BDCLND", "")
                # 同步到 httpx client cookie jar（确保后续请求自动携带）
                if self.bdclnd:
                    self.client.cookies.set("BDCLND", self.bdclnd, domain=".baidu.com", path="/")
                logger.info(f"[BDCLND] verify响应: surl={surl}, errno={verify_data.get('errno', '?')}, 新bdclnd={self.bdclnd[:20] if self.bdclnd else '空'}..., Set-Cookie数={len(verify_resp.headers.get_list('set-cookie'))}")
                
                if "error" in verify_data:
                    return verify_data
                
                errno = verify_data.get("errno", 0)
                if errno == -21:
                    return {"error": "提取码错误"}
                elif errno == -19:
                    return {"error": "分享链接已失效"}
                elif errno == -20:
                    return {"error": "分享链接已过期"}
                elif errno == -12:
                    # 需要验证码 - 提供详细的解决步骤
                    logger.warning(f"百度网盘要求验证码: surl={surl}")
                    return {
                        "error": "需要验证码",
                        "error_code": -12,
                        "solution": "请在浏览器中打开分享链接完成验证后重试",
                        "share_link": share_link
                    }
                elif errno in (-62, -9):
                    # 频率限制 — 立即返回，不重试（重试会加重限流）
                    _global_limiter.report_rate_limit(cooldown=60.0)
                    logger.warning(f"百度频率限制(errno={errno})，不重试，等待用户稍后重试")
                    return {"error": "请求过于频繁，请等待几分钟后重试", "error_code": -62}
                elif errno != 0:
                    error_msg = self._handle_error(errno, verify_data.get("errmsg", ""))
                    return {"error": error_msg}
                
                # 缓存 BDCLND（覆盖旧值）
                if self.bdclnd:
                    old_cached = self._bdclnd_cache.get(surl, "")
                    self._bdclnd_cache[surl] = self.bdclnd
                    logger.info(f"[BDCLND] 缓存更新: surl={surl}, 旧值={old_cached[:20] if old_cached else '无'}... → 新值={self.bdclnd[:20]}...")
            
            # Step 2: 获取文件列表
            _global_limiter.acquire(timeout=30.0)
            list_params = {
                "app_id": self.APP_ID,
                "shorturl": surl,
                "root": "1",
                "page": "1",
                "num": "100",
                "order": "time",
                "channel": "chunlei",
                "web": "1",
                "bdstoken": "",
                "logid": "",
                "clienttype": "0",
            }
            list_resp = self.client.get(
                self.SHARE_LIST_URL,
                params=list_params,
                headers=self._share_headers(),
                follow_redirects=True
            )
            list_data = safe_json_parse(list_resp)
            
            if "error" in list_data:
                return list_data
            
            if list_data.get("errno", -1) != 0:
                errno = list_data.get("errno", -1)
                error_msg = self._handle_error(errno, list_data.get("errmsg", ""))
                logger.warning(f"获取分享文件列表失败: {error_msg} (errno={errno})")
                return {"error": error_msg}
            
            # 提取分享信息
            share_info = {
                "share_id": list_data.get("share_id", ""),
                "uk": list_data.get("uk", ""),
                "title": list_data.get("title", ""),
                "file_count": len(list_data.get("list", [])),
                "files": list_data.get("list", [])
            }
            
            return share_info
            
        except httpx.TimeoutException:
            return {"error": "请求超时，请检查网络连接"}
        except Exception as e:
            logger.error(f"获取分享信息失败: {e}")
            return {"error": str(e)}
    
    def get_share_file_list(self, surl: str, dir_path: str = "/") -> dict:
        """获取分享文件列表"""
        self._ensure_client()
        try:
            _global_limiter.acquire(timeout=30.0)
            params = {
                "app_id": self.APP_ID,
                "shorturl": surl,
                "dir": dir_path,
                "page": "1",
                "num": "100",
                "order": "time",
                "channel": "chunlei",
                "web": "1",
                "bdstoken": "",
                "logid": "",
                "clienttype": "0",
            }
            resp = self.client.get(
                self.SHARE_LIST_URL,
                params=params,
                headers=self._share_headers(),
                follow_redirects=True
            )
            data = safe_json_parse(resp)
            
            if "error" in data:
                return data
            
            if data.get("errno", -1) != 0:
                errno = data.get("errno", -1)
                error_msg = self._handle_error(errno, data.get("errmsg", ""))
                return {"error": error_msg}
            
            return {"list": data.get("list", [])}
            
        except httpx.TimeoutException:
            return {"error": "请求超时，请检查网络连接"}
        except Exception as e:
            logger.error(f"获取分享文件列表失败: {e}")
            return {"error": str(e)}
    
    # 重试配置
    _RETRY_MAX = 10
    _RETRY_BASE_TIMEOUT = 30   # 首次超时秒数
    _RETRY_TIMEOUT_STEP = 15   # 每次递增秒数
    
    def get_share_children(self, surl: str, dir_path: str = "/", parent_child_count: int = 0) -> dict:
        """获取分享目录的直接子项（带超时重试，最多10次，线性递增）
        
        超时策略：30s → 45s → 60s → 75s → 90s → 105s → 120s → 135s → 150s → 165s
        根据父目录子项数跳过不必要的低超时尝试。
        
        Args:
            surl: 分享链接短码
            dir_path: 目录路径，"/"表示顶层
            parent_child_count: 父目录的子项数，用于确定起始超时
            
        Returns:
            {"list": [...], "path": dir_path} 或 {"error": "..."}
        """
        self._ensure_client()
        
        # 检查缓存
        cache_key = f"{surl}:{dir_path}"
        if cache_key in self._children_cache:
            logger.info(f"使用缓存的子项列表: {cache_key}")
            return {"list": self._children_cache[cache_key], "path": dir_path, "cached": True}
        
        # 根据父目录子项数确定起始 attempt
        if parent_child_count <= 30:
            start_attempt = 0   # 从 30s 开始
        elif parent_child_count <= 100:
            start_attempt = 1   # 从 45s 开始（跳过 30s）
        else:
            start_attempt = 2   # 从 60s 开始（跳过 30s、45s）
        
        params = {
            "app_id": self.APP_ID,
            "shorturl": surl,
            "dir": dir_path,
            "page": "1",
            "num": "100",
            "order": "time",
            "channel": "chunlei",
            "web": "1",
            "bdstoken": "",
            "logid": "",
            "clienttype": "0",
        }
        
        last_error = None
        for attempt in range(start_attempt, self._RETRY_MAX):
            timeout = self._RETRY_BASE_TIMEOUT + self._RETRY_TIMEOUT_STEP * attempt
            req_start = time.time()
            
            try:
                _global_limiter.acquire(timeout=30.0)
                resp = self.client.get(
                    self.SHARE_LIST_URL,
                    params=params,
                    headers=self._share_headers(),
                    follow_redirects=True,
                    timeout=timeout,
                )
                data = safe_json_parse(resp)
                
                if "error" in data:
                    # API 层错误（非网络），不重试
                    logger.warning(f"[RETRY] {dir_path} API错误(不重试): {data['error']}")
                    return data
                
                errno = data.get("errno", -1)
                if errno in (-62, -9):
                    _global_limiter.report_rate_limit(cooldown=60.0)
                    return {"error": "请求过于频繁，请稍后重试", "error_code": -62}
                if errno != 0:
                    error_msg = self._handle_error(errno, data.get("errmsg", ""))
                    return {"error": error_msg}
                
                # 成功
                items = data.get("list", [])
                for item in items:
                    item["isdir"] = int(item.get("isdir", 0))
                
                self._children_cache[cache_key] = items
                elapsed = time.time() - req_start
                logger.info(
                    f"[RETRY] {dir_path} 成功 | attempt={attempt+1}/{self._RETRY_MAX} "
                    f"timeout={timeout}s actual={elapsed:.1f}s items={len(items)} "
                    f"parent_children={parent_child_count}"
                )
                return {"list": items, "path": dir_path}
                
            except httpx.TimeoutException:
                elapsed = time.time() - req_start
                last_error = "请求超时"
                logger.warning(
                    f"[RETRY] {dir_path} 超时 | attempt={attempt+1}/{self._RETRY_MAX} "
                    f"timeout={timeout}s actual={elapsed:.1f}s "
                    f"parent_children={parent_child_count}"
                )
                continue  # 重试
                
            except Exception as e:
                import traceback
                elapsed = time.time() - req_start
                last_error = str(e)
                logger.error(
                    f"[RETRY] {dir_path} 异常 | attempt={attempt+1}/{self._RETRY_MAX} "
                    f"timeout={timeout}s actual={elapsed:.1f}s "
                    f"parent_children={parent_child_count}\n"
                    f"  异常类型: {type(e).__module__}.{type(e).__qualname__}\n"
                    f"  异常信息: {e}\n"
                    f"  限流器: {_global_limiter.get_stats()}\n"
                    f"  堆栈:\n{traceback.format_exc()}"
                )
                continue  # 重试
        
        # 10 次全部失败
        logger.error(
            f"[RETRY] {dir_path} 全部失败 | {self._RETRY_MAX}次尝试 "
            f"timeout范围={self._RETRY_BASE_TIMEOUT}~{self._RETRY_BASE_TIMEOUT + self._RETRY_TIMEOUT_STEP * (self._RETRY_MAX - 1)}s "
            f"parent_children={parent_child_count} last_error={last_error}"
        )
        return {"error": f"重试{self._RETRY_MAX}次后仍失败: {last_error}"}
    
    def collect_files_recursive(self, surl: str, dir_path: str = "/", max_depth: int = 15, collected: list = None, progress_callback=None, parent_child_count: int = 0) -> dict:
        """递归收集目录下所有文件（带缓存和限流）
        
        用于全量转存和目录选中转存：
        - 已展开的目录直接用缓存（0次API请求）
        - 未展开的目录逐层请求（每层1次API请求）
        - 使用 _global_limiter 控制频率
        
        Args:
            surl: 分享链接短码
            dir_path: 起始目录路径
            max_depth: 最大递归深度（防止死循环）
            collected: 内部递归用的文件收集列表
            progress_callback: 可选回调函数 callback(dirs_scanned, files_found, current_dir, api_requests)
            parent_child_count: 父目录的子项数，用于动态确定请求超时
            
        Returns:
            {"files": [...], "dirs_scanned": N, "api_requests": M}
        """
        if collected is None:
            collected = []
        
        # 用于跟踪累计值（通过列表引用传递给递归调用）
        if not hasattr(self, '_collect_stats'):
            self._collect_stats = {"dirs_scanned": 0, "api_requests": 0, "seq": 0, "last_req_time": time.time()}
        
        if max_depth <= 0:
            logger.warning(f"达到最大递归深度，停止遍历: {dir_path}")
            return {"files": collected, "dirs_scanned": self._collect_stats["dirs_scanned"], "api_requests": self._collect_stats["api_requests"]}
        
        # 获取当前目录的子项（优先用缓存）
        self._collect_stats["seq"] += 1
        seq = self._collect_stats["seq"]
        gap = time.time() - self._collect_stats["last_req_time"]
        logger.info(f"[DIAG] collect seq={seq} dir={dir_path} gap={gap:.2f}s parent_child_count={parent_child_count}")
        result = self.get_share_children(surl, dir_path, parent_child_count=parent_child_count)
        self._collect_stats["last_req_time"] = time.time()
        if "error" in result:
            logger.warning(f"获取子项失败: {dir_path} → {result['error']}")
            return {"files": collected, "dirs_scanned": self._collect_stats["dirs_scanned"], "api_requests": self._collect_stats["api_requests"], "error": result["error"]}
        
        items = result.get("list", [])
        cached = result.get("cached", False)
        if not cached:
            self._collect_stats["api_requests"] += 1
        self._collect_stats["dirs_scanned"] += 1
        
        # 分离文件和目录
        files = []
        subdirs = []
        for item in items:
            isdir = int(item.get("isdir", 0))
            if isdir == 1:
                subdirs.append(item)
            else:
                files.append(item)
        
        collected.extend(files)
        
        # 实时回调进度
        if progress_callback:
            progress_callback(
                self._collect_stats["dirs_scanned"],
                len(collected),
                dir_path,
                self._collect_stats["api_requests"]
            )
        
        # 递归处理子目录
        for subdir in subdirs:
            sub_path = subdir.get("path", "")
            if not sub_path:
                continue
            sub_result = self.collect_files_recursive(surl, sub_path, max_depth - 1, collected, progress_callback, parent_child_count=len(items))
            # 如果子目录遇到限流错误，停止遍历
            if sub_result.get("error"):
                return {"files": collected, "dirs_scanned": self._collect_stats["dirs_scanned"], "api_requests": self._collect_stats["api_requests"], "error": sub_result["error"]}
        
        return {"files": collected, "dirs_scanned": self._collect_stats["dirs_scanned"], "api_requests": self._collect_stats["api_requests"]}
    
    def collect_files_start(self):
        """重置收集统计（在调用 collect_files_recursive 前调用）"""
        self._collect_stats = {"dirs_scanned": 0, "api_requests": 0, "seq": 0, "last_req_time": time.time()}
    
    def collect_files_batch(self, surl: str, batch_size: int = 100, root_dir: str = "/"):
        """流式收集：BFS 逐目录扫描，每攒够 batch_size 个文件就 yield 一批
        
        收集一批 → 转存 → 收集下一批 → 转存 ...
        BDCLND 不会过期（每批收集耗时远低于 15 分钟过期窗口）
        
        Args:
            surl: 分享链接短码
            batch_size: 每批文件数（默认100，与转存API上限对齐）
            root_dir: 起始目录
            
        Yields:
            dict: {
                "files": list,             # 本批文件列表（最多 batch_size 个）
                "dirs_scanned": int,       # 已扫描目录总数
                "files_found": int,        # 已发现文件总数
                "api_requests": int,       # 已消耗API请求数
                "batch_num": int,          # 第几批（从1开始）
                "error": str|None,         # 错误信息（仅最后一批可能有）
            }
        """
        self.collect_files_start()
        
        # BFS 队列：(dir_path, parent_child_count)
        queue = collections.deque()
        queue.append((root_dir, 0))
        total_files = 0
        batch_num = 0
        buffer = []  # 攒文件的缓冲区
        
        while queue:
            dir_path, parent_child_count = queue.popleft()
            
            self._collect_stats["seq"] += 1
            seq = self._collect_stats["seq"]
            gap = time.time() - self._collect_stats["last_req_time"]
            logger.info(f"[BFS] seq={seq} dir={dir_path} queue={len(queue)} buffer={len(buffer)} gap={gap:.2f}s")
            
            result = self.get_share_children(surl, dir_path, parent_child_count=parent_child_count)
            self._collect_stats["last_req_time"] = time.time()
            
            if "error" in result:
                logger.warning(f"[BFS] 获取子项失败: {dir_path} → {result['error']}")
                # 把缓冲区剩余文件作为最后一批 yield
                if buffer:
                    batch_num += 1
                    yield {
                        "files": buffer,
                        "dirs_scanned": self._collect_stats["dirs_scanned"],
                        "files_found": total_files,
                        "api_requests": self._collect_stats["api_requests"],
                        "batch_num": batch_num,
                        "error": None,
                    }
                    buffer = []
                # yield 错误批次
                batch_num += 1
                yield {
                    "files": [],
                    "dirs_scanned": self._collect_stats["dirs_scanned"],
                    "files_found": total_files,
                    "api_requests": self._collect_stats["api_requests"],
                    "batch_num": batch_num,
                    "error": result["error"],
                }
                return
            
            items = result.get("list", [])
            cached = result.get("cached", False)
            if not cached:
                self._collect_stats["api_requests"] += 1
            self._collect_stats["dirs_scanned"] += 1
            
            # 分离文件和子目录
            for item in items:
                if int(item.get("isdir", 0)) == 1:
                    sub_path = item.get("path", "")
                    if sub_path:
                        queue.append((sub_path, len(items)))
                else:
                    buffer.append(item)
                    total_files += 1
            
            # 缓冲区满 → yield 一批
            while len(buffer) >= batch_size:
                batch_num += 1
                yield {
                    "files": buffer[:batch_size],
                    "dirs_scanned": self._collect_stats["dirs_scanned"],
                    "files_found": total_files,
                    "api_requests": self._collect_stats["api_requests"],
                    "batch_num": batch_num,
                    "error": None,
                }
                buffer = buffer[batch_size:]
        
        # BFS 结束，yield 缓冲区剩余文件
        if buffer:
            batch_num += 1
            yield {
                "files": buffer,
                "dirs_scanned": self._collect_stats["dirs_scanned"],
                "files_found": total_files,
                "api_requests": self._collect_stats["api_requests"],
                "batch_num": batch_num,
                "error": None,
            }
        
        # 空目录情况
        if batch_num == 0:
            yield {
                "files": [],
                "dirs_scanned": self._collect_stats["dirs_scanned"],
                "files_found": 0,
                "api_requests": self._collect_stats["api_requests"],
                "batch_num": 1,
                "error": None,
            }
    
    def get_all_share_files(self, surl: str, dir_path: str = "/", depth: int = 0) -> List[dict]:
        """递归获取所有分享文件（同步包装器）"""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._async_get_all_files(surl))
        finally:
            loop.close()
    
    async def _async_get_all_files(self, surl: str) -> List[dict]:
        """异步队列遍历 — 5 个 worker，per-request 限流处理"""
        all_files = []
        file_lock = asyncio.Lock()
        stats = {"requests": 0, "dirs_scanned": 0}
        stats_lock = asyncio.Lock()
        sem = asyncio.Semaphore(self._concurrency)
        queue = asyncio.Queue()
        queue.put_nowait(("/", 0))  # (path, retry_count)
        pending = 1
        pending_lock = asyncio.Lock()
        
        async def fetch_dir(client, dir_path):
            """获取单个目录，返回 (files, subdirs, rate_limited)"""
            local_files, local_subdirs = [], []
            page = 1
            while True:
                async with stats_lock:
                    if stats["requests"] >= self._max_requests:
                        return local_files, local_subdirs, False
                    stats["requests"] += 1
                
                # 限流器控制
                await asyncio.to_thread(_global_limiter.acquire, timeout=30.0)
                
                params = {
                    "app_id": self.APP_ID,
                    "shorturl": surl,
                    "dir": dir_path,
                    "page": str(page),
                    "num": "100",
                    "order": "time",
                    "channel": "chunlei",
                    "web": "1",
                    "bdstoken": "",
                    "logid": "",
                    "clienttype": "0",
                }
                
                try:
                    resp = await client.get(
                        self.SHARE_LIST_URL, params=params,
                        headers=self._share_headers(), timeout=30.0
                    )
                    data = safe_json_parse(resp)
                    
                    if "error" in data:
                        return local_files, local_subdirs, False
                    
                    errno = data.get("errno", 0)
                    if errno in (-62, -9):
                        _global_limiter.report_rate_limit(cooldown=60.0)
                        return local_files, local_subdirs, True  # 被限流
                    elif errno != 0:
                        return local_files, local_subdirs, False
                    
                    file_list = data.get("list", [])
                    if not file_list:
                        break
                    
                    for item in file_list:
                        if int(item.get("isdir", 0)) == 1:
                            local_subdirs.append(item.get("path", ""))
                        else:
                            local_files.append(item)
                    
                    # 检查是否有更多页
                    if len(file_list) < 100:
                        break
                    page += 1
                    
                except Exception as e:
                    logger.error(f"获取目录失败: {dir_path}, 错误: {e}")
                    return local_files, local_subdirs, False
            
            return local_files, local_subdirs, False
        
        async def worker(client):
            nonlocal pending
            while True:
                try:
                    dir_path, retries = await asyncio.wait_for(queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    async with pending_lock:
                        if pending <= 0 and queue.empty():
                            return
                    continue
                
                if self._traversal_cancelled:
                    return
                
                # 获取信号量后执行请求
                async with sem:
                    files, subdirs, rate_limited = await fetch_dir(client, dir_path)
                
                if rate_limited and retries < 5:
                    # 被限流 — 在信号量外等待，不阻塞其他worker
                    backoff = min(1.0 * (2 ** retries), 10.0)  # 1,2,4,8,10s
                    await asyncio.sleep(backoff)
                    queue.put_nowait((dir_path, retries + 1))
                    async with pending_lock:
                        pending += 1  # 重试的目录重新计入pending
                else:
                    # 成功或放弃 — 记录结果
                    async with file_lock:
                        all_files.extend(files)
                        self._total_files_found += len(files)
                    async with stats_lock:
                        stats["dirs_scanned"] += 1
                        if self._progress_callback:
                            self._progress_callback(
                                stats["dirs_scanned"], self._total_files_found,
                                f"扫描: {dir_path}"
                            )
                    
                    # 子目录入队
                    for subdir in subdirs:
                        queue.put_nowait((subdir, 0))
                        async with pending_lock:
                            pending += 1
                
                async with pending_lock:
                    pending -= 1
                    queue.task_done()
        
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)
        async with httpx.AsyncClient(headers=self.headers, timeout=30.0,
                                     limits=limits, follow_redirects=True) as client:
            workers = [asyncio.create_task(worker(client)) for _ in range(self._concurrency)]
            await asyncio.gather(*workers, return_exceptions=True)
        
        return all_files
    
    def create_dir(self, path: str) -> dict:
        """创建目录"""
        self._ensure_client()
        try:
            _global_limiter.acquire(timeout=30.0)
            bdstoken = self._get_bdstoken()
            url = self.CREATE_DIR_URL
            data = {"path": path, "isdir": "1", "block_list": "[]"}
            params = {"a": "commit", "bdstoken": bdstoken, "app_id": self.APP_ID}
            # ⚠️ 必须使用 _share_headers() 包含 BDCLND
            headers = self._share_headers()
            logger.info(f"[create_dir] 请求: path={path}, bdstoken={bdstoken[:8]}..., BDCLND={self.bdclnd[:20] if self.bdclnd else '空'}...")
            resp = self.client.post(url, params=params, data=data, headers=headers)
            result = safe_json_parse(resp)
            
            logger.info(f"[create_dir] 响应: path={path}, errno={result.get('errno')}, errmsg={result.get('errmsg', '')}")
            
            if "error" in result:
                return result
            
            errno = result.get("errno", 0)
            if errno in (-62, -9):
                _global_limiter.report_rate_limit(cooldown=60.0)
                return {"error": "请求过于频繁，请等待几分钟后重试", "error_code": -62}
            elif errno == 9019:
                return {"error": "需要验证（BDCLND缺失）", "error_code": 9019}
            elif errno != 0:
                error_msg = self._handle_error(errno, result.get("errmsg", ""))
                return {"error": error_msg}
            
            return {"success": True, "path": path}
            
        except httpx.TimeoutException:
            return {"error": "请求超时，请检查网络连接"}
        except Exception as e:
            logger.error(f"创建目录失败: {e}")
            return {"error": str(e)}
    
    def transfer_files(self, share_id: str, uk: str, file_paths: List, target_path: str, pwd: str = "") -> dict:
        """批量转存文件"""
        self._ensure_client()
        
        # 获取 bdstoken（CSRF token，转存 API 必需）
        bdstoken = self._get_bdstoken()
        
        # 确保目标目录存在
        dir_result = self.create_dir(target_path)
        if "error" in dir_result and dir_result.get("error_code") != -7:  # -7 表示目录已存在
            logger.warning(f"创建目标目录失败: {dir_result}")
            # 继续尝试转存，可能目录已存在
        
        url = self.TRANSFER_URL
        params = {
            "app_id": self.APP_ID,
            "shareid": share_id,
            "from": uk,
            "from_type": "1",
            "channel": "chunlei",
            "web": "1",
            "bdstoken": bdstoken,
            "logid": "",
            "clienttype": "0"
        }
        
        data = {
            "filelist": json.dumps(file_paths),
            "path": target_path,
            "ondup": "newcopy"
        }
        
        if pwd:
            data["pwd"] = pwd
        
        # 限流重试：最多 3 次，指数退避
        for attempt in range(3):
            try:
                # 全局速率控制
                _global_limiter.acquire(timeout=60.0)
                
                headers = self._share_headers()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                logger.info(f"转存请求: url={url}, filelist类型={type(file_paths).__name__}, filelist长度={len(file_paths)}, filelist示例={file_paths[:2]}, path={target_path}")
                resp = self.client.post(url, params=params, data=data, headers=headers)
                result = safe_json_parse(resp)
                logger.info(f"转存响应: status={resp.status_code}, errno={result.get('errno')}, errmsg={result.get('errmsg', '')}, 完整响应={result}")
                
                if "error" in result:
                    return {"success": False, "error": result["error"]}
                
                errno = int(result.get("errno", -1))
                
                # 限流错误 → 立即返回，不重试（重试会加重限流）
                if errno in (-62, -9):
                    _global_limiter.report_rate_limit(cooldown=60.0)
                    logger.warning(f"转存被限流(errno={errno})，立即返回，等待用户稍后重试")
                    return {"success": False, "error": "请求过于频繁，请等待几分钟后重试", "errno": errno}
                
                if errno == 0:
                    logger.info(f"成功转存 {len(file_paths)} 个文件(fs_id)到 {target_path}")
                    return {
                        "success": True,
                        "task_id": result.get("task_id", ""),
                        "extra": result.get("extra", {})
                    }
                else:
                    error_msg = self._handle_error(errno, result.get("errmsg", ""))
                    logger.warning(f"转存失败: {error_msg} (errno={errno})")
                    return {
                        "success": False,
                        "error": error_msg,
                        "errno": errno
                    }
            except httpx.TimeoutException:
                if attempt < 2:
                    logger.warning(f"转存超时，第{attempt+1}次重试")
                    time.sleep(3)
                    continue
                return {"success": False, "error": "请求超时，请检查网络连接"}
            except Exception as e:
                logger.error(f"转存异常: {e}")
                return {"success": False, "error": str(e)}
        
        return {"success": False, "error": "请求过于频繁，多次重试后仍失败"}
    
    def transfer_files_with_fallback(self, share_id: str, uk: str, file_list: List[dict], target_path: str, pwd: str = "") -> dict:
        """转存文件（自动 fallback：fs_id → path）
        
        file_list: [{"path": "/xxx", "fs_id": 123}, {"path": "/yyy", "fs_id": None}, ...]
        """
        # 先尝试 fs_id（过滤掉 None）
        file_ids = [int(f["fs_id"]) for f in file_list if f.get("fs_id")]
        if file_ids:
            logger.info(f"尝试 fs_id 格式转存: {len(file_ids)} 个文件")
            result = self.transfer_files(share_id, uk, file_ids, target_path, pwd)
            if result.get("success"):
                return result
            if result.get("errno") not in (2,):
                return result  # 非格式错误，直接返回
            logger.info(f"fs_id 格式失败(errno={result.get('errno')}), 尝试 path 格式")
        
        # 降级到 path 格式
        file_paths = [f["path"] for f in file_list if f.get("path")]
        if not file_paths:
            return {"success": False, "error": "无有效文件路径"}
        logger.info(f"尝试 path 格式转存: {len(file_paths)} 个文件")
        return self.transfer_files(share_id, uk, file_paths, target_path, pwd)
    
    def check_file_exists(self, path: str) -> bool:
        """检查文件是否存在"""
        self._ensure_client()
        try:
            _global_limiter.acquire(timeout=30.0)
            parent_dir = "/".join(path.split("/")[:-1]) or "/"
            resp = self.client.get(self.LIST_URL, params={"dir": parent_dir})
            data = safe_json_parse(resp)
            
            if "error" in data:
                return False
            
            if data.get("errno", -1) != 0:
                return False
            
            file_list = data.get("list", [])
            filename = path.split("/")[-1]
            return any(f.get("server_filename") == filename for f in file_list)
            
        except Exception as e:
            logger.error(f"检查文件存在失败: {e}")
            return False
    
    def close(self):
        """关闭 HTTP client"""
        if self.client and not self.client.is_closed:
            self.client.close()


class BatchTransferManager:
    """批量转存管理器"""
    
    def __init__(self, api: BaiduPanAPI, share_id: str, uk: str, files: List[dict], target_path: str, pwd: str = ""):
        self.api = api
        self.share_id = share_id
        self.uk = uk
        self.files = files
        self.target_path = target_path
        self.pwd = pwd
        self.task_progress = {
            "status": "ready",
            "total": len(files),
            "completed": 0,
            "failed": 0,
            "current_batch": 0,
            "total_batches": (len(files) + 499) // 500,
            "start_time": None,
            "elapsed": 0,
            "speed": 0,
            "errors": []
        }
    
    def execute_transfer(self, batch_size: int = 500, batch_interval: float = 5.0) -> dict:
        """执行批量转存"""
        if self.task_progress["status"] != "ready":
            return {"error": "任务未就绪"}
        
        self.task_progress["status"] = "running"
        self.task_progress["start_time"] = time.time()
        
        total_files = len(self.files)
        completed = 0
        failed = 0
        errors = []
        
        try:
            # 分批处理
            for i in range(0, total_files, batch_size):
                if self.api._traversal_cancelled:
                    self.task_progress["status"] = "cancelled"
                    return {"success": False, "error": "任务已取消"}
                
                batch = self.files[i:i + batch_size]
                batch_num = i // batch_size + 1
                self.task_progress["current_batch"] = batch_num
                
                logger.info(f"开始处理批次 {batch_num}/{self.task_progress['total_batches']}, 文件数: {len(batch)}")
                
                # 尝试 fs_id，失败则 fallback 到 path
                file_ids = [f.get("fs_id") for f in batch]
                result = self.api.transfer_files(self.share_id, self.uk, file_ids, self.target_path, self.pwd)
                
                if result.get("success"):
                    completed += len(batch)
                    logger.info(f"批次 {batch_num} 成功: {len(batch)} 个文件")
                elif result.get("errno") == 2:
                    # fs_id 失败，尝试 path
                    logger.info(f"批次 {batch_num} fs_id 失败，尝试 path 格式")
                    file_paths = [f.get("path") for f in batch]
                    result = self.api.transfer_files(self.share_id, self.uk, file_paths, self.target_path, self.pwd)
                    
                    if result.get("success"):
                        completed += len(batch)
                        logger.info(f"批次 {batch_num} path 格式成功: {len(batch)} 个文件")
                    else:
                        failed += len(batch)
                        error_msg = result.get("error", "未知错误")
                        errors.append(f"批次 {batch_num}: {error_msg}")
                        logger.warning(f"批次 {batch_num} 失败: {error_msg}")
                elif result.get("errno") in (-62, -9):
                    # 限流 — 立即停止
                    failed += len(batch)
                    errors.append(f"批次 {batch_num}: 请求过于频繁，已停止")
                    logger.warning(f"批次 {batch_num} 被限流，停止转存")
                    break
                else:
                    failed += len(batch)
                    error_msg = result.get("error", "未知错误")
                    errors.append(f"批次 {batch_num}: {error_msg}")
                    logger.warning(f"批次 {batch_num} 失败: {error_msg}")
                
                # 更新进度
                self.task_progress["completed"] = completed
                self.task_progress["failed"] = failed
                self.task_progress["errors"] = errors
                
                # 计算速度和耗时
                elapsed = time.time() - self.task_progress["start_time"]
                self.task_progress["elapsed"] = round(elapsed, 1)
                if elapsed > 0:
                    self.task_progress["speed"] = round(completed / elapsed, 1)
                
                # 批次间等待
                if i + batch_size < total_files:
                    time.sleep(batch_interval)
            
            # 完成
            self.task_progress["status"] = "completed"
            logger.info(f"转存完成: 成功 {completed}, 失败 {failed}")
            
            return {
                "success": True,
                "completed": completed,
                "failed": failed,
                "errors": errors
            }
            
        except Exception as e:
            self.task_progress["status"] = "error"
            logger.error(f"转存异常: {e}")
            return {"success": False, "error": str(e)}
    
    def get_progress(self) -> dict:
        """获取进度信息"""
        # 实时计算耗时
        start = self.task_progress.get("start_time")
        if start and self.task_progress.get("status") == "running":
            self.task_progress["elapsed"] = round(time.time() - start, 1)
            if self.task_progress["elapsed"] > 0:
                self.task_progress["speed"] = round(
                    self.task_progress["completed"] / self.task_progress["elapsed"], 1
                )
        return self.task_progress
    
    def cancel(self):
        """取消任务"""
        self.api._traversal_cancelled = True
        self.task_progress["status"] = "cancelled"
