"""
DeepAgent Web 应用一键启动脚本

同时启动后端和前端服务
"""

import os
import signal
import subprocess
import sys
import threading
import time

# 强制 Python 无缓冲输出，确保日志实时显示
os.environ["PYTHONUNBUFFERED"] = "1"

def run_backend():
    """启动后端服务"""
    print("[Backend] Starting FastAPI service...", flush=True)
    print("[Backend] Using uvicorn at http://localhost:8090", flush=True)

    project_root = os.path.dirname(os.path.abspath(__file__))
    src_dir = os.path.join(project_root, "src")
    env = os.environ.copy()
    env["PYTHONPATH"] = src_dir + os.pathsep + env.get("PYTHONPATH", "")

    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api_view.web_main:app",
         "--reload", "--host", "0.0.0.0", "--port", "8090"],
        cwd=project_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1
    )

    # 实时输出后端日志
    def output_backend():
        try:
            for line in backend_proc.stdout:
                if line:
                    try:
                        print(f"[Backend] {line.decode('utf-8', errors='replace').rstrip()}", flush=True)
                    except:
                        print(f"[Backend] {line.decode('gbk', errors='replace').rstrip()}", flush=True)
        except:
            pass

    t = threading.Thread(target=output_backend, daemon=True)
    t.start()

    return backend_proc

def check_backend_ready(max_wait=180):
    """检查后端是否就绪"""
    print("[System] Waiting for backend to be ready...", flush=True)
    start_time = time.time()

    while time.time() - start_time < max_wait:
        try:
            import urllib.request
            req = urllib.request.Request("http://localhost:8090/health")
            response = urllib.request.urlopen(req, timeout=5)
            if response.status == 200:
                data = response.read().decode('utf-8')
                if 'healthy' in data:
                    print("[System] Backend is ready!", flush=True)
                    return True
        except:
            pass
        time.sleep(2)
        elapsed = int(time.time() - start_time)
        print(f"[System] Waiting... ({elapsed}s / {max_wait}s)", flush=True)

    return False

def run_frontend():
    """启动前端服务"""
    frontend_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")

    # 检查 node_modules 是否存在
    if not os.path.exists(os.path.join(frontend_dir, "node_modules")):
        print("[Frontend] Installing dependencies...", flush=True)
        result = subprocess.run(
            "npm install",
            cwd=frontend_dir,
            shell=True,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print("[Frontend] Dependency installation failed:")
            print(result.stderr)
            return None
        print("[Frontend] Dependencies installed", flush=True)

    print("[Frontend] Starting Vue dev server...", flush=True)
    print("[Frontend] Using Vite at http://localhost:3000", flush=True)

    frontend_proc = subprocess.Popen(
        "npm run dev",
        cwd=frontend_dir,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1
    )

    # 实时输出前端日志
    def output_frontend():
        try:
            for line in frontend_proc.stdout:
                if line:
                    try:
                        print(f"[Frontend] {line.decode('utf-8', errors='replace').rstrip()}", flush=True)
                    except:
                        print(f"[Frontend] {line.decode('gbk', errors='replace').rstrip()}", flush=True)
        except:
            pass

    t = threading.Thread(target=output_frontend, daemon=True)
    t.start()

    return frontend_proc

def main():
    print("=" * 60, flush=True)
    print("         DeepAgent Web Application Launcher", flush=True)
    print("=" * 60, flush=True)
    print(flush=True)
    print("NOTE: Agent initialization takes 30-60 seconds", flush=True)
    print("      Please wait patiently for the service to be ready", flush=True)
    print(flush=True)

    backend_proc = None
    frontend_proc = None

    def cleanup():
        """清理进程"""
        print("\n\n[System] Stopping services...", flush=True)
        if backend_proc:
            backend_proc.terminate()
            try:
                backend_proc.wait(timeout=5)
            except:
                backend_proc.kill()
        if frontend_proc:
            frontend_proc.terminate()
            try:
                frontend_proc.wait(timeout=5)
            except:
                frontend_proc.kill()
        print("[System] All services stopped", flush=True)

    # 注册信号处理
    def signal_handler(sig, frame):
        cleanup()
        sys.exit(0)

    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except:
        pass  # Windows 不支持某些信号

    try:
        # 启动后端
        backend_proc = run_backend()

        # 等待后端就绪（包括 Agent 初始化）
        if not check_backend_ready(max_wait=180):
            print("[ERROR] Backend failed to start within 180 seconds", flush=True)
            print("[ERROR] Please check the logs above for errors", flush=True)
            cleanup()
            return

        # 启动前端
        frontend_proc = run_frontend()

        print()
        print("=" * 60, flush=True)
        print("  All services started successfully!", flush=True)
        print("=" * 60, flush=True)
        print(flush=True)
        print("  Please access:", flush=True)
        print("    - Frontend: http://localhost:3000", flush=True)
        print("    - API docs: http://localhost:8090/docs", flush=True)
        print(flush=True)
        print("  Press Ctrl+C to stop all services", flush=True)
        print("=" * 60, flush=True)

        # 等待
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        cleanup()
    except Exception as e:
        print(f"[ERROR] An error occurred: {e}", flush=True)
        cleanup()

if __name__ == "__main__":
    main()
