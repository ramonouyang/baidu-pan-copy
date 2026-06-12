"""FastAPI 主应用 - 增强版"""
import json
import sqlite3
import uuid
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from baidu_api import BaiduPanAPI, BatchTransferManager

app = FastAPI(title="百度网盘批量转存工具")

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

# 全局状态存储
active_tasks = {}
active_batches = {}


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


@app.post("/api/share/parse")
async def parse_share_link(req: ShareLinkRequest, request: Request):
    """解析分享链接，返回文件列表预览"""
    cookie = request.headers.get("X-Baidu-Cookie", "")
    if not cookie:
        raise HTTPException(status_code=400, detail="请先设置Cookie")
    
    api = BaiduPanAPI(cookie)
    manager = BatchTransferManager(api)
    
    try:
        result = manager.prepare_transfer(req.share_link, req.pwd, req.target_path)
        
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        
        task_id = str(uuid.uuid4())[:8]
        
        active_tasks[task_id] = {
            "manager": manager,
            "cookie": cookie,
            "share_link": req.share_link,
            "target_path": req.target_path,
            "status": "ready"
        }
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO tasks (id, share_link, target_path, status, total_files, created_at, updated_at)
            VALUES (?, ?, ?, 'ready', ?, ?, ?)
        """, (task_id, req.share_link, req.target_path, result["total_files"],
              datetime.now().isoformat(), datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        return {
            "task_id": task_id,
            "total_files": result["total_files"],
            "share_title": result.get("share_title", ""),
            "preview_files": result.get("files", [])
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        api.close()


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
                surl = url.split("/s/")[-1].split("?")[0] if "/s/" in url else f"share_{i}"
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
            "total_files": total_files
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
    finally:
        api.close()


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
    """开始执行转存任务"""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = active_tasks[task_id]
    manager = task["manager"]
    
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
    
    thread = threading.Thread(target=run_transfer)
    thread.daemon = True
    thread.start()
    
    return {"message": "任务已启动", "task_id": task_id}


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
    """获取任务进度"""
    if task_id not in active_tasks:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    task = active_tasks[task_id]
    manager = task["manager"]
    progress = manager.get_progress()
    
    return {
        "task_id": task_id,
        "status": task.get("status", "unknown"),
        "total": progress.get("total", 0),
        "completed": progress.get("completed", 0),
        "failed": progress.get("failed", 0),
        "current_batch": progress.get("current_batch", 0),
        "total_batches": progress.get("total_batches", 0),
        "logs": progress.get("logs", [])[-10:],
        "failed_files": progress.get("failed_files", []),
        "confirm_data": task.get("confirm_data"),
        "speed": progress.get("speed", 0),
        "elapsed": progress.get("elapsed", 0)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
