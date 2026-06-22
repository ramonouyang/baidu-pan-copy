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

# 添加文件输出（诊断用）
_file_handler = logging.FileHandler('baidu_api.log', encoding='utf-8')
_file_handler.setLevel(logging.INFO)
_file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(_file_handler)


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
    BUDGET_LIMIT = 120     # DTS-2026-009: 每窗口最多 120 次请求（原 80，提升 50%）
    
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
# DTS-2026-009: 提升吞吐量 — rate 1.5→2.5, burst 2→4
_global_limiter = RateLimiter(rate=2.5, burst=4)

# DTS-2026-011: Debug 模式控制
def set_debug_mode(enabled: bool):
    """切换日志级别：True=DEBUG, False=INFO"""
    level = logging.DEBUG if enabled else logging.INFO
    logger.setLevel(level)
    logger.info("日志级别切换为: %s", "DEBUG" if enabled else "INFO")


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
    # ✅ 修复：使用正确的端点 /share/transfer
    # 开源项目（BaiduPCS-Go, baidupcsapi, BaiduPCS-Py）都使用这个端点
    CREATE_DIR_URL = f"{BASE_URL}/api/create"
    FILE_METAS_URL = f"{BASE_URL}/rest/2.0/xpan/multimedia?method=filemetas"
    UINFO_URL = f"{BASE_URL}/rest/2.0/xpan/nas?method=uinfo"
    
    # 错误码映射
    ERROR_CODES = {
        0: "成功",
        2: "文件名无效或包含非法字符",
        4: "请求超时，请稍后再试",  # DTS2026062282633：百度临时超时，可重试
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
        200025: "提取码输入错误，请重试",  # 新增：百度网盘新错误码
    }
    
    def __init__(self, cookie: str):
        self.cookie = cookie
        self.bdclnd = ""  # Set after share verify
        # ✅ DTS2026062143821：对齐 BaiduPCS-Py，headers 不含 Cookie
        # Cookie 由 httpx.Client cookie jar 自动管理（如 requests.Session）
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://pan.baidu.com/"
        }
        self.client = httpx.Client(headers=self.headers, timeout=30.0)
        # 把初始 cookie 存入 client cookie jar
        self._sync_cookies_to_jar()
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
    
    def _sync_cookies_to_jar(self):
        """把 self.cookie 中的 cookie 同步到 httpx client cookie jar
    
        对齐 BaiduPCS-Py 的 _cookies_update()，确保 client 自动携带所有 cookie
        """
        import re as _re
        for part in self.cookie.split(";"):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                name = name.strip()
                value = value.strip()
                if name:
                    self.client.cookies.set(name, value, domain=".baidu.com", path="/")
        # ✅ DTS2026062143821：BDCLND 也必须同步到 cookie jar（client 重建时必须保留）
        if self.bdclnd:
            self._set_bdclnd_cookie(self.bdclnd)
    
    def _set_bdclnd_cookie(self, value: str):
        """安全设置 BDCLND cookie，先清除旧的避免重复"""
        self.client.cookies.delete("BDCLND")
        self.client.cookies.set("BDCLND", value, domain=".baidu.com", path="/")
    
    def _ensure_client(self):
        """确保 httpx client 可用，如果已关闭则重新创建"""
        if self.client.is_closed:
            logger.info("HTTP client 已关闭，重新创建")
            self.client = httpx.Client(headers=self.headers, timeout=30.0)
            # ✅ DTS2026062143821：重建后同步 cookie 到 jar
            self._sync_cookies_to_jar()
    
    def _share_headers(self) -> dict:
        """返回包含 BDCLND cookie 的请求头"""
        h = self.headers.copy()
        # Cookie 始终包含基础 cookie（BDUSS、STOKEN 等）
        cookie_val = self.cookie
        if self.bdclnd:
            cookie_val = f"{self.cookie}; BDCLND={self.bdclnd}"
        h["Cookie"] = cookie_val
        return h
    
    def _ensure_baiduid(self, force_refresh=False):
        """确保 cookie 中包含有效的 BAIDUID（bdstoken 接口必需，缺少或过期会导致 errno=-6）

        如果 self.cookie 中缺少 BAIDUID，或 force_refresh=True，发请求到 pan.baidu.com，
        从响应的 Set-Cookie 中提取最新的 BAIDUID 并固化到 self.cookie / self.headers，
        后续所有请求都会自动携带。
        """
        if not force_refresh and "BAIDUID=" in self.cookie:
            return
        try:
            self._ensure_client()
            _global_limiter.acquire(timeout=10.0)
            resp = self.client.get(
                "https://pan.baidu.com/disk/main",
                headers=self.headers,
                follow_redirects=True,
            )
            baiduid = resp.cookies.get("BAIDUID", "")
            if not baiduid:
                # 有时 BAIDUID 在 Set-Cookie 中但 httpx jar 未捕获，手动从 header 解析
                for sc in resp.headers.get_list("set-cookie"):
                    if sc.startswith("BAIDUID="):
                        baiduid = sc.split("=", 1)[1].split(";")[0]
                        break
            if baiduid:
                # 替换或追加 BAIDUID 到 self.cookie
                if "BAIDUID=" in self.cookie:
                    import re
                    self.cookie = re.sub(r'BAIDUID=[^;]+', f'BAIDUID={baiduid}', self.cookie)
                else:
                    self.cookie = f"{self.cookie}; BAIDUID={baiduid}"
                # ✅ DTS2026062143821：同步到 cookie jar，不再手动设 headers Cookie
                self._sync_cookies_to_jar()
                logger.debug("[DIAG-COOKIE] %s BAIDUID: %s...", '刷新' if force_refresh else '获取', baiduid[:8])
            else:
                logger.warning("[DIAG-COOKIE] 未能从响应中获取 BAIDUID，bdstoken 可能失败 (errno=-6)")
        except Exception as e:
            logger.warning(f"[DIAG-COOKIE] 获取 BAIDUID 异常: {e}")

    def _get_bdstoken(self) -> str:
        """获取 bdstoken（CSRF token），转存 API 必需"""
        if self._bdstoken_cache:
            # DTS2026061793850 — 补充 bdstoken 缓存命中日志
            logger.info(f"[bdstoken] 缓存命中: {self._bdstoken_cache[:8]}...")
            return self._bdstoken_cache
        try:
            self._ensure_client()
            # DTS2026061989160 — 确保 BAIDUID 存在，缺少会导致 errno=-6
            self._ensure_baiduid()
            _global_limiter.acquire(timeout=10.0)
            # DTS2026061989160 — 添加 clienttype/app_id/web 参数，缺少会导致 errno=-6
            resp = self.client.get(
                "https://pan.baidu.com/api/gettemplatevariable",
                params={
                    "clienttype": "0",
                    "app_id": "38824127",
                    "web": "1",
                    "fields": '["bdstoken","token","uk","isdocuser","servertime"]'
                },
                headers=self.headers,
            )
            data = safe_json_parse(resp)
            # DTS2026061948271 — 添加类型检查，防止 data 是 list 而非 dict
            if not isinstance(data, dict):
                logger.warning(f"获取 bdstoken 失败: 响应不是 dict 类型: {type(data)}, data={str(data)[:200]}")
                return ""
            
            # DTS2026061989160 — errno=-6 时从响应 Set-Cookie 提取新 BAIDUID 并重试
            if data.get("errno") == -6:
                # DTS2026061989160 — 添加详细诊断日志
                logger.warning(f"获取 bdstoken 失败: errno=-6, 完整响应: {data}")
                logger.warning(f"响应 headers: {dict(resp.headers)}")
                logger.warning(f"请求 Cookie: {self.headers.get('Cookie', '')[:200]}")
                
                new_baiduid = resp.cookies.get("BAIDUID", "")
                if not new_baiduid:
                    for sc in resp.headers.get_list("set-cookie"):
                        if sc.startswith("BAIDUID="):
                            new_baiduid = sc.split("=", 1)[1].split(";")[0]
                            break
                if new_baiduid:
                    logger.warning(f"获取 bdstoken 失败: errno=-6，从响应提取新 BAIDUID: {new_baiduid[:8]}...，重试")
                    import re
                    if "BAIDUID=" in self.cookie:
                        self.cookie = re.sub(r'BAIDUID=[^;]+', f'BAIDUID={new_baiduid}', self.cookie)
                    else:
                        self.cookie = f"{self.cookie}; BAIDUID={new_baiduid}"
                    # ✅ DTS2026062143821：同步到 cookie jar
                    self._sync_cookies_to_jar()
                    _global_limiter.acquire(timeout=10.0)
                    # DTS2026061989160 — 重试也需要完整参数
                    resp = self.client.get(
                        "https://pan.baidu.com/api/gettemplatevariable",
                        params={
                            "clienttype": "0",
                            "app_id": "38824127",
                            "web": "1",
                            "fields": '["bdstoken","token","uk","isdocuser","servertime"]'
                        },
                        headers=self.headers,
                    )
                    data = safe_json_parse(resp)
                    if not isinstance(data, dict):
                        logger.warning(f"刷新后重试仍失败: 响应不是 dict: {type(data)}")
                        return ""
                else:
                    logger.warning(f"获取 bdstoken 失败: errno=-6 且响应中无新 BAIDUID")
            
            result = data.get("result", {})
            if not isinstance(result, dict):
                logger.warning(f"获取 bdstoken 失败: result 不是 dict 类型: {type(result)}, data={data}")
                return ""
            token = result.get("bdstoken", "")
            if token:
                self._bdstoken_cache = token
                logger.info(f"获取 bdstoken 成功: {token[:8]}...")
            else:
                logger.warning(f"获取 bdstoken 失败: errno={data.get('errno')}, data={data}")
            return token
        except Exception as e:
            logger.warning(f"获取 bdstoken 异常: {e}")
            return ""
    
    def _get_share_page_tokens(self, share_link: str, pwd: str = "") -> dict:
        """访问分享页获取 tokens（bdstoken, uk, shareid, bdstoken）
        
        根据开源项目（BaiduPCS-Go, baidupcsapi, BaiduPCS-Py）的实现，
        转存前必须先访问分享页获取 tokens，否则会返回 errno=-3 或 404。
        
        Args:
            share_link: 分享链接
            pwd: 提取码（如果有）
            
        Returns:
            dict: {
                "bdstoken": str,
                "uk": str,
                "shareid": str,
                "bdstoken": str,
                "success": bool,
                "error": str
            }
        """
        self._ensure_client()
        surl = self._extract_surl(share_link)
        if not surl:
            return {"success": False, "error": "无效的分享链接"}
        
        try:
            # Step 1: 访问分享页，获取初始 tokens
            _global_limiter.acquire(timeout=30.0)
            share_url = f"https://pan.baidu.com/s/{surl}"
            logger.info(f"[share-page] 访问分享页: {share_url}")
            
            resp = self.client.get(
                share_url,
                headers=self.headers,
                follow_redirects=True,
                timeout=30.0
            )
            
            # 从响应中提取 tokens
            # 开源项目通常从 HTML 页面中提取，但我们可以从 Set-Cookie 和响应头获取
            bdstoken = ""
            uk = ""
            shareid = ""
            
            # 尝试从 Set-Cookie 提取
            for cookie in resp.headers.get_list("set-cookie"):
                if cookie.startswith("BDUSS="):
                    # 提取 BDUSS
                    pass
                elif cookie.startswith("STOKEN="):
                    # 提取 STOKEN
                    pass
                elif cookie.startswith("BAIDUID="):
                    # 提取 BAIDUID
                    pass
            
            # 尝试从响应内容提取（如果需要）
            # 开源项目通常解析 HTML 页面，但我们先尝试简单方式
            
            # 获取 bdstoken（从 gettemplatevariable 接口）
            bdstoken = self._get_bdstoken()
            
            # 获取 uk 和 shareid（从 share/list 接口）
            if pwd:
                # 先验证提取码
                verify_result = self._verify_share(surl, pwd)
                if not verify_result.get("success"):
                    return {"success": False, "error": f"验证提取码失败: {verify_result.get('error')}"}
            
            # 获取文件列表（同时获取 uk 和 shareid）
            list_result = self.get_share_file_list(surl)
            if "error" in list_result:
                return {"success": False, "error": f"获取文件列表失败: {list_result['error']}"}
            
            uk = list_result.get("uk", "")
            shareid = list_result.get("share_id", "")
            
            if not uk or not shareid:
                return {"success": False, "error": f"无法获取 uk 或 shareid: uk={uk}, shareid={shareid}"}
            
            logger.info(f"[share-page] 获取 tokens 成功: bdstoken={bdstoken[:8]}..., uk={uk}, shareid={shareid}")
            
            return {
                "success": True,
                "bdstoken": bdstoken,
                "uk": uk,
                "shareid": shareid,
                "surl": surl
            }
            
        except Exception as e:
            logger.error(f"[share-page] 获取 tokens 失败: {e}")
            return {"success": False, "error": str(e)}
    
    def _verify_share(self, surl: str, pwd: str) -> dict:
        """验证分享链接的提取码
        
        Args:
            surl: 分享链接短码
            pwd: 提取码
            
        Returns:
            dict: {"success": bool, "error": str}
        """
        self._ensure_client()
        try:
            _global_limiter.acquire(timeout=30.0)
            verify_params = {
                "app_id": self.APP_ID,
                "surl": surl,
                "channel": "chunlei",
                "web": "1",
                "bdstoken": "",
                "logid": "",
                "clienttype": "0",
                "t": str(int(time.time() * 1000))
            }
            verify_resp = self.client.post(
                self.SHARE_VERIFY_URL,
                params=verify_params,
                data={"pwd": pwd},
                headers=self.headers,
                follow_redirects=True,
                timeout=30.0
            )
            verify_data = safe_json_parse(verify_resp)
            
            if "error" in verify_data:
                return {"success": False, "error": verify_data["error"]}
            
            errno = verify_data.get("errno", 0)
            if errno == -21:
                return {"success": False, "error": "提取码错误"}
            elif errno == -19:
                return {"success": False, "error": "分享链接已失效"}
            elif errno == -20:
                return {"success": False, "error": "分享链接已过期"}
            elif errno == -12:
                return {"success": False, "error": "需要验证码"}
            elif errno in (-62, -9):
                return {"success": False, "error": "请求过于频繁"}
            elif errno != 0:
                return {"success": False, "error": f"验证失败: errno={errno}"}
            
            # 提取 BDCLND cookie
            self.bdclnd = verify_resp.cookies.get("BDCLND", "")
            if self.bdclnd:
                self._bdclnd_cache[surl] = self.bdclnd
                logger.info(f"[verify] 获取 BDCLND 成功: {self.bdclnd[:20]}...")
            
            return {"success": True}
            
        except Exception as e:
            logger.error(f"[verify] 验证失败: {e}")
            return {"success": False, "error": str(e)}
    
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
                # ✅ DTS2026062143821：缓存命中也必须同步到 cookie jar，否则转存会 errno=200025
                if self.bdclnd:
                    self._set_bdclnd_cookie(self.bdclnd)
                logger.info(f"[BDCLND] 使用缓存: surl={surl}, bdclnd={self.bdclnd[:20]}..., 已同步到cookie jar")
            else:
                reason = "force_refresh" if force_refresh else "缓存未命中"
                old_bdclnd = self._bdclnd_cache.get(surl, "")
                logger.info(f"[BDCLND] 重新verify: surl={surl}, 原因={reason}, 旧值={old_bdclnd[:20] if old_bdclnd else '无'}...")
                
                # verify 重试逻辑（最多3次，递增超时，应对SSL EOF/超时）
                verify_resp = None
                verify_data = None
                _VERIFY_RETRIES = 3
                _VERIFY_BASE_TIMEOUT = 30
                for _verify_attempt in range(_VERIFY_RETRIES):
                    _verify_timeout = _VERIFY_BASE_TIMEOUT + _verify_attempt * 15
                    try:
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
                            follow_redirects=True,
                            timeout=float(_verify_timeout)
                        )
                        verify_data = safe_json_parse(verify_resp)
                        break  # 成功，跳出重试
                    except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ConnectError) as _verify_exc:
                        logger.warning(f"[BDCLND] verify 第{_verify_attempt+1}次失败: {type(_verify_exc).__name__}: {_verify_exc}")
                        if _verify_attempt < _VERIFY_RETRIES - 1:
                            # 重建 client 以清除可能的坏连接
                            self.client.close()
                            self.client = httpx.Client(headers=self.headers, timeout=float(_verify_timeout))
                            import time as _sleep_time
                            _sleep_time.sleep(1 + _verify_attempt)
                        else:
                            raise
                if verify_resp is None or verify_data is None:
                    return {"error": "verify请求失败，请重试"}
                
                # 提取 BDCLND cookie
                self.bdclnd = verify_resp.cookies.get("BDCLND", "")
                # ✅ DTS2026062143821：BDCLND 必须存入 cookie jar
                # BaiduPCS-Py 用 _cookies_update(resp.cookies.get_dict()) 自动管理
                # self.client.post() 会自动携带 cookie jar 中的所有 cookie
                if self.bdclnd:
                    self._set_bdclnd_cookie(self.bdclnd)
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
            
            # Step 2: 获取文件列表（带重试）
            list_resp = None
            list_data = None
            _LIST_RETRIES = 3
            for _list_attempt in range(_LIST_RETRIES):
                _list_timeout = 30.0 + _list_attempt * 15.0
                try:
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
                        follow_redirects=True,
                        timeout=float(_list_timeout)
                    )
                    list_data = safe_json_parse(list_resp)
                    break
                except (httpx.TimeoutException, httpx.RemoteProtocolError, httpx.ConnectError) as _list_exc:
                    logger.warning(f"[share/list] 第{_list_attempt+1}次失败: {type(_list_exc).__name__}: {_list_exc}")
                    if _list_attempt < _LIST_RETRIES - 1:
                        self.client.close()
                        self.client = httpx.Client(headers=self.headers, timeout=float(_list_timeout))
                        import time as _sleep_time
                        _sleep_time.sleep(1 + _list_attempt)
                    else:
                        raise
            if list_resp is None or list_data is None:
                return {"error": "获取文件列表失败，请重试"}
            
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
    _SERVER_ERROR_CODES = {500, 502, 503, 504}  # HTTP 服务器临时错误
    _SERVER_ERROR_MAX = 5      # 服务器错误最大重试次数
    _SERVER_ERROR_BASE_DELAY = 3   # 服务器错误首次延迟秒数
    _SERVER_ERROR_DELAY_STEP = 2   # 服务器错误延迟递增秒数（3→5→7→9→11）
    
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
                # DTS2026062238511 — HTTP 500/502/503/504 服务器临时错误重试
                if resp.status_code in self._SERVER_ERROR_CODES:
                    server_retry = attempt - start_attempt
                    if server_retry < self._SERVER_ERROR_MAX:
                        delay = self._SERVER_ERROR_BASE_DELAY + self._SERVER_ERROR_DELAY_STEP * server_retry
                        logger.warning(
                            f"[RETRY] {dir_path} 服务器错误 HTTP {resp.status_code} | "
                            f"server_retry={server_retry+1}/{self._SERVER_ERROR_MAX} "
                            f"delay={delay}s"
                        )
                        time.sleep(delay)
                        continue  # 重试
                    else:
                        logger.error(
                            f"[RETRY] {dir_path} 服务器错误 HTTP {resp.status_code} | "
                            f"已达最大重试次数 {self._SERVER_ERROR_MAX}"
                        )
                        return {"error": f"服务器错误 (HTTP {resp.status_code})，已重试{self._SERVER_ERROR_MAX}次"}
                
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
    
    # DTS2026061793847 — 流水线模式：BFS逐目录收集→立即转存，解决BDCLND过期
    # DTS2026061793848 — 按数量分批：攒够100个文件就暂停收集→转存→继续
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
            # DIAG: create_dir 用 httpx 成功，记录 httpx 实际发送的 Cookie 用于对比
            create_dir_headers = self._share_headers()
            logger.debug("[DIAG-CREATE] httpx _share_headers Cookie: %s", create_dir_headers.get('Cookie', '无'))
            logger.debug("[DIAG-CREATE] httpx cookie jar keys: %s", list(dict(self.client.cookies).keys()))
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
            elif errno == -7:
                # DTS2026061801238 — 目录已存在，视为成功
                return {"success": True, "path": path, "existed": True}
            elif errno != 0:
                error_msg = self._handle_error(errno, result.get("errmsg", ""))
                return {"error": error_msg, "errno": errno}
            
            return {"success": True, "path": path}
            
        except httpx.TimeoutException:
            return {"error": "请求超时，请检查网络连接"}
        except Exception as e:
            logger.error(f"创建目录失败: {e}")
            return {"error": str(e)}
    
    def transfer_files(self, share_id: str, uk: str, file_paths: List, target_path: str, pwd: str = "", share_link: str = "") -> dict:
        """批量转存文件
        
        根据开源项目（BaiduPCS-Go, baidupcsapi, BaiduPCS-Py）的实现，
        转存必须使用 /share/transfer 端点，并且 Referer 必须是分享链接。
        
        Args:
            share_id: 分享ID
            uk: 分享者UK
            file_paths: 文件路径列表（fs_id 或 path）
            target_path: 目标路径
            pwd: 提取码（如果有）
            share_link: 分享链接（用于设置 Referer）
        """
        self._ensure_client()
        
        # [DIAG] DTS-2026-010: 记录原始 cookie 字符串（debug 级别）
        logger.debug("[DIAG-COOKIE] self.cookie 原始字符串: %s", self.cookie)
        logger.debug("[DIAG-COOKIE] self.cookie 长度: %d 字符", len(self.cookie))
        
        # 获取 bdstoken（CSRF token，转存 API 必需）
        bdstoken = self._get_bdstoken()
        
        # DTS2026061827298 — 不在此处 create_dir，由调用方（流水线）负责创建目标目录
        # 此处冗余调用会导致：目录已存在 + ondup=newcopy → 百度创建带时间戳的副本目录
        
        # ✅ 修复：使用正确的端点 /share/transfer
        url = f"{self.BASE_URL}/share/transfer"
        params = {
            "shareid": share_id,
            "from": uk,
            "bdstoken": bdstoken,
            "channel": "chunlei",
            "clienttype": "0",
            "web": "1",
        }
        
        # ✅ 修复：使用 fsidlist 参数名（不是 filelist）
        # ✅ DTS2026062143821：pwd 不放 body（百度不认），通过 BDCLND cookie 传递
        data = {
            "fsidlist": json.dumps(file_paths),
            "path": target_path,
        }
        
        # 限流重试：最多 3 次，指数退避
        for attempt in range(3):
            try:
                # 全局速率控制
                _global_limiter.acquire(timeout=60.0)
                
                # ✅ DTS2026062143821：Cookie 由 httpx client cookie jar 自动管理
                # BAIDUID 由 _get_bdstoken() → _ensure_baiduid() 保证，此处无需重复检查
                
                # ✅ 修复：Referer 必须是分享链接（不是 /disk/main）
                # ✅ DTS2026062143821：对齐 BaiduPCS-Py 实现
                # BaiduPCS-Py 的 PAN_HEADERS 不含 Cookie，Cookie 由 Session 自动管理
                # 我们也必须从 headers 中移除 Cookie，让 httpx client cookie jar 生效
                headers = dict(self.headers)
                headers.pop("Cookie", None)  # 关键：移除手动 Cookie，让 client 自动管理
                headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
                headers["X-Requested-With"] = "XMLHttpRequest"
                headers["Origin"] = "https://pan.baidu.com"
                
                # 如果提供了 share_link，使用它作为 Referer
                if share_link:
                    headers["Referer"] = share_link
                else:
                    # 如果没有提供 share_link，尝试从 share_id 构造
                    headers["Referer"] = f"https://pan.baidu.com/s/{share_id}"
                
                # ===== 发送请求 =====
                # DTS-2026-010: 诊断日志改 debug 级别 + lazy formatting
                logger.debug("[transfer] 开始发送: files=%d, attempt=%d/3", len(file_paths), attempt+1)
                logger.debug("[transfer] Cookie header 长度: %d 字符", len(self.cookie))
                logger.debug("[transfer] Cookie header 包含: %s", [p.split('=', 1)[0].strip() for p in self.cookie.split(';') if '=' in p])
                # ✅ 诊断：检查 cookie jar 中的 cookies
                jar_dict = dict(self.client.cookies)
                logger.debug("[transfer] cookie jar 包含: %s", list(jar_dict.keys()))
                logger.debug("[transfer] BDCLND in jar: %s", 'BDCLND' in jar_dict)
                logger.debug("[transfer] BDCLND 值: %s...", jar_dict.get('BDCLND', '未找到')[:30])
                logger.debug("[transfer] User-Agent: %s", self.headers.get('User-Agent', '')[:50])
                logger.debug("[transfer] Referer: %s", headers.get('Referer', ''))
                logger.debug("[transfer] URL: %s", url)
                logger.debug("[transfer] params: %s", params)
                logger.debug("[transfer] data: %s", data)
                
                # ✅ DTS2026062143821：使用 self.client.post() 而非 httpx.post()
                # BaiduPCS-Py 用 requests.Session 自动管理 cookie，httpx.Client 同理
                # verify 后 BDCLND 已存入 client cookie jar，self.client.post 会自动携带
                _resp = self.client.post(
                    url,
                    params=params,
                    data=data,
                    headers=headers,
                    follow_redirects=True,
                    timeout=30.0
                )
                result = safe_json_parse(_resp)
                logger.debug("[transfer] 响应: status=%d, errno=%s, 完整响应=%s", _resp.status_code, result.get('errno'), result)
                
                if "error" in result:
                    return {"success": False, "error": result["error"]}
                
                errno = int(result.get("errno", -1))
                
                # 限流错误 → 立即返回，不重试（重试会加重限流）
                if errno in (-62, -9):
                    _global_limiter.report_rate_limit(cooldown=60.0)
                    logger.warning(f"转存被限流(errno={errno})，立即返回，等待用户稍后重试")
                    return {"success": False, "error": "请求过于频繁，请等待几分钟后重试", "errno": errno}
                
                # errno=4（请求超时）→ 重试（DTS2026062282633）
                if errno == 4:
                    if attempt < 2:
                        logger.warning(f"转存 errno=4（请求超时），第{attempt+1}次重试")
                        time.sleep(5)
                        continue
                    logger.warning(f"转存 errno=4 重试3次仍失败")
                    return {"success": False, "error": "请求超时，请稍后再试", "errno": errno}
                
                # errno=-3（用户未登录）— 不重试，直接返回
                # 重建 client 会丢失 cookie jar，导致后续请求更失败
                if errno == -3:
                    logger.error(f"转存返回 errno=-3（用户未登录），Cookie 可能已过期")
                    return {
                        "success": False, 
                        "error": "登录状态失效，请在百度网盘页面重新获取Cookie", 
                        "errno": errno,
                        "need_refresh_cookie": True
                    }
                
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
    
    def transfer_files_with_fallback(self, share_id: str, uk: str, file_list: List[dict], target_path: str, pwd: str = "", share_link: str = "") -> dict:
        """转存文件（自动 fallback：fs_id → path）
        
        file_list: [{"path": "/xxx", "fs_id": 123}, {"path": "/yyy", "fs_id": None}, ...]
        """
        # 先尝试 fs_id（过滤掉 None）
        file_ids = [int(f["fs_id"]) for f in file_list if f.get("fs_id")]
        if file_ids:
            logger.info(f"尝试 fs_id 格式转存: {len(file_ids)} 个文件")
            result = self.transfer_files(share_id, uk, file_ids, target_path, pwd, share_link)
            if result.get("success"):
                return result
            # DTS2026061948271 — errno=-6（文件不存在）也需要 fallback 到 path 格式
            if result.get("errno") not in (2, -6):
                return result  # 非格式错误，直接返回
            logger.info(f"fs_id 格式失败(errno={result.get('errno')}), 尝试 path 格式")
        
        # 降级到 path 格式
        file_paths = [f["path"] for f in file_list if f.get("path")]
        if not file_paths:
            return {"success": False, "error": "无有效文件路径"}
        logger.info(f"尝试 path 格式转存: {len(file_paths)} 个文件")
        return self.transfer_files(share_id, uk, file_paths, target_path, pwd, share_link)
    
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
    """批量转存管理器
    
    支持两种模式：
    1. 预构造模式：__init__(api, share_id, uk, files, target_path, pwd) 直接传参
    2. 延迟解析模式：__init__(api) 创建空实例，再调 prepare_transfer(url, pwd, target) 解析
    
    DTS2026061836255 — 重构支持延迟解析模式，修复 4 个崩溃 bug
    """
    
    def __init__(self, api: BaiduPanAPI, share_id: str = "", uk: str = "", 
                 files: list = None, target_path: str = "", pwd: str = "", share_link: str = ""):
        self.api = api
        self.share_id = share_id
        self.uk = uk
        self.files = files or []
        self.target_path = target_path
        self.pwd = pwd
        self.share_link = share_link  # ✅ 新增：保存分享链接，用于设置 Referer
        self.task_progress = {
            "status": "ready",
            "total": len(self.files),
            "completed": 0,
            "failed": 0,
            "current_batch": 0,
            "total_batches": (len(self.files) + 499) // 500 if self.files else 0,
            "start_time": None,
            "elapsed": 0,
            "speed": 0,
            "errors": []
        }
    
    def prepare_transfer(self, url: str, pwd: str, target_path: str) -> dict:
        """解析分享链接并准备转存参数（延迟解析模式）
        
        DTS2026061836255 — 新增方法，调用 get_share_info 解析分享信息
        """
        try:
            result = self.api.get_share_info(url, pwd)
            if "error" in result:
                return {"error": result["error"]}
            
            self.share_id = result["share_id"]
            self.uk = result["uk"]
            self.files = result.get("files", [])
            self.target_path = target_path
            self.pwd = pwd
            self.share_link = url  # ✅ 新增：保存分享链接，用于设置 Referer
            
            # 更新进度
            self.task_progress["total"] = len(self.files)
            self.task_progress["total_batches"] = (len(self.files) + 499) // 500 if self.files else 0
            
            return {
                "success": True,
                "share_id": self.share_id,
                "uk": self.uk,
                "total_files": len(self.files),
                "share_title": result.get("title", ""),
                "files": self.files
            }
        except Exception as e:
            logger.error(f"prepare_transfer 失败: {e}")
            return {"error": str(e)}
    
    def execute_transfer(self, batch_size: int = 500, batch_interval: float = 5.0,
                         overwrite_confirmed: bool = False) -> dict:
        """执行批量转存
        
        DTS2026061836255 — 新增 overwrite_confirmed 参数
        """
        if self.task_progress["status"] != "ready":
            return {"error": "任务未就绪"}
        
        self.task_progress["status"] = "running"
        self.task_progress["start_time"] = time.time()
        
        total_files = len(self.files)
        completed = 0
        failed = 0
        errors = []
        
        try:
            # DTS2026061827298 — 目标目录由调用方创建（transfer_files 不再内部创建）
            # 先检查是否存在，避免百度创建带时间戳的副本目录
            if self.api.check_file_exists(self.target_path):
                logger.info(f"目标目录已存在，跳过创建: {self.target_path}")
            else:
                mkdir_result = self.api.create_dir(self.target_path)
                if not mkdir_result.get("success") and mkdir_result.get("error_code") != -7:
                    logger.warning(f"创建目标目录失败: {mkdir_result}")
            
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
                result = self.api.transfer_files(self.share_id, self.uk, file_ids, self.target_path, self.pwd, self.share_link)
                
                if result.get("success"):
                    completed += len(batch)
                    logger.info(f"批次 {batch_num} 成功: {len(batch)} 个文件")
                elif result.get("errno") == 2:
                    # fs_id 失败，尝试 path
                    logger.info(f"批次 {batch_num} fs_id 失败，尝试 path 格式")
                    file_paths = [f.get("path") for f in batch]
                    result = self.api.transfer_files(self.share_id, self.uk, file_paths, self.target_path, self.pwd, self.share_link)
                    
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
            
            result = {
                "success": True,
                "completed": completed,
                "failed": failed,
                "errors": errors
            }
            
            # DTS2026061836255 — 文件冲突时返回 need_confirm 让前端提示用户
            if failed > 0 and not overwrite_confirmed:
                # 检查是否有文件已存在的错误（errno=12 或包含"已存在"）
                conflict_errors = [e for e in errors if "已存在" in e or "errno=12" in e.lower()]
                if conflict_errors:
                    result["need_confirm"] = True
                    result["existing_files"] = conflict_errors
                    self.task_progress["status"] = "waiting_confirm"
            
            return result
            
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
