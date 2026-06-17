"""FastAPI 主应用 - 增强版"""
import json
import sqlite3
import uuid
import threading
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from baidu_api import BaiduPanAPI, BatchTransferManager

logger = logging.getLogger('baidu-pan-tool')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

app = FastAPI(title="百度网盘批量转存工具")

# 全局异常处理 — 确保所有响应都是 JSON（避免前端解析 HTML 崩溃）
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": f"服务器内部错误: {str(exc)}"}
    )

@app.exception_handler(KeyError)
async def key_error_handler(request: Request, exc: KeyError):
    logger.error(f"KeyError: {exc}")
    return JSONResponse(
        status_code=404,
        content={"detail": f"资源不存在: {exc}"}
    )

# CORS - 允许来自百度网盘页面的跨域请求（书签小工具回传Cookie）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 数据库初始化
DB_PATH = "tasks.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            share_link TEXT,
            target_path TEXT,
            status TEXT DEFAULT 'pending',
            total_files INTEGER DEFAULT 0,
            completed_files INTEGER DEFAULT 0,
            failed_files INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT,
            logs TEXT DEFAULT '[]',
            error_message TEXT,
            batch_id TEXT
        )
    """)
    # 添加批量任务表
    c.execute("""
        CREATE TABLE IF NOT EXISTS batch_tasks (
            id TEXT PRIMARY KEY,
            name TEXT,
            total_links INTEGER DEFAULT 0,
            completed_links INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()


init_db()

# DB schema migration: add new columns if missing
def migrate_db():
    """Add file_list, transferred_files, checkpoint columns to tasks table if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for col, col_def in [
        ("file_list", "TEXT DEFAULT '[]'"),
        ("transferred_files", "TEXT DEFAULT '[]'"),
        ("checkpoint", "TEXT DEFAULT '{}'"),
    ]:
        try:
            c.execute(f"ALTER TABLE tasks ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()

migrate_db()


# DTS2026061748508 — 执行日志功能：同时写入内存缓冲 + DB
def add_task_log(task_id, msg, level="INFO"):
    """向任务的内存日志缓冲和DB追加一条日志
    
    Args:
        task_id: 任务ID
        msg: 日志消息
        level: 日志级别 (INFO/WARNING/ERROR/SUCCESS)
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    # 内存中存结构化对象（前端用）
    log_entry = {"time": timestamp, "level": level, "message": msg}
    # DB 中存纯文本（导出用）
    log_text = f"[{timestamp}] [{level}] {msg}"
    
    # 写入内存缓冲
    if task_id in active_tasks:
        task = active_tasks[task_id]
        if "task_logs" not in task:
            task["task_logs"] = []
        task["task_logs"].append(log_entry)
        # 限制内存中最多保留200条日志
        if len(task["task_logs"]) > 200:
            task["task_logs"] = task["task_logs"][-200:]
    
    # 写入DB（追加到现有日志数组）
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT logs FROM tasks WHERE id = ?", (task_id,))
        row = c.fetchone()
        if row:
            existing = json.loads(row[0]) if row[0] else []
            existing.append(log_text)
            # DB中最多保留500条
            if len(existing) > 500:
                existing = existing[-500:]
            c.execute("UPDATE tasks SET logs = ?, updated_at = ? WHERE id = ?",
                      (json.dumps(existing, ensure_ascii=False), datetime.now().isoformat(), task_id))
            conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"写入任务日志到DB失败: {e}")


# DTS2026061748509 — 断点续传：保存已转存文件ID到DB，支持中断后恢复
def _save_checkpoint(task_id, transferred_fs_ids, last_batch_index, total_files):
    """保存转存断点到内存和DB
    
    Args:
        task_id: 任务ID
        transferred_fs_ids: 已转存的文件fs_id集合
        last_batch_index: 最后完成的批次索引
        total_files: 总文件数
    """
    checkpoint = {
        "transferred_fs_ids": list(transferred_fs_ids),
        "last_batch_index": last_batch_index,
        "total_files": total_files,
        "saved_at": datetime.now().isoformat()
    }
    
    # 保存到内存
    if task_id in active_tasks:
        active_tasks[task_id]["checkpoint"] = checkpoint
    
    # 保存到DB
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE tasks SET checkpoint = ?, updated_at = ? WHERE id = ?",
                  (json.dumps(checkpoint, ensure_ascii=False), datetime.now().isoformat(), task_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"保存断点到DB失败: {e}")


def _clear_checkpoint(task_id):
    """清除断点（任务完成后调用）"""
    if task_id in active_tasks:
        active_tasks[task_id]["checkpoint"] = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE tasks SET checkpoint = '{}', updated_at = ? WHERE id = ?",
                  (datetime.now().isoformat(), task_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"清除断点失败: {e}")


def _update_task_db(task_id, status, completed, failed, total, error=""):
    """更新任务状态到DB"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            UPDATE tasks 
            SET status = ?, completed_files = ?, failed_files = ?, total_files = ?, 
                error_message = ?, updated_at = ?
            WHERE id = ?
        """, (status, completed, failed, total, error, datetime.now().isoformat(), task_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"更新任务DB失败: {e}")

# 全局状态存储
active_tasks = {}
active_batches = {}
# 解析进度追踪（两阶段：快速验证 + 后台文件枚举）
parsing_tasks = {}  # task_id -> {status, share_info, files, dirs_scanned, error, ...}

# Cookie 书签小工具回传状态
_received_cookie = {"cookie": None}


class CookieRequest(BaseModel):
    cookie: str


class ShareLinkRequest(BaseModel):
    share_link: str
    pwd: Optional[str] = ""
    target_path: str


class BatchShareRequest(BaseModel):
    links: List[dict]  # [{url: "...", pwd: "..."}]
    target_path: str
    create_subdirs: bool = True  # 是否为每个分享创建子目录


class ConfirmRequest(BaseModel):
    task_id: str
    overwrite: bool


# 读取HTML文件
def get_html():
    html_path = Path(__file__).parent / "templates" / "index.html"
    return html_path.read_text(encoding="utf-8")


# ============ API 路由 ============


@app.get("/", response_class=HTMLResponse)
async def index():
    return get_html()


@app.post("/api/cookie/validate")
async def validate_cookie(req: CookieRequest):
    """验证Cookie有效性"""
    api = BaiduPanAPI(req.cookie)
    try:
        result = api.validate_cookie()
        return result
    finally:
        api.close()


# Load bookmarklet template once at import time
import os as _os
_BM_TEMPLATE_PATH = _os.path.join(_os.path.dirname(__file__), 'bookmarklet_template.js')
with open(_BM_TEMPLATE_PATH, 'r', encoding='utf-8') as _f:
    _BM_TEMPLATE = _f.read()


@app.get("/api/cookie/bookmarklet")
async def get_bookmarklet(request: Request):
    host = request.headers.get("host", "localhost:8089")
    js = _BM_TEMPLATE.replace("__SERVER_URL__", "https://" + host)
    return Response(content=js, media_type="application/javascript; charset=utf-8")


@app.post("/api/cookie/receive")
async def receive_cookie(req: CookieRequest):
    """接收从书签小工具回传的 Cookie"""
    if not req.cookie or "BDUSS" not in req.cookie:
        return JSONResponse({"ok": False, "message": "未检测到有效的百度网盘Cookie，请先登录"})

    _received_cookie["cookie"] = req.cookie
    return {"ok": True, "message": "Cookie已接收"}


@app.get("/api/cookie/poll")
async def poll_cookie():
    """前端轮询：检查是否有新 Cookie 回传"""
    cookie = _received_cookie.get("cookie")
    if cookie:
        # 取出后清空，避免重复
        _received_cookie["cookie"] = None
        return {"received": True, "cookie": cookie}
    return {"received": False}


@app.post("/api/share/parse")
async def parse_share_link(req: ShareLinkRequest, request: Request):
    """懒加载解析：只扫描顶层目录（2次请求），子目录按需展开
    
    请求量对比：
    - 旧方案：递归遍历 = 600-1000次请求 → 必然触发限流
    - 新方案：顶层扫描 = 2次请求 → 不会触发限流
    """
    cookie = request.headers.get("X-Baidu-Cookie", "")
    if not cookie:
        cookie = _received_cookie.get("cookie") or ""
    if not cookie:
        raise HTTPException(status_code=400, detail="请先设置Cookie")
    
    api = BaiduPanAPI(cookie)
    
    try:
        # 顶层扫描：verify + list = 2次请求
        share_info = api.get_share_info(req.share_link, req.pwd)
        
        if "error" in share_info:
            api.close()
            if share_info.get("error_code") == -12:
                return JSONResponse(
                    status_code=400,
                    content={
                        "detail": share_info["error"],
                        "error_code": -12,
                        "solution": share_info.get("solution", ""),
                        "share_link": share_info.get("share_link", req.share_link)
                    }
                )
            if share_info.get("error_code") == -62:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": share_info["error"],
                        "error_code": -62
                    }
                )
            raise HTTPException(status_code=400, detail=share_info["error"])
        
        task_id = str(uuid.uuid4())[:8]
        surl = api._extract_surl(req.share_link)
        
        # 获取顶层子项列表（已有 isdir 字段）
        top_items = share_info.get("files", [])
        dirs_count = sum(1 for f in top_items if int(f.get("isdir", 0)) == 1)
        files_count = len(top_items) - dirs_count
        
        # 缓存顶层子项到 api._children_cache
        cache_key = f"{surl}:/" 
        api._children_cache[cache_key] = top_items
        
        # 保存任务信息（不启动后台遍历）
        active_tasks[task_id] = {
            "api": api,
            "cookie": cookie,
            "share_link": req.share_link,
            "pwd": req.pwd or "",
            "share_info": {
                "share_id": share_info.get("share_id", ""),
                "uk": share_info.get("uk", ""),
                "title": share_info.get("title", ""),
            },
            "surl": surl,
            "target_path": req.target_path,
            "status": "ready",
            "mode": "lazy"  # 标记为懒加载模式
        }
        
        # 保存到数据库
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO tasks (id, share_link, target_path, status, total_files, created_at, updated_at)
            VALUES (?, ?, ?, 'ready', ?, ?, ?)
        """, (task_id, req.share_link, req.target_path, 0,
              datetime.now().isoformat(), datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        return {
            "task_id": task_id,
            "phase": "ready",
            "share_title": share_info.get("title", ""),
            "share_id": share_info.get("share_id", ""),
            "uk": share_info.get("uk", ""),
            "surl": surl,
            "top_items": top_items,
            "dirs_count": dirs_count,
            "files_count": files_count,
            "total_items": len(top_items)
        }
        
    except HTTPException:
        if not api.client.is_closed:
            api.close()
        raise
    except Exception as e:
        if not api.client.is_closed:
            api.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/share/parse/progress/{task_id}")
async def get_parse_progress(task_id: str):
    """获取解析进度（阶段2文件枚举进度）"""
    if task_id not in parsing_tasks:
        raise HTTPException(status_code=404, detail="解析任务不存在")
    
    pt = parsing_tasks[task_id]
    
    result = {
        "task_id": task_id,
        "status": pt["status"],
        "dirs_scanned": pt["dirs_scanned"],
        "files_found": pt["files_found"],
        "share_title": pt["share_info"].get("title", ""),
    }
    
    if pt["status"] == "ready":
        result["total_files"] = pt["total_files"]
        result["preview_files"] = [{"name": f.get("server_filename"), "path": f.get("path")} for f in pt["files"][:50]]
    
    if pt["status"] == "error":
        result["error"] = pt["error"]
    
    return result


@app.post("/api/share/expand/{task_id}")
async def expand_directory(task_id: str, request: Request):
    """展开子目录（懒加载，每次1次API请求）
    
    前端点击文件夹时调用，返回该目录的直接子项。
    结果会被缓存，重复展开不消耗API请求。
    """
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = active_tasks[task_id]
    api = task.get("api")
    if not api:
        raise HTTPException(status_code=400, detail="API实例不可用")
    
    # 获取要展开的目录路径
    body = await request.json()
    dir_path = body.get("path", "/")
    surl = task.get("surl", "")
    
    if not surl:
        raise HTTPException(status_code=400, detail="缺少surl参数")
    
    # 调用懒加载方法（有缓存，不会重复请求）
    result = api.get_share_children(surl, dir_path)
    
    if "error" in result:
        error_code = result.get("error_code", 0)
        if error_code == -62:
            return JSONResponse(status_code=429, content={"detail": result["error"]})
        raise HTTPException(status_code=400, detail=result["error"])
    
    items = result.get("list", [])
    dirs_count = sum(1 for f in items if int(f.get("isdir", 0)) == 1)
    files_count = len(items) - dirs_count
    
    return {
        "path": dir_path,
        "items": items,
        "dirs_count": dirs_count,
        "files_count": files_count,
        "cached": result.get("cached", False)
    }


@app.post("/api/share/transfer-selected/{task_id}")
async def transfer_selected(task_id: str, request: Request):
    """转存选中的文件/目录（目录自动递归展开）
    
    选中目录 = 选中该目录下所有文件（递归）。
    前端发送选中的文件/文件夹，后端自动展开目录并收集所有文件。
    """
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = active_tasks[task_id]
    api = task.get("api")
    if not api:
        raise HTTPException(status_code=400, detail="API实例不可用")
    
    body = await request.json()
    items = body.get("items", [])
    selected_paths = body.get("paths", [])
    target_path = body.get("target_path", task.get("target_path", "/我的资源"))
    surl = task.get("surl", "")
    
    if not surl:
        raise HTTPException(status_code=400, detail="缺少surl参数，请重新解析分享链接")
    
    # 第一步：分离文件和目录
    direct_files = []  # 直接选中的文件
    dir_paths = []     # 需要递归展开的目录
    if items:
        for item in items:
            isdir = int(item.get("isdir", 0))
            if isdir == 1:
                dir_paths.append(item.get("path", ""))
            else:
                direct_files.append({
                    "path": item.get("path", ""),
                    "fs_id": item.get("fs_id"),
                })
    elif selected_paths:
        # 旧格式：只有路径，无法区分文件/目录，全部当作文件处理
        direct_files = [{"path": p, "fs_id": None} for p in selected_paths]
    
    # 第二步：递归展开目录，收集目录下所有文件
    total_api_requests = 0
    for dir_path in dir_paths:
        if not dir_path:
            continue
        logger.info(f"递归展开目录: {dir_path}")
        result = api.collect_files_recursive(surl, dir_path)
        
        if result.get("error"):
            error = result["error"]
            error_code = result.get("error_code", 0)
            if error_code == -62:
                return JSONResponse(status_code=429, content={"detail": f"展开目录 {dir_path} 时被限流: {error}"})
            raise HTTPException(status_code=400, detail=f"展开目录 {dir_path} 失败: {error}")
        
        dir_files = result.get("files", [])
        total_api_requests += result.get("api_requests", 0)
        for f in dir_files:
            direct_files.append({
                "path": f.get("path", ""),
                "fs_id": f.get("fs_id"),
            })
        logger.info(f"目录 {dir_path} 收集到 {len(dir_files)} 个文件, {result.get('api_requests', 0)} 次API请求")
    
    if not direct_files:
        raise HTTPException(status_code=400, detail="未找到任何可转存的文件")
    
    # 去重（同一文件可能被多个路径选中）
    seen = set()
    unique_files = []
    for f in direct_files:
        key = f.get("fs_id") or f.get("path")
        if key and key not in seen:
            seen.add(key)
            unique_files.append(f)
    
    share_info = task.get("share_info", {})
    share_id = share_info.get("share_id", "")
    uk = share_info.get("uk", "")
    
    logger.info(f"开始转存: {len(unique_files)} 个文件 (来自 {len(direct_files)} 个直接文件 + {len(dir_paths)} 个目录展开, {total_api_requests} 次API请求)")
    
    # 使用 transfer_files_with_fallback（fs_id → path 自动降级）
    result = api.transfer_files_with_fallback(share_id, uk, unique_files, target_path)
    
    if result.get("success"):
        msg = f"成功转存 {len(unique_files)} 个文件"
        if dir_paths:
            msg += f"（展开 {len(dir_paths)} 个目录）"
        return {
            "success": True,
            "message": msg,
            "task_id": result.get("task_id"),
            "count": len(unique_files),
            "dirs_expanded": len(dir_paths),
            "api_requests": total_api_requests
        }
    else:
        error_code = result.get("errno", 0)
        if error_code in (-62, -9):
            return JSONResponse(status_code=429, content={"detail": result.get("error", "请求过于频繁")})
        raise HTTPException(status_code=400, detail=result.get("error", "转存失败"))


@app.post("/api/batch/parse")
async def parse_batch_links(req: BatchShareRequest, request: Request):
    """批量解析分享链接"""
    cookie = request.headers.get("X-Baidu-Cookie", "")
    if not cookie:
        raise HTTPException(status_code=400, detail="请先设置Cookie")
    
    batch_id = str(uuid.uuid4())[:8]
    results = []
    total_files = 0
    
    api = BaiduPanAPI(cookie)
    
    try:
        for i, link_info in enumerate(req.links):
            url = link_info.get("url", "")
            pwd = link_info.get("pwd", "")
            
            if not url:
                continue
            
            # 为每个分享创建子目录
            target = req.target_path
            if req.create_subdirs:
                # 从链接提取标识作为目录名
                if "/s/" in url:
                    surl = url.split("/s/")[-1].split("?")[0]
                elif "surl=" in url:
                    surl = url.split("surl=")[-1].split("&")[0]
                else:
                    surl = f"share_{i}"
                target = f"{req.target_path}/{surl}"
            
            manager = BatchTransferManager(api)
            result = manager.prepare_transfer(url, pwd, target)
            
            if "error" in result:
                results.append({
                    "url": url,
                    "status": "error",
                    "error": result["error"]
                })
            else:
                task_id = str(uuid.uuid4())[:8]
                file_count = result["total_files"]
                total_files += file_count
                
                # 保存任务
                active_tasks[task_id] = {
                    "manager": manager,
                    "api": api,
                    "cookie": cookie,
                    "share_link": url,
                    "target_path": target,
                    "status": "ready",
                    "batch_id": batch_id
                }
                
                results.append({
                    "url": url,
                    "task_id": task_id,
                    "status": "ready",
                    "total_files": file_count,
                    "share_title": result.get("share_title", "")
                })
        
        # 保存批量任务
        active_batches[batch_id] = {
            "tasks": [r for r in results if r.get("task_id")],
            "status": "ready",
            "total_files": total_files,
            "api": api
        }
        
        # 保存到数据库
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO batch_tasks (id, total_links, status, created_at, updated_at)
            VALUES (?, ?, 'ready', ?, ?)
        """, (batch_id, len(req.links), datetime.now().isoformat(), datetime.now().isoformat()))
        
        for r in results:
            if r.get("task_id"):
                c.execute("""
                    INSERT INTO tasks (id, share_link, target_path, status, total_files, created_at, updated_at, batch_id)
                    VALUES (?, ?, ?, 'ready', ?, ?, ?, ?)
                """, (r["task_id"], r["url"], req.target_path, r["total_files"],
                      datetime.now().isoformat(), datetime.now().isoformat(), batch_id))
        
        conn.commit()
        conn.close()
        
        return {
            "batch_id": batch_id,
            "total_links": len(req.links),
            "total_files": total_files,
            "results": results
        }
    except Exception as e:
        api.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/batch/{batch_id}/start")
async def start_batch_task(batch_id: str, request: Request):
    """启动批量任务"""
    if batch_id not in active_batches:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    
    batch = active_batches[batch_id]
    cookie = request.headers.get("X-Baidu-Cookie", "")
    
    def run_batch():
        completed = 0
        for task_info in batch["tasks"]:
            task_id = task_info["task_id"]
            if task_id in active_tasks:
                task = active_tasks[task_id]
                manager = task["manager"]
                
                try:
                    result = manager.execute_transfer(overwrite_confirmed=True)
                    task["status"] = "completed" if result.get("success") else "error"
                    if result.get("success"):
                        completed += 1
                except Exception as e:
                    task["status"] = "error"
                    task["error"] = str(e)
        
        batch["status"] = "completed"
        batch["completed_links"] = completed
        
        # 关闭 api
        api = batch.get("api")
        if api and not api.client.is_closed:
            api.close()
    
    thread = threading.Thread(target=run_batch)
    thread.daemon = True
    thread.start()
    
    return {"message": "批量任务已启动", "batch_id": batch_id}


@app.get("/api/batch/{batch_id}/progress")
async def get_batch_progress(batch_id: str):
    """获取批量任务进度"""
    if batch_id not in active_batches:
        raise HTTPException(status_code=404, detail="批量任务不存在")
    
    batch = active_batches[batch_id]
    
    tasks_progress = []
    total_completed = 0
    total_failed = 0
    
    for task_info in batch["tasks"]:
        task_id = task_info["task_id"]
        if task_id in active_tasks:
            task = active_tasks[task_id]
            manager = task["manager"]
            progress = manager.get_progress()
            
            tasks_progress.append({
                "task_id": task_id,
                "url": task["share_link"],
                "status": task["status"],
                "total": progress.get("total", 0),
                "completed": progress.get("completed", 0),
                "failed": progress.get("failed", 0)
            })
            
            total_completed += progress.get("completed", 0)
            total_failed += progress.get("failed", 0)
    
    return {
        "batch_id": batch_id,
        "status": batch["status"],
        "total_files": batch["total_files"],
        "completed_files": total_completed,
        "failed_files": total_failed,
        "tasks": tasks_progress
    }


@app.post("/api/task/{task_id}/start")
async def start_task(task_id: str, req: ConfirmRequest):
    """开始执行转存任务（全量转存）"""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = active_tasks[task_id]
    
    # 防止重复启动（但允许从 error 状态重新启动）
    if task.get("status") == "running":
        return {"message": "任务已在执行中", "task_id": task_id}
    
    api = task.get("api")
    if not api:
        raise HTTPException(status_code=400, detail="API实例不可用")
    
    task["status"] = "running"
    task["transfer_start_time"] = datetime.now().isoformat()
    task["progress"] = {"phase": "collecting", "dirs_scanned": 0, "files_found": 0, "api_requests": 0}
    task["error"] = ""  # 清除之前的错误信息
    
    # 懒加载模式：先递归收集文件，再分批转存（支持断点续传）
    if task.get("mode") == "lazy":
        surl = task.get("surl", "")
        share_info = task.get("share_info", {})
        share_id = share_info.get("share_id", "")
        uk = share_info.get("uk", "")
        target_path = task.get("target_path", "/我的资源")
        
        # 转存批次大小（每批最多N个文件，避免单次请求过大触发限流）
        # DTS2026061748509 — 分批转存：每批100个文件，批次间3秒间隔防限流
        TRANSFER_BATCH_SIZE = 100
        # 批次间等待秒数（避免连续请求触发限流）
        BATCH_INTERVAL = 3.0
        
        def run_lazy_transfer():
            try:
                # ===== 阶段1：递归收集所有文件 =====
                add_task_log(task_id, f"开始全量转存，surl={surl}")
                logger.info(f"全量转存：开始递归收集文件, surl={surl}")
                api.collect_files_start()
                
                def on_progress(dirs_scanned, files_found, current_dir, api_requests):
                    task["progress"] = {
                        "phase": "collecting",
                        "dirs_scanned": dirs_scanned,
                        "files_found": files_found,
                        "current_dir": current_dir,
                        "api_requests": api_requests,
                        "total": 0,
                        "completed": 0,
                        "speed": 0,
                    }
                
                result = api.collect_files_recursive(surl, "/", progress_callback=on_progress)
                
                if result.get("error"):
                    task["status"] = "error"
                    task["error"] = f"收集文件失败: {result['error']}"
                    add_task_log(task_id, f"收集文件失败: {result['error']}", "ERROR")
                    return
                
                all_files = result.get("files", [])
                dirs_scanned = result.get("dirs_scanned", 0)
                api_requests = result.get("api_requests", 0)
                
                add_task_log(task_id, f"收集完成: {len(all_files)} 个文件, {dirs_scanned} 个目录, {api_requests} 次API请求")
                logger.info(f"全量转存：收集完成, {len(all_files)} 个文件, {dirs_scanned} 个目录, {api_requests} 次API请求")
                
                if not all_files:
                    task["status"] = "completed"
                    task["result"] = {"completed": 0, "failed": 0, "message": "未找到任何文件"}
                    add_task_log(task_id, "未找到任何文件，任务完成", "WARNING")
                    return
                
                # ===== 阶段2：重新 verify 获取新鲜 BDCLND =====
                add_task_log(task_id, "重新验证分享链接（获取新鲜凭证）...")
                logger.info(f"全量转存：重新 verify 获取 BDCLND...")
                task["progress"]["current_action"] = "正在验证分享链接（获取新鲜凭证）..."
                reverify = api.get_share_info(task.get("share_link", ""), task.get("pwd", ""), force_refresh=True)
                if reverify.get("error"):
                    add_task_log(task_id, f"重新 verify 失败: {reverify['error']}，尝试用旧 BDCLND 继续", "WARNING")
                    logger.warning(f"重新 verify 失败: {reverify['error']}，尝试用旧 BDCLND 继续")
                else:
                    add_task_log(task_id, "重新 verify 成功，BDCLND 已刷新")
                    logger.info(f"全量转存：重新 verify 成功，BDCLND 已刷新")
                
                # ===== 阶段3：加载断点（如果有） =====
                # 先从内存加载，如果没有则从DB加载（支持服务器重启后续传）
                checkpoint = task.get("checkpoint", {})
                if not checkpoint or not checkpoint.get("transferred_fs_ids"):
                    # 尝试从DB加载断点（按 share_link + target_path 匹配）
                    try:
                        conn = sqlite3.connect(DB_PATH)
                        c = conn.cursor()
                        c.execute("""
                            SELECT checkpoint FROM tasks 
                            WHERE share_link = ? AND target_path = ? AND checkpoint != '{}' AND checkpoint != ''
                            ORDER BY updated_at DESC LIMIT 1
                        """, (task.get("share_link", ""), task.get("target_path", "")))
                        row = c.fetchone()
                        conn.close()
                        if row and row[0]:
                            checkpoint = json.loads(row[0])
                            task["checkpoint"] = checkpoint
                            logger.info(f"从DB加载断点: {len(checkpoint.get('transferred_fs_ids', []))} 个已转存文件")
                    except Exception as e:
                        logger.warning(f"从DB加载断点失败: {e}")
                
                transferred_fs_ids = set(checkpoint.get("transferred_fs_ids", []))
                start_batch_index = checkpoint.get("last_batch_index", 0)
                
                # 过滤已转存的文件
                remaining_files = []
                for f in all_files:
                    fs_id = f.get("fs_id")
                    if fs_id and fs_id in transferred_fs_ids:
                        continue  # 已转存，跳过
                    remaining_files.append(f)
                
                total_files = len(all_files)
                already_done = total_files - len(remaining_files)
                
                if already_done > 0:
                    add_task_log(task_id, f"断点续传：已跳过 {already_done}/{total_files} 个已转存文件，从第 {start_batch_index + 1} 批开始")
                    logger.info(f"断点续传：跳过 {already_done} 个已转存文件")
                
                # ===== 阶段4：分批转存 =====
                task["progress"] = {
                    "phase": "transferring",
                    "dirs_scanned": dirs_scanned,
                    "files_found": total_files,
                    "api_requests": api_requests,
                    "total": total_files,
                    "completed": already_done,
                    "failed": 0,
                    "speed": 0,
                    "current_batch": start_batch_index,
                    "total_batches": (len(remaining_files) + TRANSFER_BATCH_SIZE - 1) // TRANSFER_BATCH_SIZE + start_batch_index,
                }
                
                add_task_log(task_id, f"开始分批转存: {len(remaining_files)} 个待转存文件, 每批 {TRANSFER_BATCH_SIZE} 个")
                
                completed_count = already_done
                failed_count = 0
                transfer_start_ts = time.time()
                
                for batch_idx, i in enumerate(range(0, len(remaining_files), TRANSFER_BATCH_SIZE)):
                    batch_num = start_batch_index + batch_idx + 1
                    batch = remaining_files[i:i + TRANSFER_BATCH_SIZE]
                    
                    task["progress"]["current_batch"] = batch_num
                    task["progress"]["current_action"] = f"正在转存第 {batch_num} 批 ({len(batch)} 个文件)..."
                    add_task_log(task_id, f"开始第 {batch_num} 批转存: {len(batch)} 个文件")
                    
                    # 构建转存列表
                    transfer_items = [{"path": f.get("path", ""), "fs_id": f.get("fs_id")} for f in batch]
                    
                    # 执行转存（fs_id → path 自动降级）
                    transfer_result = api.transfer_files_with_fallback(share_id, uk, transfer_items, target_path)
                    
                    if transfer_result.get("success"):
                        completed_count += len(batch)
                        # 记录本批成功转存的 fs_id 到断点
                        for f in batch:
                            fs_id = f.get("fs_id")
                            if fs_id:
                                transferred_fs_ids.add(fs_id)
                        add_task_log(task_id, f"第 {batch_num} 批成功: {len(batch)} 个文件")
                    else:
                        error = transfer_result.get("error", "未知错误")
                        errno = transfer_result.get("errno", 0)
                        
                        if errno in (-62, -9):
                            # 限流 — 保存断点后停止
                            add_task_log(task_id, f"第 {batch_num} 批被限流(errno={errno})，保存断点并停止", "ERROR")
                            failed_count += len(batch)
                            task["progress"]["failed"] = failed_count
                            # 保存断点
                            _save_checkpoint(task_id, transferred_fs_ids, batch_num - 1, total_files)
                            task["status"] = "error"
                            task["error"] = f"被限流，已保存断点（已完成 {completed_count}/{total_files}），请稍后重新启动任务继续"
                            _update_task_db(task_id, "error", completed_count, failed_count, total_files,
                                          error=task["error"])
                            return
                        elif errno == 2:
                            # 文件名非法 — 拆成单文件逐个转存，记录具体失败文件
                            add_task_log(task_id, f"第 {batch_num} 批有非法文件名(errno=2)，拆成单文件逐个转存", "WARN")
                            batch_ok = 0
                            batch_fail = 0
                            failed_names = []
                            for f in batch:
                                single_items = [{"path": f.get("path", ""), "fs_id": f.get("fs_id")}]
                                single_result = api.transfer_files_with_fallback(share_id, uk, single_items, target_path)
                                if single_result.get("success"):
                                    batch_ok += 1
                                    fs_id = f.get("fs_id")
                                    if fs_id:
                                        transferred_fs_ids.add(fs_id)
                                else:
                                    batch_fail += 1
                                    single_errno = single_result.get("errno", 0)
                                    single_error = single_result.get("error", "未知")
                                    fname = f.get("path", "").split("/")[-1]
                                    failed_names.append(f"{fname}(errno={single_errno})")
                                    logger.warning(f"单文件转存失败: {f.get('path', '')} errno={single_errno} {single_error}")
                            completed_count += batch_ok
                            failed_count += batch_fail
                            add_task_log(task_id, f"第 {batch_num} 批单文件转存完成: 成功 {batch_ok}, 失败 {batch_fail} (非法文件名)")
                            if failed_names:
                                # 只记录前10个失败文件名，避免日志过大
                                sample = failed_names[:10]
                                add_task_log(task_id, f"失败文件示例: {', '.join(sample)}", "WARN")
                        else:
                            failed_count += len(batch)
                            add_task_log(task_id, f"第 {batch_num} 批失败: {error} (errno={errno})", "ERROR")
                    
                    # 更新进度
                    elapsed = time.time() - transfer_start_ts
                    # DTS2026061748507 — 计算实时转存速度（文件/秒）
                    speed = round(completed_count / elapsed, 1) if elapsed > 0 else 0
                    
                    task["progress"]["completed"] = completed_count
                    task["progress"]["failed"] = failed_count
                    task["progress"]["speed"] = speed
                    
                    # 每批完成后保存断点
                    _save_checkpoint(task_id, transferred_fs_ids, batch_num, total_files)
                    
                    # 批次间等待（避免限流）
                    if i + TRANSFER_BATCH_SIZE < len(remaining_files):
                        time.sleep(BATCH_INTERVAL)
                
                # ===== 阶段5：完成 =====
                task["progress"]["phase"] = "completed"
                task["progress"]["speed"] = round(completed_count / (time.time() - transfer_start_ts), 1) if (time.time() - transfer_start_ts) > 0 else 0
                task["status"] = "completed"
                task["result"] = {
                    "completed": completed_count,
                    "failed": failed_count,
                    "dirs_scanned": dirs_scanned,
                    "api_requests": api_requests
                }
                
                add_task_log(task_id, f"转存完成: 成功 {completed_count}, 失败 {failed_count}, 总耗时 {int(time.time() - transfer_start_ts)}s")
                _update_task_db(task_id, "completed", completed_count, failed_count, total_files)
                # 清除断点（任务已完成）
                _clear_checkpoint(task_id)
                    
            except Exception as e:
                logger.error(f"全量转存异常: {e}")
                add_task_log(task_id, f"转存异常: {e}", "ERROR")
                task["status"] = "error"
                task["error"] = str(e)
                _update_task_db(task_id, "error", 
                              task["progress"].get("completed", 0),
                              task["progress"].get("failed", 0),
                              task["progress"].get("total", 0),
                              error=str(e))
            finally:
                if api and not api.client.is_closed:
                    api.close()
        
        thread = threading.Thread(target=run_lazy_transfer, daemon=True)
        thread.start()
        
        return {
            "message": "全量转存已启动（正在递归收集文件...）",
            "task_id": task_id,
            "total": 0  # 收集完成后前端轮询会获取真实数量
        }
    
    # 旧模式：有 manager，使用 manager.execute_transfer
    manager = task.get("manager")
    if not manager:
        raise HTTPException(status_code=400, detail="任务状态异常，请重新解析")
    
    manager.task_progress["status"] = "ready"
    
    def run_transfer():
        try:
            result = manager.execute_transfer(overwrite_confirmed=req.overwrite)
            
            if result.get("need_confirm"):
                task["status"] = "waiting_confirm"
                task["confirm_data"] = result
                return
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("""
                UPDATE tasks 
                SET status = 'completed', completed_files = ?, failed_files = ?, updated_at = ?
                WHERE id = ?
            """, (result.get("completed", 0), result.get("failed", 0),
                  datetime.now().isoformat(), task_id))
            conn.commit()
            conn.close()
            
            task["status"] = "completed"
            task["result"] = result
            
        except Exception as e:
            task["status"] = "error"
            task["error"] = str(e)
            
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("""
                UPDATE tasks SET status = 'error', error_message = ?, updated_at = ?
                WHERE id = ?
            """, (str(e), datetime.now().isoformat(), task_id))
            conn.commit()
            conn.close()
        finally:
            if api and not api.client.is_closed:
                api.close()
    
    thread = threading.Thread(target=run_transfer, daemon=True)
    thread.start()
    
    return {
        "message": "任务已启动",
        "task_id": task_id,
        "total": manager.task_progress.get("total", 0)
    }


@app.post("/api/task/{task_id}/pause")
async def pause_task(task_id: str):
    """暂停任务"""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = active_tasks[task_id]
    manager = task["manager"]
    manager.pause()
    task["status"] = "paused"
    
    return {"message": "任务已暂停", "task_id": task_id}


@app.post("/api/task/{task_id}/resume")
async def resume_task(task_id: str):
    """恢复任务"""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = active_tasks[task_id]
    manager = task["manager"]
    manager.resume()
    task["status"] = "running"
    
    return {"message": "任务已恢复", "task_id": task_id}


@app.get("/api/task/{task_id}/progress")
async def get_task_progress(task_id: str):
    """获取任务进度（支持懒加载模式和旧模式）"""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = active_tasks[task_id]
    status = task.get("status", "unknown")
    tp = task.get("progress", {})
    
    # 懒加载模式：从 task["progress"] 读取
    if task.get("mode") == "lazy":
        phase = tp.get("phase", "collecting")
        dirs_scanned = tp.get("dirs_scanned", 0)
        files_found = tp.get("files_found", 0)
        api_requests = tp.get("api_requests", 0)
        current_dir = tp.get("current_dir", "")
        speed = tp.get("speed", 0)
        current_batch = tp.get("current_batch", 0)
        total_batches = tp.get("total_batches", 0)
        
        # 构造人类可读的当前状态描述
        if phase == "collecting":
            dir_info = f" → {current_dir}" if current_dir else ""
            current_action = f"正在扫描目录{dir_info}... 已扫描 {dirs_scanned} 个目录，找到 {files_found} 个文件，{api_requests} 次API请求"
        elif phase == "transferring":
            total = tp.get("total", files_found)
            completed = tp.get("completed", 0)
            percent = round(completed / total * 100, 1) if total > 0 else 0
            current_action = f"正在转存: {completed}/{total} ({percent}%), 速度 {speed} 文件/秒, 第 {current_batch}/{total_batches} 批"
        elif phase == "completed":
            current_action = f"转存完成：{files_found} 个文件"
        elif phase == "error":
            current_action = f"转存失败：{task.get('error', '未知错误')}"
        else:
            current_action = f"状态：{phase}"
        
        # 计算已用时间
        elapsed = 0
        start_time = task.get("transfer_start_time")
        if start_time:
            try:
                start_dt = datetime.fromisoformat(start_time)
                elapsed = int((datetime.now() - start_dt).total_seconds())
            except:
                pass
        
        # 转存阶段的进度：total = files_found, completed 由转存结果决定
        total = tp.get("total", files_found) if phase in ("transferring", "completed") else 0
        completed = tp.get("completed", files_found if status == "completed" else 0)
        
        # 获取内存日志（最近50条）
        task_logs = task.get("task_logs", [])
        
        # 断点信息
        checkpoint = task.get("checkpoint", {})
        has_checkpoint = bool(checkpoint.get("transferred_fs_ids"))
        
        return {
            "task_id": task_id,
            "status": status,
            "mode": "lazy",
            "phase": phase,
            "current_action": current_action,
            "current_dir": current_dir,
            "total": total,
            "completed": completed,
            "failed": tp.get("failed", 0),
            "dirs_scanned": dirs_scanned,
            "files_found": files_found,
            "api_requests": api_requests,
            "elapsed": elapsed,
            "speed": speed,
            "current_batch": current_batch,
            "total_batches": total_batches,
            "error": task.get("error", ""),
            "result": task.get("result", {}),
            "transfer_start_time": task.get("transfer_start_time"),
            "confirm_data": task.get("confirm_data"),
            "logs": task_logs[-50:],  # 最近50条日志
            "has_checkpoint": has_checkpoint,
        }
    
    # 旧模式：从 manager 读取
    manager = task.get("manager")
    if not manager:
        return {
            "task_id": task_id,
            "status": status,
            "total": 0,
            "completed": 0,
            "failed": 0,
            "error": task.get("error", ""),
        }
    
    progress = manager.get_progress()
    
    return {
        "task_id": task_id,
        "status": status,
        "total": progress.get("total", 0),
        "completed": progress.get("completed", 0),
        "failed": progress.get("failed", 0),
        "current_batch": progress.get("current_batch", 0),
        "total_batches": progress.get("total_batches", 0),
        "logs": progress.get("logs", [])[-10:],
        "failed_files": progress.get("failed_files", []),
        "confirm_data": task.get("confirm_data"),
        "speed": progress.get("speed", 0),
        "elapsed": progress.get("elapsed", 0),
        "transfer_start_time": task.get("transfer_start_time")
    }


@app.get("/api/tasks")
async def list_tasks():
    """获取历史任务列表"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT 50")
    rows = c.fetchall()
    conn.close()
    
    tasks = []
    for row in rows:
        tasks.append({
            "id": row[0],
            "share_link": row[1],
            "target_path": row[2],
            "status": row[3],
            "total_files": row[4],
            "completed_files": row[5],
            "failed_files": row[6],
            "created_at": row[7],
            "error_message": row[10],
            "batch_id": row[11]
        })
    
    return tasks


@app.get("/api/stats")
async def get_stats():
    """获取统计数据"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM tasks")
    total_tasks = c.fetchone()[0]
    
    c.execute("SELECT SUM(total_files) FROM tasks")
    total_files = c.fetchone()[0] or 0
    
    c.execute("SELECT SUM(completed_files) FROM tasks")
    completed_files = c.fetchone()[0] or 0
    
    c.execute("SELECT SUM(failed_files) FROM tasks")
    failed_files = c.fetchone()[0] or 0
    
    c.execute("SELECT COUNT(*) FROM tasks WHERE status = 'completed'")
    success_tasks = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM tasks WHERE status = 'error'")
    failed_tasks = c.fetchone()[0]
    
    conn.close()
    
    return {
        "total_tasks": total_tasks,
        "success_tasks": success_tasks,
        "failed_tasks": failed_tasks,
        "total_files": total_files,
        "completed_files": completed_files,
        "failed_files": failed_files,
        "success_rate": round(completed_files / total_files * 100, 1) if total_files > 0 else 0
    }


@app.get("/api/limiter/stats")
async def get_limiter_stats():
    """获取限流器状态"""
    from baidu_api import _global_limiter
    stats = _global_limiter.get_stats()
    stats["budget_config"] = {
        "window_seconds": _global_limiter.BUDGET_WINDOW,
        "limit": _global_limiter.BUDGET_LIMIT,
        "qps": _global_limiter.rate,
        "burst": _global_limiter.burst
    }
    return stats


@app.get("/api/task/{task_id}/export")
async def export_task_log(task_id: str):
    """导出任务日志"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return {
        "task_id": row[0],
        "share_link": row[1],
        "target_path": row[2],
        "status": row[3],
        "total_files": row[4],
        "completed_files": row[5],
        "failed_files": row[6],
        "created_at": row[7],
        "logs": json.loads(row[9]) if row[9] else [],
        "error_message": row[10]
    }


@app.delete("/api/tasks/clear")
async def clear_tasks():
    """清空所有任务记录"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM tasks")
    deleted = c.rowcount
    conn.commit()
    conn.close()
    
    # 同时清空内存中的活跃任务
    active_tasks.clear()
    active_batches.clear()
    
    return {"message": f"已清空 {deleted} 条记录", "deleted": deleted}


@app.delete("/api/task/{task_id}")
async def delete_task(task_id: str):
    """删除单个任务记录"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    
    # 同时清理内存
    active_tasks.pop(task_id, None)
    
    if deleted == 0:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    return {"message": "已删除", "task_id": task_id}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
# trigger reload
