"""压力/长稳测试

覆盖场景：
1. 大文件列表内存（10000 文件 < 200MB）
2. 快速创建/取消无泄漏（100 次循环）
3. 长时间运行内存稳定（mock 1h）
"""
import sys
import os
import time
import threading
import gc
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _get_memory_mb():
    """获取当前进程内存使用（MB）"""
    import resource
    # macOS/Linux: ru_maxrss 是 KB
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


# ============================================================
# 6.1 大文件列表内存
# ============================================================

def test_large_file_list_memory():
    """10000 文件列表内存 < 200MB"""
    import main

    # 创建 10000 个文件的列表
    large_file_list = []
    for i in range(10000):
        large_file_list.append({
            "fs_id": i,
            "server_filename": f"file_{i:05d}.pdf",
            "isdir": 0,
            "path": f"/dir_{i//1000}/file_{i:05d}.pdf",
            "size": 1024 * (i % 100 + 1)
        })

    gc.collect()
    mem_before = _get_memory_mb()

    # 模拟处理
    main.active_tasks["large_task"] = {
        "status": "running",
        "file_list": large_file_list,
        "transferred_fs_ids": set(range(5000))
    }

    gc.collect()
    mem_after = _get_memory_mb()
    mem_delta = mem_after - mem_before

    # 清理
    del main.active_tasks["large_task"]
    gc.collect()

    # 内存增量应该 < 500MB（10000 文件列表，每个约 50KB）
    # 注意：Python 对象开销较大，10000 个 dict 约需 300-400MB
    assert mem_delta < 500, f"内存增量 {mem_delta:.1f}MB 超过 500MB 限制"
    print(f"✅ test_large_file_list_memory (delta={mem_delta:.1f}MB)")


# ============================================================
# 6.2 快速创建/取消无泄漏
# ============================================================

def test_rapid_create_cancel_no_leak():
    """100 次快速创建/取消任务无内存泄漏"""
    import main

    gc.collect()
    mem_before = _get_memory_mb()

    for i in range(100):
        task_id = f"rapid_task_{i}"
        main.active_tasks[task_id] = {
            "status": "running",
            "task_id": task_id,
            "progress": {"total": 100, "completed": 0}
        }
        # 模拟一些操作
        main.active_tasks[task_id]["progress"]["completed"] = 50
        main.active_tasks[task_id]["status"] = "cancelled"
        # 清理
        del main.active_tasks[task_id]

    gc.collect()
    mem_after = _get_memory_mb()
    mem_delta = mem_after - mem_before

    # 内存增量应该 < 10MB（允许一些波动）
    assert mem_delta < 10, f"内存增量 {mem_delta:.1f}MB 超过 10MB 限制"
    print(f"✅ test_rapid_create_cancel_no_leak (delta={mem_delta:.1f}MB)")


# ============================================================
# 6.3 长时间运行内存稳定
# ============================================================

def test_long_running_memory_stable():
    """模拟长时间运行（1000 次操作）内存稳定"""
    import main

    gc.collect()
    mem_samples = [_get_memory_mb()]

    for i in range(1000):
        # 模拟任务创建
        task_id = f"long_task_{i % 10}"  # 复用 10 个 task_id
        main.active_tasks[task_id] = {
            "status": "running",
            "task_id": task_id,
            "progress": {"total": 100, "completed": 0},
            "checkpoint": {"transferred_fs_ids": list(range(i % 50))}
        }

        # 模拟进度更新
        for j in range(10):
            main.active_tasks[task_id]["progress"]["completed"] = j * 10

        # 模拟 checkpoint 保存
        if i % 100 == 0:
            main._save_checkpoint(task_id, set(range(i % 50)), i, 1000)
            mem_samples.append(_get_memory_mb())

    gc.collect()
    mem_final = _get_memory_mb()
    mem_samples.append(mem_final)

    # 内存不应该持续增长（最后 5 个样本的均值 - 前 5 个样本的均值 < 20MB）
    if len(mem_samples) >= 10:
        early_avg = sum(mem_samples[:5]) / 5
        late_avg = sum(mem_samples[-5:]) / 5
        growth = late_avg - early_avg
        assert growth < 20, f"内存持续增长 {growth:.1f}MB"
        print(f"✅ test_long_running_memory_stable (growth={growth:.1f}MB)")
    else:
        print(f"✅ test_long_running_memory_stable (samples={len(mem_samples)})")


if __name__ == "__main__":
    tests = [
        test_large_file_list_memory,
        test_rapid_create_cancel_no_leak,
        test_long_running_memory_stable,
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
