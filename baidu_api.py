"""百度网盘 API 封装 - 增强版"""
import httpx
import time
import json
import threading
import logging
from typing import Optional
from urllib.parse import quote, unquote
from functools import wraps

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('BaiduPanAPI')


def retry_on_error(max_retries=3, delay=1, backoff=2):
    """重试装饰器"""
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
    """百度网盘内部API封装"""
    
    BASE_URL = "https://pan.baidu.com"
    SHARE_BASE_URL = "https://pan.baidu.com/share"
    
    # API endpoints
    LIST_URL = f"{BASE_URL}/rest/2.0/xpan/file?method=list"
    SHARE_INFO_URL = f"{SHARE_BASE_URL}/proxy"
    SHARE_LIST_URL = f"{BASE_URL}/rest/2.0/xpan/share?method=list"
    TRANSFER_URL = f"{BASE_URL}/rest/2.0/xpan/share?method=transfer"
    CREATE_DIR_URL = f"{BASE_URL}/rest/2.0/xpan/file?method=create"
    FILE_METAS_URL = f"{BASE_URL}/rest/2.0/xpan/multimedia?method=filemetas"
    
    # 错误码映射
    ERROR_CODES = {
        0: "成功",
        -1: "系统错误",
        -2: "参数错误",
        -3: "用户未登录",
        -4: "Cookie无效或已过期",
        -6: "文件不存在",
        -7: "文件已存在",
        -8: "文件被锁定",
        -9: "空间不足",
        -10: "验证码错误",
        -12: "需要验证码",
        -19: "分享链接已失效",
        -20: "分享链接已过期",
        -21: "提取码错误",
        -62: "请求过于频繁",
    }
    
    def __init__(self, cookie: str):
        self.cookie = cookie
        self.headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://pan.baidu.com/"
        }
        self.client = httpx.Client(headers=self.headers, timeout=30.0)
    
    def _handle_error(self, errno: int, errmsg: str = "") -> str:
        """处理错误码，返回友好的错误信息"""
        if errno in self.ERROR_CODES:
            return self.ERROR_CODES[errno]
        return errmsg or f"未知错误 (errno={errno})"
    
    @retry_on_error(max_retries=2, delay=1)
    def validate_cookie(self) -> dict:
        """验证Cookie有效性，返回用户信息"""
        try:
            resp = self.client.get(f"{self.BASE_URL}/api/quota")
            data = resp.json()
            if data.get("errno") == 0:
                return {
                    "valid": True,
                    "username": data.get("username", ""),
                    "uk": data.get("uk", ""),
                    "total": data.get("total", 0),
                    "used": data.get("used", 0)
                }
            error_msg = self._handle_error(data.get("errno", -1))
            return {"valid": False, "error": error_msg}
        except httpx.TimeoutException:
            return {"valid": False, "error": "请求超时，请检查网络连接"}
        except Exception as e:
            logger.error(f"验证Cookie失败: {e}")
            return {"valid": False, "error": str(e)}
    
    @retry_on_error(max_retries=2, delay=1)
    def get_share_info(self, share_link: str, pwd: str = "") -> dict:
        """获取分享链接信息"""
        surl = self._extract_surl(share_link)
        if not surl:
            return {"error": "无效的分享链接"}
        
        url = f"{self.SHARE_BASE_URL}/proxy"
        params = {
            "app_id": "250528",
            "surl": surl,
            "channel": "chunlei",
            "web": "1",
            "page": "1",
            "num": "100",
            "root": "1"
        }
        if pwd:
            params["pwd"] = pwd
        
        try:
            resp = self.client.get(url, params=params)
            data = resp.json()
            
            if "errno" in data and data["errno"] != 0:
                errno = data["errno"]
                error_msg = self._handle_error(errno, data.get("errmsg", ""))
                logger.warning(f"获取分享信息失败: {error_msg} (errno={errno})")
                return {"error": error_msg}
            
            return {
                "share_id": data.get("shareid", ""),
                "uk": data.get("uk", ""),
                "file_list": data.get("list", []),
                "title": data.get("title", ""),
                "total": len(data.get("list", []))
            }
        except httpx.TimeoutException:
            return {"error": "请求超时，请检查网络连接"}
        except Exception as e:
            logger.error(f"获取分享信息异常: {e}")
            return {"error": str(e)}
    
    def get_share_file_list(self, share_id: str, uk: str, dir_path: str = "/", 
                            page: int = 1, num: int = 100) -> dict:
        """获取分享目录下的文件列表（支持分页）"""
        url = f"{self.SHARE_BASE_URL}/proxy"
        params = {
            "app_id": "250528",
            "shareid": share_id,
            "uk": uk,
            "page": str(page),
            "num": str(num),
            "dir": dir_path,
            "root": "1",
            "channel": "chunlei",
            "web": "1"
        }
        
        try:
            resp = self.client.get(url, params=params)
            data = resp.json()
            
            return {
                "list": data.get("list", []),
                "has_more": data.get("has_more", 0) == 1,
                "errno": data.get("errno", -1)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def get_all_share_files(self, share_id: str, uk: str, dir_path: str = "/") -> list:
        """递归获取分享目录下所有文件"""
        all_files = []
        page = 1
        
        while True:
            result = self.get_share_file_list(share_id, uk, dir_path, page=page)
            
            if "error" in result:
                break
            
            file_list = result.get("list", [])
            if not file_list:
                break
            
            for item in file_list:
                if item.get("isdir") == 1:
                    sub_files = self.get_all_share_files(share_id, uk, item.get("path", ""))
                    all_files.extend(sub_files)
                else:
                    all_files.append(item)
            
            if not result.get("has_more"):
                break
            
            page += 1
            time.sleep(0.5)
        
        return all_files
    
    @retry_on_error(max_retries=2, delay=2)
    def transfer_files(self, share_id: str, uk: str, fsids: list, 
                       target_path: str, pwd: str = "") -> dict:
        """批量转存文件到自己的网盘"""
        url = self.TRANSFER_URL
        fsid_list = json.dumps(fsids)
        
        data = {
            "shareid": share_id,
            "from": uk,
            "fsidlist": fsid_list,
            "path": target_path,
            "app_id": "250528",
            "channel": "chunlei",
            "web": "1"
        }
        
        if pwd:
            data["pwd"] = pwd
        
        try:
            resp = self.client.post(url, data=data)
            result = resp.json()
            
            if result.get("errno") == 0:
                logger.info(f"成功转存 {len(fsids)} 个文件到 {target_path}")
                return {
                    "success": True,
                    "task_id": result.get("task_id", ""),
                    "extra": result.get("extra", {})
                }
            else:
                errno = result.get("errno", -1)
                error_msg = self._handle_error(errno, result.get("errmsg", ""))
                logger.warning(f"转存失败: {error_msg} (errno={errno})")
                return {
                    "success": False,
                    "error": error_msg,
                    "errno": errno
                }
        except httpx.TimeoutException:
            return {"success": False, "error": "请求超时，请检查网络连接"}
        except Exception as e:
            logger.error(f"转存异常: {e}")
            return {"success": False, "error": str(e)}
    
    def create_dir(self, path: str) -> dict:
        """创建目录"""
        url = self.CREATE_DIR_URL
        data = {
            "path": path,
            "isdir": "1",
            "app_id": "250528",
            "channel": "chunlei",
            "web": "1"
        }
        
        try:
            resp = self.client.post(url, data=data)
            result = resp.json()
            
            if result.get("errno") == 0:
                return {"success": True}
            return {"success": False, "error": result.get("errmsg", "创建目录失败")}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def check_file_exists(self, path: str) -> bool:
        """检查文件是否已存在"""
        try:
            parent_dir = "/".join(path.split("/")[:-1])
            filename = path.split("/")[-1]
            
            resp = self.client.get(self.LIST_URL, params={"dir": parent_dir})
            data = resp.json()
            
            if data.get("errno") == 0:
                for item in data.get("list", []):
                    if item.get("server_filename") == filename:
                        return True
            return False
        except:
            return False
    
    def _extract_surl(self, link: str) -> str:
        """从分享链接提取surl"""
        if "/s/" in link:
            return link.split("/s/")[-1].split("?")[0].split("/")[0]
        elif "surl=" in link:
            return link.split("surl=")[-1].split("&")[0]
        return ""
    
    def close(self):
        """关闭HTTP客户端"""
        self.client.close()


class BatchTransferManager:
    """批量转存任务管理器 - 增强版"""
    
    def __init__(self, api: BaiduPanAPI):
        self.api = api
        self._paused = False
        self._lock = threading.Lock()
        self.task_progress = {
            "total": 0,
            "completed": 0,
            "failed": 0,
            "status": "idle",
            "current_batch": 0,
            "total_batches": 0,
            "failed_files": [],
            "logs": [],
            "speed": 0,
            "elapsed": 0,
            "start_time": None
        }
    
    def pause(self):
        """暂停任务"""
        with self._lock:
            self._paused = True
            self.task_progress["status"] = "paused"
            self.task_progress["logs"].append("任务已暂停")
    
    def resume(self):
        """恢复任务"""
        with self._lock:
            self._paused = False
            self.task_progress["status"] = "running"
            self.task_progress["logs"].append("任务已恢复")
    
    def _check_paused(self):
        """检查是否暂停，如果是则等待"""
        while True:
            with self._lock:
                if not self._paused:
                    return
            time.sleep(0.5)
    
    def prepare_transfer(self, share_link: str, pwd: str, target_path: str) -> dict:
        """准备转存任务"""
        share_info = self.api.get_share_info(share_link, pwd)
        
        if "error" in share_info:
            return share_info
        
        share_id = share_info.get("share_id")
        uk = share_info.get("uk")
        
        all_files = self.api.get_all_share_files(share_id, uk)
        files = [f for f in all_files if f.get("isdir") != 1]
        
        self.task_progress.update({
            "total": len(files),
            "completed": 0,
            "failed": 0,
            "status": "ready",
            "share_id": share_id,
            "uk": uk,
            "files": files,
            "target_path": target_path
        })
        
        return {
            "success": True,
            "total_files": len(files),
            "share_title": share_info.get("title", ""),
            "files": [{"name": f.get("server_filename"), "path": f.get("path")} for f in files[:50]]
        }
    
    def execute_transfer(self, overwrite_confirmed: bool = False) -> dict:
        """执行批量转存"""
        if self.task_progress["status"] != "ready":
            return {"error": "任务未就绪"}
        
        files = self.task_progress.get("files", [])
        if not files:
            return {"error": "没有文件需要转存"}
        
        batch_size = 500
        batches = [files[i:i + batch_size] for i in range(0, len(files), batch_size)]
        
        self.task_progress.update({
            "status": "running",
            "total_batches": len(batches),
            "current_batch": 0,
            "start_time": time.time()
        })
        
        success_count = 0
        failed_files = []
        
        for batch_idx, batch in enumerate(batches):
            # 检查是否暂停
            self._check_paused()
            
            self.task_progress["current_batch"] = batch_idx + 1
            
            fsids = [f.get("fs_id") for f in batch]
            
            if not overwrite_confirmed:
                existing = []
                for f in batch:
                    target = f"{self.task_progress['target_path']}/{f.get('server_filename')}"
                    if self.api.check_file_exists(target):
                        existing.append(f.get("server_filename"))
                
                if existing:
                    self.task_progress["status"] = "waiting_confirm"
                    return {
                        "need_confirm": True,
                        "existing_files": existing[:20],
                        "total_existing": len(existing),
                        "message": f"发现 {len(existing)} 个文件已存在，是否覆盖？"
                    }
            
            result = self.api.transfer_files(
                self.task_progress["share_id"],
                self.task_progress["uk"],
                fsids,
                self.task_progress["target_path"]
            )
            
            if result.get("success"):
                success_count += len(batch)
                self.task_progress["completed"] = success_count
                self.task_progress["logs"].append(
                    f"批次 {batch_idx + 1}: 成功转存 {len(batch)} 个文件"
                )
            else:
                for f in batch:
                    # 检查是否暂停
                    self._check_paused()
                    
                    retry_result = self.api.transfer_files(
                        self.task_progress["share_id"],
                        self.task_progress["uk"],
                        [f.get("fs_id")],
                        self.task_progress["target_path"]
                    )
                    
                    if retry_result.get("success"):
                        success_count += 1
                        self.task_progress["completed"] = success_count
                    else:
                        failed_files.append({
                            "name": f.get("server_filename"),
                            "error": retry_result.get("error")
                        })
                        self.task_progress["failed"] += 1
            
            # 计算速度和已用时间
            elapsed = time.time() - self.task_progress["start_time"]
            self.task_progress["elapsed"] = round(elapsed, 1)
            if elapsed > 0:
                self.task_progress["speed"] = round(success_count / elapsed, 2)
            
            if batch_idx < len(batches) - 1:
                time.sleep(3)
        
        self.task_progress["status"] = "completed"
        self.task_progress["failed_files"] = failed_files
        
        return {
            "success": True,
            "total": len(files),
            "completed": success_count,
            "failed": len(failed_files),
            "failed_files": failed_files,
            "elapsed": self.task_progress["elapsed"],
            "speed": self.task_progress["speed"]
        }
    
    def get_progress(self) -> dict:
        """获取任务进度"""
        return self.task_progress
