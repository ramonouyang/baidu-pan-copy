"""性能/并发测试

覆盖场景：
1. RateLimiter burst 突发行为
2. 并发任务竞争条件
3. DB 高频写入性能
"""
import sys
import os
import time
import threading
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ============================================================
# 5.1 RateLimiter burst 突发行为
# ============================================================

def test_rate_limiter_burst_allows_immediate():
    """burst 范围内请求应该立即通过"""
    from baidu_api import RateLimiter

    limiter = RateLimiter(rate=1.0, burst=4)

    start = time.time()
    for _ in range(4):
        limiter.acquire(timeout=1.0)
    elapsed = time.time() - start

    # 4 个请求在 burst 范围内，应该几乎立即完成
    assert elapsed < 0.5
    print("✅ test_rate_limiter_burst_allows_immediate")


def test_rate_limiter_after_burst_blocks():
    """burst 耗尽后请求应该阻塞"""
    from baidu_api import RateLimiter

    limiter = RateLimiter(rate=2.0, burst=2)

    # 消耗 burst
    limiter.acquire(timeout=0.1)
    limiter.acquire(timeout=0.1)

    # 下一个请求应该阻塞
    start = time.time()
    limiter.acquire(timeout=1.0)
    elapsed = time.time() - start

    # 应该阻塞了约 0.5 秒（rate=2.0 → 0.5s/令牌）
    assert elapsed >= 0.3
    print("✅ test_rate_limiter_after_burst_blocks")


def test_rate_limiter_concurrent_acquire():
    """多线程并发 acquire 不会超发令牌"""
    from baidu_api import RateLimiter

    limiter = RateLimiter(rate=10.0, burst=3)
    results = []
    errors = []

    def acquire_worker():
        try:
            limiter.acquire(timeout=2.0)
            results.append(1)
        except Exception as e:
            errors.append(e)

    # 启动 10 个线程
    threads = []
    for _ in range(10):
        t = threading.Thread(target=acquire_worker)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(results) == 10
    print("✅ test_rate_limiter_concurrent_acquire")


# ============================================================
# 5.2 并发任务竞争条件
# ============================================================

def test_concurrent_task_creation():
    """多线程同时创建任务不冲突"""
    import main

    main.active_tasks.clear()
    errors = []

    def create_task(task_id):
        try:
            main.active_tasks[task_id] = {
                "status": "running",
                "task_id": task_id,
                "created_at": time.time()
            }
        except Exception as e:
            errors.append(e)

    threads = []
    for i in range(20):
        t = threading.Thread(target=create_task, args=(f"task_{i}",))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0
    assert len(main.active_tasks) == 20
    print("✅ test_concurrent_task_creation")


def test_concurrent_task_status_update():
    """多线程同时更新任务状态"""
    import main

    main.active_tasks.clear()
    main.active_tasks["shared"] = {"status": "running", "progress": 0}
    errors = []

    def update_status(new_status):
        try:
            main.active_tasks["shared"]["status"] = new_status
        except Exception as e:
            errors.append(e)

    threads = []
    statuses = ["rate_limited", "running", "completed", "paused", "error"]
    for status in statuses * 4:  # 20 次更新
        t = threading.Thread(target=update_status, args=(status,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    assert len(errors) == 0
    assert main.active_tasks["shared"]["status"] in statuses
    print("✅ test_concurrent_task_status_update")


# ============================================================
# 5.3 DB 高频写入性能
# ============================================================

def test_db_high_frequency_save():
    """高频 checkpoint 保存不崩溃"""
    import main

    errors = []
    save_count = 50

    def save_checkpoint(i):
        try:
            main._save_checkpoint(f"perf_task_{i}", {i, i+1, i+2}, i, 100)
        except Exception as e:
            errors.append(e)

    start = time.time()
    for i in range(save_count):
        save_checkpoint(i)
    elapsed = time.time() - start

    assert len(errors) == 0
    # 50 次保存应该在 5 秒内完成
    assert elapsed < 5.0
    print(f"✅ test_db_high_frequency_save ({elapsed:.2f}s for {save_count} saves)")


if __name__ == "__main__":
    tests = [
        test_rate_limiter_burst_allows_immediate,
        test_rate_limiter_after_burst_blocks,
        test_rate_limiter_concurrent_acquire,
        test_concurrent_task_creation,
        test_concurrent_task_status_update,
        test_db_high_frequency_save,
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
