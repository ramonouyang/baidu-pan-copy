"""FastAPI 主应用 - 增强版"""
__version__ = "1.1.27"
import json
import time
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

@app.get("/api/version")
async def get_version():
    """返回当前版本号"""
    return {"version": __version__}

@app.post("/api/debug")
async def toggle_debug(request: Request):
    """切换 debug 日志模式"""
    global _debug_mode
    from baidu_api import set_debug_mode
    body = await request.json()
    enabled = body.get("enabled", False)
    _debug_mode = enabled
    set_debug_mode(enabled)
    
    # 记录debug模式切换
    for task_id in active_tasks:
        add_debug_log(task_id, "log.debug_mode", enabled=enabled)
    
    return {"debug": enabled, "message": f"Debug 模式已{'开启' if enabled else '关闭'}"}

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
        ("surl", "TEXT DEFAULT ''"),
        ("pwd", "TEXT DEFAULT ''"),
        ("share_id", "TEXT DEFAULT ''"),
        ("uk", "TEXT DEFAULT ''"),
        ("processed", "BOOLEAN DEFAULT 0"),
    ]:
        try:
            c.execute(f"ALTER TABLE tasks ADD COLUMN {col} {col_def}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()

migrate_db()


# ===== 启动时孤儿任务检测与恢复 =====
def recover_orphan_tasks():
    """服务器启动时检测DB中残留的 running/paused 状态任务，标记为中断"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # 查找所有非终态任务（不包括 ready — ready 表示解析完成、从未转存，不是孤儿）
        c.execute("""
            SELECT id, share_link, target_path, status, checkpoint, completed_files, total_files
            FROM tasks WHERE status IN ('running', 'paused')
        """)
        orphans = c.fetchall()
        conn.close()

        if not orphans:
            logger.info("[启动恢复] 无孤儿任务")
            return

        logger.warning(f"[启动恢复] 发现 {len(orphans)} 个孤儿任务")

        for row in orphans:
            task_id, share_link, target_path, status, checkpoint_json, completed, total = row
            has_checkpoint = False
            checkpoint_count = 0
            try:
                if checkpoint_json and checkpoint_json not in ('{}', ''):
                    cp = json.loads(checkpoint_json)
                    checkpoint_count = len(cp.get("transferred_fs_ids", []))
                    has_checkpoint = checkpoint_count > 0
            except:
                pass

            if has_checkpoint:
                # 有断点 → 标记为可恢复
                new_status = "recoverable"
                error_msg = f"服务器重启中断（已完成 {checkpoint_count} 个文件，可从断点恢复）"
                logger.info(f"[启动恢复] task={task_id} → recoverable (checkpoint={checkpoint_count})")
            else:
                # 无断点 → 标记为错误
                new_status = "error"
                error_msg = "服务器重启导致任务中断"
                logger.info(f"[启动恢复] task={task_id} → error (无断点)")

            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("""
                    UPDATE tasks SET status = ?, error_message = ?, updated_at = ?
                    WHERE id = ?
                """, (new_status, error_msg, datetime.now().isoformat(), task_id))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"[启动恢复] 更新 task={task_id} 失败: {e}")

    except Exception as e:
        logger.error(f"[启动恢复] 扫描孤儿任务失败: {e}")


recover_orphan_tasks()


def add_task_log(task_id, msg, level="INFO", **kwargs):
    """向任务的内存日志缓冲和DB追加一条日志（支持 i18n）
    
    Args:
        task_id: 任务ID
        msg: i18n key（如 "log.started"）或纯文本消息（向后兼容）
        level: 日志级别 (INFO/WARNING/ERROR/SUCCESS)
        **kwargs: i18n 参数（如 surl="xxx", batch_num=3）
    """
    timestamp = datetime.now().strftime("%H:%M:%S")
    # 内存中存结构化对象（前端用）— 携带 i18n key + params
    log_entry = {"time": timestamp, "level": level, "key": msg, "params": kwargs}
    # DB 中存纯文本（导出用）
    log_text = f"[{timestamp}] [{level}] {msg}" + (f" {kwargs}" if kwargs else "")
    
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


def add_debug_log(task_id, msg, **kwargs):
    """添加DEBUG级别日志（仅在debug模式开启时记录）
    
    Args:
        task_id: 任务ID
        msg: i18n key 或纯文本消息
        **kwargs: i18n 参数
    """
    global _debug_mode
    if not _debug_mode:
        return
    
    timestamp = datetime.now().strftime("%H:%M:%S")
    log_entry = {"time": timestamp, "level": "DEBUG", "key": msg, "params": kwargs}
    
    # 写入内存缓冲
    if task_id in active_tasks:
        task = active_tasks[task_id]
        if "task_logs" not in task:
            task["task_logs"] = []
        task["task_logs"].append(log_entry)
        # 限制内存中最多保留200条日志
        if len(task["task_logs"]) > 200:
            task["task_logs"] = task["task_logs"][-200:]
    
    # DEBUG日志不写入DB（避免占用太多空间）
    logger.debug(f"[{task_id}] {msg} {kwargs}")


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
        logger.info(f"[checkpoint] 保存成功: task={task_id}, 已转存={len(transferred_fs_ids)}, 批次={last_batch_index}, 总文件={total_files}")
    except Exception as e:
        logger.error(f"[checkpoint] 保存到DB失败: {e}")


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


def _save_file_list(task_id, file_list):
    """Save collected file list to DB for recovery skip BFS"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE tasks SET file_list = ?, updated_at = ? WHERE id = ?",
                  (json.dumps(file_list, ensure_ascii=False), datetime.now().isoformat(), task_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"save file_list failed: {e}")

def _load_file_list(task_id) -> list:
    """Load collected file list from DB"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT file_list FROM tasks WHERE id = ?", (task_id,))
        row = c.fetchone()
        conn.close()
        if row and row[0] and row[0] not in ('[]', '{}', ''):
            return json.loads(row[0])
    except Exception as e:
        logger.error(f"load file_list failed: {e}")
    return []

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

# Debug模式状态
_debug_mode = False

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
    html = html_path.read_text(encoding="utf-8")
    # 注入版本号，避免前端异步加载延迟
    html = html.replace('__VERSION__', __version__)
    return html


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

    has_stoken = "STOKEN" in req.cookie
    if not has_stoken:
        logger.warning("[cookie] Cookie 中缺少 STOKEN，转存功能可能无法使用")

    _received_cookie["cookie"] = req.cookie
    msg = "Cookie已接收"
    if not has_stoken:
        msg += "（⚠️ 缺少 STOKEN，转存可能失败，请从浏览器补充 STOKEN）"
    return {"ok": True, "message": msg, "has_stoken": has_stoken}


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
    
    # 重复任务检测
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT id, status, completed_files, total_files 
        FROM tasks 
        WHERE share_link = ? AND target_path = ? AND processed = 0
        ORDER BY created_at DESC 
        LIMIT 1
    """, (req.share_link, req.target_path))
    existing_task = c.fetchone()
    conn.close()
    
    if existing_task:
        task_id, status, completed, total = existing_task
        
        # 已完成的任务
        if status == 'completed':
            return JSONResponse(
                status_code=409,
                content={
                    "detail": f"该任务已完成（{completed}/{total}文件），无需重复转存",
                    "error_code": "completed",
                    "existing_task_id": task_id,
                    "completed": completed,
                    "total": total
                }
            )
        
        # 正在运行的任务
        if status in ('running', 'ready', 'pending'):
            return JSONResponse(
                status_code=409,
                content={
                    "detail": f"该任务正在运行中（{status}），请等待完成或取消后再试",
                    "error_code": status,
                    "existing_task_id": task_id,
                    "completed": completed,
                    "total": total
                }
            )
        
        # 可恢复的任务
        if status == 'recoverable':
            return JSONResponse(
                status_code=409,
                content={
                    "detail": f"检测到中断任务 {task_id}（已完成 {completed} 个文件），请先恢复该任务",
                    "error_code": "recoverable",
                    "existing_task_id": task_id,
                    "completed": completed,
                    "total": total
                }
            )
        
        # 暂停的任务
        if status == 'paused':
            return JSONResponse(
                status_code=409,
                content={
                    "detail": f"检测到暂停任务 {task_id}，请先恢复或取消该任务",
                    "error_code": "paused",
                    "existing_task_id": task_id,
                    "completed": completed,
                    "total": total
                }
            )
        
        # error 或 cancelled 状态：允许重新创建，标记旧任务为已处理
        logger.info(f"[重复检测] 发现历史任务 {task_id}（{status}），允许重新创建")
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE tasks SET processed = 1 WHERE id = ?", (task_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[重复检测] 标记旧任务 {task_id} processed 失败: {e}")
    
    # 创建新任务
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
            INSERT INTO tasks (id, share_link, target_path, status, total_files, created_at, updated_at, surl, pwd, share_id, uk)
            VALUES (?, ?, ?, 'ready', ?, ?, ?, ?, ?, ?, ?)
        """, (task_id, req.share_link, req.target_path, 0,
              datetime.now().isoformat(), datetime.now().isoformat(),
              surl, req.pwd or "", share_info.get("share_id", ""), share_info.get("uk", "")))
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
    add_debug_log(task_id, "log.debug_transfer_start", 
                  total_files=len(unique_files), 
                  direct_files=len(direct_files),
                  dir_count=len(dir_paths),
                  api_requests=total_api_requests,
                  target_path=target_path)
    start_time = time.time()
    
    # 分析选中的目录，找出公共前缀，按相对目录分组
    # dir_paths = 用户选中的目录列表（如 ["/labubu合集/拉布布", "/labubu合集/泡泡玛特"]）
    
    pwd = task.get("pwd", "")
    share_link = task.get("share_link", "")
    
    if dir_paths:
        # 找公共前缀：所有选中目录的最长公共路径前缀
        dir_parts_list = [d.strip("/").split("/") for d in dir_paths if d]
        if dir_parts_list:
            min_len = min(len(parts) for parts in dir_parts_list)
            common_prefix_parts = []
            for i in range(min_len):
                part = dir_parts_list[0][i]
                if all(parts[i] == part for parts in dir_parts_list):
                    common_prefix_parts.append(part)
                else:
                    break
            common_prefix = "/" + "/".join(common_prefix_parts) if common_prefix_parts else "/"
        else:
            common_prefix = "/"
        
        logger.info(f"[保留目录结构] 选中 {len(dir_paths)} 个目录, 公共前缀: {common_prefix}")
        
        # 按相对目录分组文件
        dir_groups = {}  # {relative_dir: [file_objects]}
        for f in unique_files:
            file_path = f.get("path", "")
            if not file_path:
                dir_groups.setdefault("/", []).append(f)
                continue
            
            parent_dir = "/".join(file_path.split("/")[:-1]) or "/"
            
            # 计算相对目录
            if common_prefix == "/":
                relative_dir = parent_dir
            elif parent_dir == common_prefix:
                relative_dir = "/"
            elif parent_dir.startswith(common_prefix + "/"):
                relative_dir = parent_dir[len(common_prefix):]
            else:
                # 文件不在公共前缀下（可能来自直接选中的文件）
                relative_dir = parent_dir
            
            dir_groups.setdefault(relative_dir, []).append(f)
        
        logger.info(f"[保留目录结构] 分组完成: {len(dir_groups)} 个目录")
        for rel_dir, files in dir_groups.items():
            logger.info(f"  {rel_dir}: {len(files)} 个文件")
        
        # 按目录分批转存
        total_transferred = 0
        failed_groups = []
        
        for relative_dir, files in dir_groups.items():
            # 构建目标子目录
            if relative_dir == "/":
                target_subdir = target_path
            else:
                rel_path = relative_dir.lstrip("/")
                target_subdir = f"{target_path}/{rel_path}"
            
            # 创建目标子目录
            if api.check_file_exists(target_subdir):
                logger.info(f"目标子目录已存在: {target_subdir}")
            else:
                mkdir_result = api.create_dir(target_subdir)
                if not mkdir_result.get("success") and mkdir_result.get("error_code") != -7:
                    logger.warning(f"创建目标子目录失败: {target_subdir} → {mkdir_result}")
                    failed_groups.append({"dir": relative_dir, "error": mkdir_result.get("error", "创建目录失败")})
                    continue
            
            # 转存该目录下的文件
            logger.info(f"转存 {len(files)} 个文件到 {target_subdir}")
            add_debug_log(task_id, "log.debug_batch_transfer",
                          file_count=len(files),
                          target=target_subdir,
                          relative_dir=relative_dir)
            result = api.transfer_files_with_fallback(share_id, uk, files, target_subdir, pwd, share_link)
            
            add_debug_log(task_id, "log.debug_transfer_result",
                          success=result.get("success", False),
                          errno=result.get("errno", 0),
                          error=result.get("error", ""),
                          transferred=result.get("transferred", 0))
            
            if result.get("success"):
                total_transferred += len(files)
                logger.info(f"✅ {relative_dir}: 成功转存 {len(files)} 个文件")
            else:
                error_code = result.get("errno", 0)
                logger.warning(f"❌ {relative_dir}: 转存失败 - {result.get('error', '未知错误')}")
                failed_groups.append({"dir": relative_dir, "error": result.get("error", "转存失败"), "errno": error_code})
        
        # 汇总结果
        total_files = len(unique_files)
        elapsed = round(time.time() - start_time, 1)
        failed_count = sum(len(g.get("files", [{}])) for g in failed_groups) if failed_groups else 0
        # failed_groups 里存的是目录级别错误，每个失败目录 = 该目录下所有文件失败
        # 但目前我们没有精确统计每个失败目录的文件数，用总数减去成功数更准确
        failed_count = total_files - total_transferred
        
        if total_transferred > 0:
            msg = f"📊 转存总结: 计划{total_files}个, 成功{total_transferred}个"
            if failed_count > 0:
                msg += f", 失败{failed_count}个"
            msg += f"（保留{len(dir_groups)}个目录结构）"
            return {
                "success": True,
                "message": msg,
                "count": total_transferred,
                "total_planned": total_files,
                "success_count": total_transferred,
                "failed_count": failed_count,
                "dirs_preserved": len(dir_groups),
                "failed_groups": failed_groups,
                "api_requests": total_api_requests,
                "elapsed": elapsed
            }
        else:
            raise HTTPException(status_code=400, detail=f"所有目录转存失败: {[g['error'] for g in failed_groups]}")
    
    else:
        # 没有选中目录（只有直接选中的文件），按原来逻辑转存到 target_path
        if api.check_file_exists(target_path):
            logger.info(f"目标目录已存在，跳过创建: {target_path}")
        else:
            mkdir_result = api.create_dir(target_path)
            if not mkdir_result.get("success") and mkdir_result.get("error_code") != -7:
                logger.warning(f"创建目标目录失败: {mkdir_result}")
        
        result = api.transfer_files_with_fallback(share_id, uk, unique_files, target_path, pwd, share_link)
        elapsed = round(time.time() - start_time, 1)
        
        if result.get("success"):
            total_files = len(unique_files)
            return {
                "success": True,
                "message": f"成功转存 {total_files} 个文件",
                "count": total_files,
                "total_planned": total_files,
                "success_count": total_files,
                "failed_count": 0,
                "dirs_preserved": 0,
                "failed_groups": [],
                "api_requests": total_api_requests,
                "elapsed": elapsed
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
    _update_task_db(task_id, "running", 0, 0, 0)  # 同步状态到 DB
    
    # 懒加载模式：流水线收集+转存（收集一批→转存一批→再收集下一批）
    if task.get("mode") == "lazy":
        surl = task.get("surl", "")
        share_info = task.get("share_info", {})
        share_id = share_info.get("share_id", "")
        uk = share_info.get("uk", "")
        target_path = task.get("target_path", "/我的资源")
        
        # 每批文件数（与转存API上限对齐）
        BATCH_SIZE = 100
        
        def run_lazy_transfer():
            try:
                pwd = task.get("pwd", "")
                share_link = task.get("share_link", "")
                
                # ===== 阶段1：创建目标目录 =====
                add_task_log(task_id, "log.started", surl=surl)
                logger.info(f"流水线转存：开始, surl={surl}, target={target_path}, batch_size={BATCH_SIZE}")
                

                task["progress"] = {"phase": "collecting", "dirs_scanned": 0, "files_found": 0, "api_requests": 0}
                task["progress"]["current_action"] = "正在创建目标目录..."
                if api.check_file_exists(target_path):
                    logger.info(f"目标目录已存在，跳过创建: {target_path}")
                    add_task_log(task_id, "log.dir_exists", path=target_path)
                else:
                    mkdir_result = api.create_dir(target_path)
                    if not mkdir_result.get("success") and "error" in mkdir_result:
                        logger.warning(f"创建目标目录: {mkdir_result.get('error', '')}")
                    add_task_log(task_id, "log.target_dir", path=target_path)
                
                # ===== 阶段2：加载断点 =====
                checkpoint = task.get("checkpoint", {})
                if not checkpoint or not checkpoint.get("transferred_fs_ids"):
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
                completed_count = len(transferred_fs_ids)
                failed_count = 0
                transfer_start_ts = time.time()
                dirs_scanned = 0
                
                if completed_count > 0:
                    add_task_log(task_id, "log.checkpoint", count=completed_count)
                
                # ===== 阶段2.5：验证 cookie 有效性 =====
                add_task_log(task_id, "log.validating_cookie")
                cookie_check = api.validate_cookie()
                if not cookie_check.get("valid"):
                    error_msg = cookie_check.get("error", "未知原因")
                    add_task_log(task_id, "log.cookie_expired", "ERROR", error=error_msg)
                    task["status"] = "error"
                    task["error"] = f"Cookie 已失效: {error_msg}，请重新设置 cookie"
                    _update_task_db(task_id, "error", 0, 0, 0, error=task["error"])
                    return
                add_task_log(task_id, "log.cookie_valid", user=cookie_check.get('username', 'unknown'))

                # ===== 阶段3：流水线 — 收集一批 → 转存一批 =====
                add_task_log(task_id, "log.pipeline_start", batch_size=BATCH_SIZE)
                
                accumulated_file_list = []  # 累积文件列表，用于持久化
                
                for batch in api.collect_files_batch(surl, batch_size=BATCH_SIZE):
                    while task.get("paused"):
                        time.sleep(1)
                    
                    if task.get("cancelled"):
                        add_task_log(task_id, "log.cancel_stopped", "WARNING")
                        return
                    
                    files = batch["files"]
                    dirs_scanned = batch["dirs_scanned"]
                    files_found = batch["files_found"]
                    api_requests = batch["api_requests"]
                    batch_num = batch["batch_num"]
                    error = batch.get("error")
                    
                    # 错误处理
                    if error:
                        add_task_log(task_id, "log.collect_failed", "ERROR", batch=batch_num, error=error)
                        task["status"] = "error"
                        task["error"] = f"收集失败: {error}"
                        _save_checkpoint(task_id, transferred_fs_ids, batch_num, files_found)
                        _update_task_db(task_id, "error", completed_count, failed_count, files_found, error=task["error"])
                        return
                    
                    # 持久化文件列表（用于恢复时跳过BFS）
                    accumulated_file_list.extend(files)
                    if batch_num % 5 == 0:  # 每5批保存一次，减少DB写入频率
                        _save_file_list(task_id, accumulated_file_list)
                    
                    # 过滤已转存文件
                    remaining = [f for f in files if f.get("fs_id") and f["fs_id"] not in transferred_fs_ids]
                    skipped = len(files) - len(remaining)
                    
                    # 更新进度
                    task["progress"] = {
                        "phase": "pipeline",
                        "dirs_scanned": dirs_scanned,
                        "files_found": files_found,
                        "api_requests": api_requests,
                        "total": files_found,
                        "completed": completed_count,
                        "failed": failed_count,
                        "speed": 0,
                        "current_action": f"第{batch_num}批: 收集{len(files)}文件, 跳过{skipped}, 待转存{len(remaining)}",
                    }
                    
                    if not remaining:
                        logger.info(f"[流水线] 第{batch_num}批: {len(files)}文件全部已转存, 跳过")
                        continue
                    
                    # ===== 转存这批文件 =====
                    add_task_log(task_id, "log.batch_info", batch=batch_num, total=len(files), skipped=skipped, remaining=len(remaining))
                    
                    transfer_items = [{"path": f.get("path", ""), "fs_id": f.get("fs_id")} for f in remaining]
                    
                    # 按文件的父目录分组，为每个子目录创建对应的目标路径
                    dir_groups = {}
                    for item in transfer_items:
                        file_path = item.get("path", "")
                        if file_path:
                            parent_dir = "/".join(file_path.split("/")[:-1]) or "/"
                        else:
                            parent_dir = "/"
                        # 计算相对于分享根目录的路径
                        # 分享根目录通常是 "/" 或用户选中的目录
                        # 这里直接用 parent_dir 作为相对路径
                        dir_groups.setdefault(parent_dir, []).append(item)
                    
                    add_debug_log(task_id, "log.debug_batch_transfer",
                                  file_count=len(remaining),
                                  batch_num=batch_num,
                                  dir_count=len(dir_groups))
                    
                    batch_success = 0
                    batch_failed = 0
                    
                    for parent_dir, group_items in dir_groups.items():
                        # 构建目标子目录
                        if parent_dir == "/":
                            target_subdir = target_path
                        else:
                            # 去掉开头的 "/"，拼接到 target_path
                            rel_path = parent_dir.lstrip("/")
                            target_subdir = f"{target_path}/{rel_path}"
                        
                        # 创建目标子目录（如果不存在）
                        if not api.check_file_exists(target_subdir):
                            mkdir_result = api.create_dir(target_subdir)
                            if not mkdir_result.get("success") and mkdir_result.get("error_code") != -7:
                                logger.warning(f"创建目标子目录失败: {target_subdir} → {mkdir_result}")
                        
                        transfer_result, should_return = _retry_on_rate_limit(
                            lambda: api.transfer_files_with_fallback(share_id, uk, group_items, target_subdir, pwd, share_link),
                            task_id, task, transferred_fs_ids,
                            batch_num, completed_count, failed_count, files_found
                        )
                        if should_return:
                            return
                        
                        if transfer_result.get("success"):
                            batch_success += len(group_items)
                            for f in remaining:
                                fs_id = f.get("fs_id")
                                if fs_id:
                                    transferred_fs_ids.add(fs_id)
                            # 实时更新内存计数器
                            task["realtime"] = {
                                "success": completed_count + batch_success,
                                "failed": failed_count + batch_failed,
                                "total": files_found,
                                "elapsed": round(time.time() - transfer_start_ts, 1),
                            }
                        else:
                            error = transfer_result.get("error", "未知错误")
                            errno = transfer_result.get("errno", 0)
                            batch_failed += len(group_items)
                            
                            if errno in (-3, -4):
                                add_task_log(task_id, "log.batch_cookie_expired", "ERROR", batch=batch_num, errno=errno)
                                _save_checkpoint(task_id, transferred_fs_ids, batch_num, files_found)
                                task["status"] = "error"
                                task["error"] = f"Cookie 已失效，已保存断点（已完成 {completed_count}/{files_found}），请重新设置 cookie 后重启"
                                _update_task_db(task_id, "error", completed_count, failed_count + batch_failed, files_found, error=task["error"])
                                return
                            elif errno == -404:
                                # CDN 404 持续故障 — 可能是分享链接过期或 CDN 问题
                                add_task_log(task_id, "log.batch_cdn_404", "ERROR", batch=batch_num, error=error)
                                _save_checkpoint(task_id, transferred_fs_ids, batch_num, files_found)
                                task["status"] = "paused"
                                task["error"] = f"CDN 404 持续故障，已保存断点（已完成 {completed_count}/{files_found}）。可能原因：1) 分享链接已过期 2) CDN 临时故障。请检查分享链接是否有效后重新启动"
                                _update_task_db(task_id, "paused", completed_count, failed_count + batch_failed, files_found, error=task["error"])
                                return
                            elif errno in (2, 12, 1504):
                                # 文件名非法或过长 — 逐个转存
                                add_task_log(task_id, "log.batch_errno2", "WARN", batch=batch_num, error=error)
                                for f in remaining:
                                    sr = api.transfer_files_with_fallback(share_id, uk, [{"path": f.get("path",""), "fs_id": f.get("fs_id")}], target_subdir, pwd, share_link)
                                    if sr.get("success"):
                                        completed_count += 1
                                        fs_id = f.get("fs_id")
                                        if fs_id:
                                            transferred_fs_ids.add(fs_id)
                                    else:
                                        failed_count += 1
                                        fname = f.get("path","").split("/")[-1]
                                        logger.warning(f"[流水线] 单文件失败: {fname} errno={sr.get('errno','?')}")
                                        add_task_log(task_id, "log.single_file_failed", "WARN", name=fname, errno=sr.get('errno', '?'))
                                    # 单文件实时更新
                                    task["realtime"] = {
                                        "success": completed_count,
                                        "failed": failed_count,
                                        "total": files_found,
                                        "elapsed": round(time.time() - transfer_start_ts, 1),
                                    }
                                    # 每10个文件同步进度到DB
                                    if (completed_count + failed_count) % 10 == 0:
                                        _update_task_db(task_id, "running", completed_count, failed_count, files_found)
                            else:
                                add_task_log(task_id, "log.batch_failed", "ERROR", batch=batch_num, error=error, errno=errno)
                    
                    completed_count += batch_success
                    failed_count += batch_failed
                    
                    if batch_success > 0:
                        logger.info(f"[流水线] 第{batch_num}批: 转存成功 {batch_success} 文件 (保留目录结构)")
                    
                    # 每批转存完保存断点
                    _save_checkpoint(task_id, transferred_fs_ids, batch_num, files_found)
                    
                    # 实时更新内存计数器
                    elapsed = time.time() - transfer_start_ts
                    task["realtime"] = {
                        "success": completed_count,
                        "failed": failed_count,
                        "total": files_found,
                        "elapsed": round(elapsed, 1),
                    }
                    speed = round(completed_count / elapsed, 1) if elapsed > 0 else 0
                    task["progress"].update({
                        "completed": completed_count,
                        "failed": failed_count,
                        "speed": speed,
                        "current_action": f"第{batch_num}批完成, 继续收集...",
                    })
                    # 每批完成同步进度到DB
                    _update_task_db(task_id, "running", completed_count, failed_count, files_found)
                
                # ===== 阶段4：完成 =====
                total_elapsed = time.time() - transfer_start_ts
                task["progress"]["phase"] = "completed"
                task["progress"]["speed"] = round(completed_count / total_elapsed, 1) if total_elapsed > 0 else 0
                task["status"] = "completed"
                task["result"] = {
                    "completed": completed_count,
                    "failed": failed_count,
                    "dirs_scanned": dirs_scanned,
                    "api_requests": api._collect_stats.get("api_requests", 0),
                }
                
                total_planned = completed_count + failed_count
                skipped_count = len(transferred_fs_ids) - completed_count  # 断点续传跳过的
                add_task_log(task_id, "log.summary_title")
                add_task_log(task_id, "log.summary_planned", count=files_found)
                add_task_log(task_id, "log.summary_success", count=completed_count)
                if skipped_count > 0:
                    add_task_log(task_id, "log.summary_skipped", count=skipped_count)
                add_task_log(task_id, "log.summary_failed", count=failed_count)
                add_task_log(task_id, "log.summary_elapsed", seconds=int(total_elapsed))
                
                task["transfer_summary"] = {
                    "total_planned": files_found,
                    "success_count": completed_count,
                    "failed_count": failed_count,
                    "skipped_count": skipped_count,
                    "elapsed": int(total_elapsed),
                    "dirs_scanned": dirs_scanned,
                    "api_requests": api._collect_stats.get("api_requests", 0),
                    "failed_files": task["progress"].get("failed_files", []),
                }
                
                _update_task_db(task_id, "completed", completed_count, failed_count, completed_count + failed_count)
                _clear_checkpoint(task_id)
                    
            except Exception as e:
                logger.error(f"流水线转存异常: {e}")
                add_task_log(task_id, "log.transfer_error", "ERROR", error=str(e))
                task["status"] = "error"
                task["error"] = str(e)
                _update_task_db(task_id, "error", 
                              task["progress"].get("completed", 0),
                              task["progress"].get("failed", 0),
                              task["progress"].get("files_found", 0),
                              error=str(e))
            finally:
                if api and not api.client.is_closed:
                    api.close()
        
        thread = threading.Thread(target=run_lazy_transfer, daemon=True)
        thread.start()
        
        return {
            "message": "流水线转存已启动（收集→转存→收集→转存...）",
            "task_id": task_id,
            "total": 0
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
    task["paused"] = True
    
    # 旧模式：有 manager
    manager = task.get("manager")
    if manager:
        manager.pause()
    
    task["status"] = "paused"
    _update_task_db(task_id, "paused", 0, 0, 0)  # 同步状态到 DB
    add_task_log(task_id, "log.paused", "INFO")
    return {"message": "任务已暂停", "task_id": task_id}


@app.post("/api/task/{task_id}/resume")
async def resume_task(task_id: str):
    """恢复任务"""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = active_tasks[task_id]
    task["paused"] = False
    
    # 旧模式：有 manager
    manager = task.get("manager")
    if manager:
        manager.resume()
    
    task["status"] = "running"
    _update_task_db(task_id, "running", 0, 0, 0)  # 同步状态到 DB
    add_task_log(task_id, "log.resumed", "INFO")
    return {"message": "任务已恢复", "task_id": task_id}


@app.post("/api/task/{task_id}/cancel")
async def cancel_task(task_id: str):
    """取消任务（停止后续转存，已转存的文件保留）"""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = active_tasks[task_id]
    
    # 设置取消标志
    task["cancelled"] = True
    
    # 如果有manager，调用cancel方法
    manager = task.get("manager")
    if manager:
        manager.cancel()
    
    # 更新状态
    task["status"] = "cancelled"
    _update_task_db(task_id, "cancelled", 0, 0, 0)
    
    # 记录日志
    add_task_log(task_id, "log.cancelled", "WARNING")
    add_task_log(task_id, "log.cancel_warning", "WARNING")
    
    return {
        "message": "任务已取消",
        "task_id": task_id,
        "warning": "已转存的文件需要手动删除，百度网盘不支持批量撤销"
    }


@app.post("/api/task/{task_id}/recover")
async def recover_task(task_id: str, request: Request):
    """从断点恢复孤儿任务（服务器重启后的任务）
    
    请求体：{"cookie": "..."} — 可选，不传则使用最近收到的 cookie
    """
    # 从 DB 读取任务信息
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT id, share_link, target_path, status, checkpoint, 
                   completed_files, failed_files, total_files, error_message,
                   surl, pwd, share_id, uk
            FROM tasks WHERE id = ?
        """, (task_id,))
        row = c.fetchone()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询任务失败: {str(e)}")
    
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    tid, share_link, target_path, status, checkpoint_json, completed, failed, total, error_msg, \
        surl, pwd, share_id, uk = row
    
    # 只允许恢复 recoverable 或 error 状态的任务
    if status not in ("recoverable", "error"):
        raise HTTPException(status_code=400, detail=f"任务状态为 {status}，无法恢复")
    
    # 解析 checkpoint
    checkpoint = {}
    try:
        if checkpoint_json and checkpoint_json not in ('{}', ''):
            checkpoint = json.loads(checkpoint_json)
    except:
        pass
    
    checkpoint_count = len(checkpoint.get("transferred_fs_ids", []))
    
    # 获取 cookie
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    cookie = body.get("cookie") or _received_cookie.get("cookie")
    
    if not cookie:
        raise HTTPException(status_code=400, detail="需要提供 cookie（通过请求体或书签小工具）")
    
    # 从 DB 读取 surl，如果为空则从 share_link 提取
    if not surl:
        if "surl=" in share_link:
            import re
            match = re.search(r'surl=([^&]+)', share_link)
            if match:
                surl = match.group(1)
            if not pwd:
                pwd_match = re.search(r'pwd=([^&]+)', share_link)
                if pwd_match:
                    pwd = pwd_match.group(1)
    
    if not surl:
        raise HTTPException(status_code=400, detail=f"无法从 share_link 提取 surl: {share_link}")
    
    # 创建 API 实例
    api = BaiduPanAPI(cookie)
    
    # 验证 cookie
    cookie_check = api.validate_cookie()
    if not cookie_check.get("valid"):
        api.close()
        raise HTTPException(status_code=400, detail=f"Cookie 已失效: {cookie_check.get('error', '未知原因')}")
    
    # 如果 DB 中没有 share_id/uk，才重新获取
    if not share_id or not uk:
        logger.info(f"[恢复] DB中无 share_id/uk，重新获取: surl={surl}")
        share_info_result = api.get_share_info(surl)
        if "error" in share_info_result:
            api.close()
            raise HTTPException(status_code=400, detail=f"获取分享信息失败: {share_info_result['error']}")
        share_id = share_info_result.get("share_id", "")
        uk = share_info_result.get("uk", "")
        # 更新到 DB
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE tasks SET share_id = ?, uk = ? WHERE id = ?", (share_id, uk, task_id))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[恢复] 更新 share_id/uk 到DB失败: {e}")
    
    # 重建 active_tasks 条目
    active_tasks[task_id] = {
        "api": api,
        "cookie": cookie,
        "share_link": share_link,
        "pwd": pwd or "",
        "share_info": {
            "share_id": share_id,
            "uk": uk,
            "title": "",
        },
        "surl": surl,
        "target_path": target_path,
        "status": "running",
        "mode": "lazy",
        "checkpoint": checkpoint,
        "transfer_start_time": datetime.now().isoformat(),
        "progress": {"phase": "collecting", "dirs_scanned": 0, "files_found": 0, "api_requests": 0},
        "task_logs": [],
        "error": "",
    }
    
    task = active_tasks[task_id]
    BATCH_SIZE = 100
    
    def _recovery_transfer_from_file_list(task_id, task, api, file_list, transferred_fs_ids,
                                           completed_count, failed_count, transfer_start_ts,
                                           share_id, uk, target_path, pwd, share_link, surl, total_files):
        """从已保存的文件列表直接转存（跳过BFS收集）"""
        remaining = [f for f in file_list if f.get("fs_id") and f["fs_id"] not in transferred_fs_ids]
        skipped = len(file_list) - len(remaining)
        logger.info(f"[恢复转存] 总文件={len(file_list)}, 已转存={len(transferred_fs_ids)}, 跳过={skipped}, 待转存={len(remaining)}")
        add_task_log(task_id, "log.recovery_transfer_start", total=len(file_list), skipped=skipped, remaining=len(remaining))
        
        batch_num = 0
        for i in range(0, len(remaining), BATCH_SIZE):
            batch_files = remaining[i:i + BATCH_SIZE]
            batch_num += 1
            
            while task.get("paused"):
                time.sleep(1)
            if task.get("cancelled"):
                add_task_log(task_id, "log.cancel_stopped", "WARNING")
                return
            
            # 按目录分组
            dir_groups = {}
            for f in batch_files:
                parent_dir = "/".join(f.get("path", "").split("/")[:-1]) or "/"
                dir_groups.setdefault(parent_dir, []).append({"path": f.get("path", ""), "fs_id": f.get("fs_id")})
            
            batch_success = 0
            batch_failed = 0
            
            for parent_dir, group_items in dir_groups.items():
                target_subdir = target_path if parent_dir == "/" else f"{target_path}/{parent_dir.lstrip('/')}"
                
                if not api.check_file_exists(target_subdir):
                    mkdir_result = api.create_dir(target_subdir)
                    if not mkdir_result.get("success") and mkdir_result.get("error_code") != -7:
                        logger.warning(f"创建目标子目录失败: {target_subdir}")
                
                transfer_result, should_return = _retry_on_rate_limit(
                    lambda: api.transfer_files_with_fallback(share_id, uk, group_items, target_subdir, pwd, share_link),
                    task_id, task, transferred_fs_ids,
                    batch_num, completed_count, failed_count, total_files
                )
                if should_return:
                    return
                
                if transfer_result.get("success"):
                    batch_success += len(group_items)
                    for f in batch_files:
                        fs_id = f.get("fs_id")
                        if fs_id:
                            transferred_fs_ids.add(fs_id)
                else:
                    errno = transfer_result.get("errno", 0)
                    batch_failed += len(group_items)
                    error = transfer_result.get("error", "未知错误")
                    
                    if errno in (-3, -4):
                        add_task_log(task_id, "log.batch_cookie_expired", "ERROR", batch=batch_num, errno=errno)
                        _save_checkpoint(task_id, transferred_fs_ids, batch_num, total_files)
                        task["status"] = "error"
                        task["error"] = f"Cookie 已失效，请重新设置 cookie"
                        _update_task_db(task_id, "error", completed_count, failed_count + batch_failed, total_files, error=task["error"])
                        return
                    elif errno == -404:
                        add_task_log(task_id, "log.batch_cdn_404", "ERROR", batch=batch_num, error=error)
                        _save_checkpoint(task_id, transferred_fs_ids, batch_num, total_files)
                        task["status"] = "paused"
                        task["error"] = f"CDN 404 持续故障，已保存断点（已完成 {completed_count}/{total_files}）。请检查分享链接是否有效后重新启动"
                        _update_task_db(task_id, "paused", completed_count, failed_count + batch_failed, total_files, error=task["error"])
                        return
                    elif errno in (2, 12, 1504):
                        add_task_log(task_id, "log.batch_errno2", "WARN", batch=batch_num, error=error)
                        for f in batch_files:
                            sr = api.transfer_files_with_fallback(share_id, uk, [{"path": f.get("path",""), "fs_id": f.get("fs_id")}], target_subdir, pwd, share_link)
                            if sr.get("success"):
                                completed_count += 1
                                fs_id = f.get("fs_id")
                                if fs_id:
                                    transferred_fs_ids.add(fs_id)
                            else:
                                failed_count += 1
                    else:
                        add_task_log(task_id, "log.batch_failed", "ERROR", batch=batch_num, error=error, errno=errno)
            
            completed_count += batch_success
            failed_count += batch_failed
            
            _save_checkpoint(task_id, transferred_fs_ids, batch_num, total_files)
            elapsed = time.time() - transfer_start_ts
            task["realtime"] = {"success": completed_count, "failed": failed_count, "total": total_files, "elapsed": round(elapsed, 1)}
            if (completed_count + failed_count) % 10 == 0:
                _update_task_db(task_id, "running", completed_count, failed_count, total_files)
            
            if batch_success > 0:
                logger.info(f"[恢复转存] 第{batch_num}批: 成功 {batch_success}, 失败 {batch_failed}")
        
        # 完成
        total_elapsed = time.time() - transfer_start_ts
        task["status"] = "completed"
        _update_task_db(task_id, "completed", completed_count, failed_count, total_files)
        _clear_checkpoint(task_id)
        add_task_log(task_id, "log.completed", completed=completed_count, failed=failed_count, elapsed=int(total_elapsed))
        logger.info(f"[恢复转存] 完成: 成功={completed_count}, 失败={failed_count}, 耗时={int(total_elapsed)}s")
    
    def run_lazy_transfer():
        try:
            add_task_log(task_id, "log.recovered", surl=surl, checkpoint_count=checkpoint_count)
            logger.info(f"[恢复] 任务 {task_id} 从断点恢复: surl={surl}, checkpoint={checkpoint_count}")
            
            # 创建目标目录
            if not api.check_file_exists(target_path):
                api.create_dir(target_path)
            
            # 加载断点
            transferred_fs_ids = set(checkpoint.get("transferred_fs_ids", []))
            completed_count = len(transferred_fs_ids)
            failed_count = 0
            transfer_start_ts = time.time()
            
            if completed_count > 0:
                add_task_log(task_id, "log.checkpoint", count=completed_count)
            
            # 验证 cookie
            add_task_log(task_id, "log.validating_cookie")
            cookie_check = api.validate_cookie()
            if not cookie_check.get("valid"):
                add_task_log(task_id, "log.cookie_expired", "ERROR", error=cookie_check.get('error', ''))
                task["status"] = "error"
                task["error"] = f"Cookie 已失效，请重新设置 cookie"
                _update_task_db(task_id, "error", completed_count, 0, 0, error=task["error"])
                return
            add_task_log(task_id, "log.cookie_valid", user=cookie_check.get('username', 'unknown'))
            
            # 尝试从DB加载已收集的文件列表（跳过BFS）
            saved_file_list = _load_file_list(task_id)
            
            if saved_file_list:
                # 有已保存的文件列表，跳过BFS直接转存
                add_task_log(task_id, "log.recovery_skip_bfs", count=len(saved_file_list))
                logger.info(f"[恢复] 从DB加载文件列表: {len(saved_file_list)} 个文件，跳过BFS")
                
                _recovery_transfer_from_file_list(
                    task_id, task, api, saved_file_list, transferred_fs_ids,
                    completed_count, failed_count, transfer_start_ts,
                    share_id, uk, target_path, pwd, share_link, surl, files_found
                )
                return
            
            # 没有已保存的文件列表，走BFS收集流程
            add_task_log(task_id, "log.recovery_bfs_fallback")
            logger.info(f"[恢复] 无已保存文件列表，走BFS收集流程")
            
            # 流水线收集+转存
            add_task_log(task_id, "log.pipeline_start", batch_size=BATCH_SIZE)
            
            accumulated_file_list = []  # 累积文件列表，用于持久化
            
            for batch in api.collect_files_batch(surl, batch_size=BATCH_SIZE):
                while task.get("paused"):
                    time.sleep(1)
                
                if task.get("cancelled"):
                    add_task_log(task_id, "log.cancel_stopped", "WARNING")
                    return
                
                files = batch["files"]
                dirs_scanned = batch["dirs_scanned"]
                files_found = batch["files_found"]
                batch_num = batch["batch_num"]
                error = batch.get("error")
                
                if error:
                    add_task_log(task_id, "log.collect_failed", "ERROR", batch=batch_num, error=error)
                    task["status"] = "error"
                    task["error"] = f"收集失败: {error}"
                    _save_checkpoint(task_id, transferred_fs_ids, batch_num, files_found)
                    _update_task_db(task_id, "error", completed_count, failed_count, files_found, error=task["error"])
                    return
                
                # 持久化文件列表（用于下次恢复时跳过BFS）
                accumulated_file_list.extend(files)
                if batch_num % 5 == 0:
                    _save_file_list(task_id, accumulated_file_list)
                
                remaining = [f for f in files if f.get("fs_id") and f["fs_id"] not in transferred_fs_ids]
                skipped = len(files) - len(remaining)
                
                task["progress"] = {
                    "phase": "pipeline",
                    "dirs_scanned": dirs_scanned,
                    "files_found": files_found,
                    "total": files_found,
                    "completed": completed_count,
                    "failed": failed_count,
                    "current_action": f"第{batch_num}批: 收集{len(files)}文件, 跳过{skipped}, 待转存{len(remaining)}",
                }
                
                if not remaining:
                    continue
                
                add_task_log(task_id, "log.batch_info", batch=batch_num, total=len(files), skipped=skipped, remaining=len(remaining))
                
                transfer_items = [{"path": f.get("path", ""), "fs_id": f.get("fs_id")} for f in remaining]
                dir_groups = {}
                for item in transfer_items:
                    file_path = item.get("path", "")
                    parent_dir = "/".join(file_path.split("/")[:-1]) or "/" if file_path else "/"
                    dir_groups.setdefault(parent_dir, []).append(item)
                
                batch_success = 0
                batch_failed = 0
                
                for parent_dir, group_items in dir_groups.items():
                    target_subdir = target_path if parent_dir == "/" else f"{target_path}/{parent_dir.lstrip('/')}"
                    
                    if not api.check_file_exists(target_subdir):
                        mkdir_result = api.create_dir(target_subdir)
                        if not mkdir_result.get("success") and mkdir_result.get("error_code") != -7:
                            logger.warning(f"创建目标子目录失败: {target_subdir}")
                    
                    transfer_result, should_return = _retry_on_rate_limit(
                        lambda: api.transfer_files_with_fallback(share_id, uk, group_items, target_subdir, pwd, share_link),
                        task_id, task, transferred_fs_ids,
                        batch_num, completed_count, failed_count, files_found
                    )
                    if should_return:
                        return
                    
                    if transfer_result.get("success"):
                        batch_success += len(group_items)
                        for f in remaining:
                            fs_id = f.get("fs_id")
                            if fs_id:
                                transferred_fs_ids.add(fs_id)
                    else:
                        error = transfer_result.get("error", "未知错误")
                        errno = transfer_result.get("errno", 0)
                        batch_failed += len(group_items)
                        
                        if errno in (-3, -4):
                            add_task_log(task_id, "log.batch_cookie_expired", "ERROR", batch=batch_num, errno=errno)
                            _save_checkpoint(task_id, transferred_fs_ids, batch_num, files_found)
                            task["status"] = "error"
                            task["error"] = f"Cookie 已失效，请重新设置 cookie"
                            _update_task_db(task_id, "error", completed_count, failed_count + batch_failed, files_found, error=task["error"])
                            return
                        elif errno == -404:
                            # CDN 404 持续故障 — 可能是分享链接过期或 CDN 问题
                            error = transfer_result.get("error", "CDN 404 错误")
                            add_task_log(task_id, "log.batch_cdn_404", "ERROR", batch=batch_num, error=error)
                            _save_checkpoint(task_id, transferred_fs_ids, batch_num, files_found)
                            task["status"] = "paused"
                            task["error"] = f"CDN 404 持续故障，已保存断点（已完成 {completed_count}/{files_found}）。请检查分享链接是否有效后重新启动"
                            _update_task_db(task_id, "paused", completed_count, failed_count + batch_failed, files_found, error=task["error"])
                            return
                        elif errno in (2, 12, 1504):
                            # 文件名非法或过长 — 逐个转存
                            add_task_log(task_id, "log.batch_errno2", "WARN", batch=batch_num, error=error)
                            for f in remaining:
                                sr = api.transfer_files_with_fallback(share_id, uk, [{"path": f.get("path",""), "fs_id": f.get("fs_id")}], target_subdir, pwd, share_link)
                                if sr.get("success"):
                                    completed_count += 1
                                    fs_id = f.get("fs_id")
                                    if fs_id:
                                        transferred_fs_ids.add(fs_id)
                                else:
                                    failed_count += 1
                                    fname = f.get("path","").split("/")[-1]
                                    logger.warning(f"[selected] 单文件失败: {fname} errno={sr.get('errno','?')}")
                                    add_task_log(task_id, "log.single_file_failed", "WARN", name=fname, errno=sr.get('errno', '?'))
                        else:
                            add_task_log(task_id, "log.batch_failed", "ERROR", batch=batch_num, error=error, errno=errno)
                
                completed_count += batch_success
                failed_count += batch_failed
                _save_checkpoint(task_id, transferred_fs_ids, batch_num, files_found)
                _update_task_db(task_id, "running", completed_count, failed_count, files_found)
            
            # 完成
            total_elapsed = time.time() - transfer_start_ts
            task["status"] = "completed"
            task["progress"]["phase"] = "completed"
            _update_task_db(task_id, "completed", completed_count, failed_count, completed_count + failed_count)
            _clear_checkpoint(task_id)
            add_task_log(task_id, "log.completed", completed=completed_count, failed=failed_count, elapsed=int(total_elapsed))
            
        except Exception as e:
            logger.error(f"[恢复] 任务异常: {e}")
            add_task_log(task_id, "log.transfer_error", "ERROR", error=str(e))
            task["status"] = "error"
            task["error"] = str(e)
            _update_task_db(task_id, "error", 0, 0, 0, error=str(e))
        finally:
            if api and not api.client.is_closed:
                api.close()
    
    thread = threading.Thread(target=run_lazy_transfer, daemon=True)
    thread.start()
    
    # 标记任务为已处理（防止重复恢复）
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE tasks SET processed = 1 WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[恢复] 标记 processed 失败: {e}")
    
    return {
        "message": f"任务已从断点恢复（已有 {checkpoint_count} 个文件）",
        "task_id": task_id,
        "checkpoint_count": checkpoint_count,
    }


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
        if status == "rate_limited":
            current_action = task.get("error", "限流中，自动重试中...")
        elif phase == "collecting":
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
        total = tp.get("total", files_found) if phase in ("pipeline", "transferring", "completed") else 0
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
            "transfer_summary": task.get("transfer_summary"),
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
        elapsed = 0
        status = row[3]
        created_at = row[7]
        updated_at = row[8] if len(row) > 8 else None
        if status in ("completed", "error") and created_at and updated_at:
            try:
                start = datetime.fromisoformat(created_at)
                end = datetime.fromisoformat(updated_at)
                elapsed = int((end - start).total_seconds())
            except:
                pass
        
        # 从 checkpoint 获取实际进度（运行中/暂停任务）
        total = row[4] or 0
        completed = row[5] or 0
        failed = row[6] or 0
        if status in ("ready", "running", "paused") and total == 0:
            try:
                checkpoint = row[14] if len(row) > 14 else None
                if checkpoint:
                    cp = json.loads(checkpoint)
                    transferred = len(cp.get("transferred_fs_ids", []))
                    if transferred > 0:
                        completed = transferred
                        total = cp.get("total_files", transferred)
            except Exception:
                pass
        
        tasks.append({
            "id": row[0],
            "share_link": row[1],
            "target_path": row[2],
            "status": status,
            "total_files": total,
            "completed_files": completed,
            "failed_files": failed,
            "created_at": created_at,
            "error_message": row[10],
            "batch_id": row[11],
            "elapsed": elapsed,
            "surl": row[15] if len(row) > 15 else "",
            "pwd": row[16] if len(row) > 16 else "",
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


@app.get("/api/task/{task_id}/summary")
async def get_task_summary(task_id: str):
    """获取任务转存总结（优先从内存，否则从 checkpoint + DB 计算）"""
    # 先从活跃任务获取
    if task_id in active_tasks:
        task = active_tasks[task_id]
        # 任务完成：返回最终总结
        summary = task.get("transfer_summary")
        if summary:
            return {**summary, "status": "completed"}
        # 运行中：优先返回实时计数器
        rt = task.get("realtime")
        if rt:
            return {
                "total_planned": rt.get("total", 0),
                "success_count": rt.get("success", 0),
                "failed_count": rt.get("failed", 0),
                "skipped_count": 0,
                "elapsed": rt.get("elapsed", 0),
                "status": task.get("status", "running"),
            }
        # fallback: progress 字典
        progress = task.get("progress", {})
        elapsed = round(time.time() - task.get("start_time", time.time()), 1) if "start_time" in task else 0
        return {
            "total_planned": progress.get("files_found", 0),
            "success_count": progress.get("completed", 0),
            "failed_count": progress.get("failed", 0),
            "skipped_count": 0,
            "elapsed": elapsed,
            "status": task.get("status", "running"),
        }
    
    # 从 DB 获取
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    status = row[3]
    total_files = row[4] or 0
    completed_files = row[5] or 0
    failed_files = row[6] or 0
    created_at = row[7]
    updated_at = row[8] if len(row) > 8 else None
    
    # 尝试从 checkpoint 获取实际进度（运行中/暂停任务）
    checkpoint_transferred = 0
    checkpoint_total = 0
    try:
        conn2 = sqlite3.connect(DB_PATH)
        c2 = conn2.cursor()
        c2.execute("SELECT checkpoint, logs FROM tasks WHERE id = ?", (task_id,))
        cp_row = c2.fetchone()
        conn2.close()
        if cp_row:
            if cp_row[0]:
                cp = json.loads(cp_row[0])
                checkpoint_transferred = len(cp.get("transferred_fs_ids", []))
                checkpoint_total = cp.get("total_files", 0)
            # 从日志统计成功/失败数
            if cp_row[1]:
                logs = json.loads(cp_row[1])
                for log_entry in logs:
                    if "成功转存" in log_entry:
                        # 提取成功数: "成功转存 N 个文件"
                        import re as _re
                        m = _re.search(r"成功转存\s*(\d+)", log_entry)
                        if m:
                            completed_files += int(m.group(1))
    except Exception:
        pass
    
    # 用 checkpoint 数据覆盖（如果 DB 字段为 0）
    if total_files == 0 and checkpoint_total > 0:
        total_files = checkpoint_total
    if completed_files == 0 and checkpoint_transferred > 0:
        completed_files = checkpoint_transferred
    
    elapsed = 0
    if created_at:
        try:
            start = datetime.fromisoformat(created_at)
            end = datetime.fromisoformat(updated_at) if updated_at and status in ("completed", "error") else datetime.now()
            elapsed = int((end - start).total_seconds())
        except:
            pass
    
    return {
        "total_planned": total_files,
        "success_count": completed_files,
        "failed_count": failed_files,
        "skipped_count": 0,
        "elapsed": elapsed,
        "status": status,
        "dirs_scanned": 0,
        "api_requests": 0,
        "failed_files": [],
        "share_link": row[1] if len(row) > 1 else "",
        "target_path": row[2] if len(row) > 2 else "",
        "surl": row[15] if len(row) > 15 else "",
        "pwd": row[16] if len(row) > 16 else "",
    }


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
    """删除所有任务记录"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM tasks")
    deleted = c.rowcount
    conn.commit()
    conn.close()
    
    # 同时清空内存中的活跃任务
    active_tasks.clear()
    active_batches.clear()
    
    return {"message": f"已删除 {deleted} 条记录", "deleted": deleted}


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
