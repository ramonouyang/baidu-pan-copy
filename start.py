#!/usr/bin/env python3
"""启动脚本 - 自动查找可用端口"""
import socket
import sys
import os

# 切换到脚本目录
os.chdir(os.path.dirname(os.path.abspath(__file__)))


def find_free_port(start=8080, max_tries=20):
    """查找可用端口"""
    for port in range(start, start + max_tries):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('', port))
                return port
        except OSError:
            continue
    return None


if __name__ == "__main__":
    port = find_free_port(8080)
    if not port:
        print("错误：找不到可用端口")
        sys.exit(1)
    
    print(f"""
╔═══════════════════════════════════════════════════════════╗
║           百度网盘批量转存工具 - 启动成功                  ║
╠═══════════════════════════════════════════════════════════╣
║  访问地址: http://localhost:{port}                           ║
║  API文档:  http://localhost:{port}/docs                      ║
╚═══════════════════════════════════════════════════════════╝
    """)
    
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
