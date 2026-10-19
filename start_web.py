"""
DeepAgent Web Application Launcher.

Starts services in dependency order:
1. MCP Server (工具服务)
2. Global OpenSandbox prewarm (沙箱预热)
3. Agent Protocol server for async subagents (异步子代理服务)
4. FastAPI backend (后端 API)
5. Vue frontend (前端界面)
"""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

# ==========================================
# 基础路径与配置
# ==========================================
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
FRONTEND_DIR = os.path.join(PROJECT_ROOT, "frontend")

# Agent Protocol 服务配置 (默认为本地 2024 端口)
AGENT_PROTOCOL_HOST = os.environ.get("ASYNC_AGENT_PROTOCOL_HOST", "127.0.0.1")
AGENT_PROTOCOL_PORT = int(os.environ.get("ASYNC_AGENT_PROTOCOL_PORT", "2024"))


def build_python_env():
    """构建统一的 Python 运行环境变量"""
    env = os.environ.copy()
    # 将 src 目录加入 PYTHONPATH，确保子进程能直接 import 项目内的模块
    env["PYTHONPATH"] = SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
    # 强制 Python 使用 UTF-8 编码，避免跨平台（尤其是 Windows）中文乱码问题
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # 传递 Agent Protocol 的地址，供主 Agent 或后端代码动态读取
    env["ASYNC_AGENT_PROTOCOL_URL"] = (
        f"http://{AGENT_PROTOCOL_HOST}:{AGENT_PROTOCOL_PORT}"
    )
    return env


def stream_process_output(proc, prefix):
    """在独立线程中持续读取子进程的输出，并加上前缀打印到控制台"""
    try:
        for line in proc.stdout:
            if line:
                try:
                    text = line.decode("utf-8", errors="replace").rstrip()
                except Exception:
                    text = line.decode("gbk", errors="replace").rstrip()
                print(f"[{prefix}] {text}")
    except Exception:
        pass


def start_process(name, args, cwd, env=None, shell=False):
    """通用的子进程启动函数"""
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        shell=shell,
        stdout=subprocess.PIPE,  # 重定向标准输出
        stderr=subprocess.STDOUT, # 将错误输出合并到标准输出，方便统一捕获
        bufsize=1,               # 行缓冲，确保输出能实时打印
    )
    # 开启一个后台守护线程，专门负责读取并打印该进程的日志
    threading.Thread(
        target=stream_process_output,
        args=(proc, name),
        daemon=True,
    ).start()
    return proc


# ==========================================
# 服务启动逻辑 (按启动顺序排列)
# ==========================================

def run_mcp_server():
    """1. 启动 MCP 工具服务 (端口 8000)"""
    print("[MCP] Starting MCP service...")
    print("[MCP] Using FastMCP at http://127.0.0.1:8000/mcp")
    return start_process(
        "MCP",
        [sys.executable, "-m", "mcp_server.server_main"],
        cwd=PROJECT_ROOT,
        env=build_python_env(),
    )


def prewarm_global_sandbox():
    """2. 预热 OpenSandbox 全局沙箱 (同步阻塞执行)"""
    print("[Sandbox] Ensuring global OpenSandbox is ready...")
    # 注意：这里是同步调用（subprocess.run），必须等沙箱准备好才继续往下走
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agent.backends.global_sandbox_manager",
            "--ensure",
        ],
        cwd=PROJECT_ROOT,
        env=build_python_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300, # 设置 5 分钟超时，防止沙箱拉取镜像时卡死
    )
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr.rstrip())
        return False
    print("[Sandbox] Global OpenSandbox is ready!")
    return True


def langgraph_executable():
    """查找 langgraph 命令行工具（兼容 Windows 的 .exe 后缀）"""
    scripts_dir = os.path.dirname(sys.executable)
    candidate = os.path.join(scripts_dir, "langgraph.exe")
    if os.path.exists(candidate):
        return candidate
    return "langgraph"


def run_agent_protocol_server():
    """3. 启动 Agent Protocol 服务 (异步子代理，端口 2024)"""
    print("[AgentProtocol] Starting async subagent Agent Protocol service...")
    print(
        "[AgentProtocol] Using LangGraph at "
        f"http://{AGENT_PROTOCOL_HOST}:{AGENT_PROTOCOL_PORT}"
    )
    return start_process(
        "AgentProtocol",
        [
            langgraph_executable(),
            "dev",                   # 使用 langgraph 开发模式
            "--host",
            AGENT_PROTOCOL_HOST,
            "--port",
            str(AGENT_PROTOCOL_PORT),
            "--n-jobs-per-worker",
            "10",                    # 每个 worker 允许 10 个并发任务
            "--allow-blocking",      # 允许阻塞调用（某些工具的同步操作需要）
        ],
        cwd=PROJECT_ROOT,
        env=build_python_env(),
    )


def run_backend():
    """4. 启动 FastAPI 后端服务 (端口 8090)"""
    print("[Backend] Starting FastAPI service...")
    print("[Backend] Using uvicorn at http://localhost:8090")
    return start_process(
        "Backend",
        [
            sys.executable,
            "-m",
            "uvicorn",
            "api_view.web_main:app",
            "--reload",              # 开发模式：代码修改后自动重载
            "--host",
            "0.0.0.0",
            "--port",
            "8090",
        ],
        cwd=PROJECT_ROOT,
        env=build_python_env(),
    )


def run_frontend():
    """5. 启动 Vue/Vite 前端服务 (端口 3000)"""
    # 检查是否已安装依赖 (node_modules 是否存在)
    if not os.path.exists(os.path.join(FRONTEND_DIR, "node_modules")):
        print("[Frontend] Installing dependencies...")
        result = subprocess.run(
            "npm install",
            cwd=FRONTEND_DIR,
            shell=True,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print("[Frontend] Dependency installation failed:")
            print(result.stderr)
            return None
        print("[Frontend] Dependencies installed")

    print("[Frontend] Starting Vue dev server...")
    print("[Frontend] Using Vite at http://localhost:3000")
    return start_process(
        "Frontend",
        "npm run dev",
        cwd=FRONTEND_DIR,
        shell=True,
    )


# ==========================================
# 健康检查逻辑 (轮询等待服务就绪)
# ==========================================

def check_mcp_ready(proc, max_wait=60):
    """通过 Socket 探测 MCP 服务的 8000 端口是否通"""
    print("[System] Waiting for MCP server to be ready...")
    start_time = time.time()

    while time.time() - start_time < max_wait:
        # 如果进程提前挂了，直接返回 False
        if proc.poll() is not None:
            print(f"[ERROR] MCP process exited early with code {proc.returncode}")
            return False

        try:
            # 尝试建立 TCP 连接
            with socket.create_connection(("127.0.0.1", 8000), timeout=3):
                print("[System] MCP server is ready!")
                return True
        except Exception:
            pass

        time.sleep(1)
        elapsed = int(time.time() - start_time)
        print(f"[System] Waiting for MCP... ({elapsed}s / {max_wait}s)")

    return False


def check_agent_protocol_ready(proc, max_wait=120):
    """通过 HTTP /ok 接口 或 Socket 探测 Agent Protocol 服务是否就绪"""
    print("[System] Waiting for Agent Protocol server to be ready...")
    start_time = time.time()
    health_url = f"http://{AGENT_PROTOCOL_HOST}:{AGENT_PROTOCOL_PORT}/ok"

    while time.time() - start_time < max_wait:
        if proc.poll() is not None:
            print(
                "[ERROR] Agent Protocol process exited early "
                f"with code {proc.returncode}"
            )
            return False

        try:
            # 优先尝试请求健康检查接口
            response = urllib.request.urlopen(health_url, timeout=5)
            if response.status == 200:
                print("[System] Agent Protocol server is ready!")
                return True
        except Exception:
            try:
                # 降级方案：如果 HTTP 接口还没起，但端口通了，也认为就绪
                with socket.create_connection(
                    (AGENT_PROTOCOL_HOST, AGENT_PROTOCOL_PORT),
                    timeout=3,
                ):
                    print("[System] Agent Protocol server port is ready!")
                    return True
            except Exception:
                pass

        time.sleep(2)
        elapsed = int(time.time() - start_time)
        print(
            "[System] Waiting for Agent Protocol... "
            f"({elapsed}s / {max_wait}s)"
        )

    return False


def check_backend_ready(proc, max_wait=180):
    """通过 /health 接口探测 FastAPI 后端是否就绪"""
    print("[System] Waiting for backend to be ready...")
    start_time = time.time()

    while time.time() - start_time < max_wait:
        if proc.poll() is not None:
            print(f"[ERROR] Backend process exited early with code {proc.returncode}")
            return False

        try:
            req = urllib.request.Request("http://localhost:8090/health")
            response = urllib.request.urlopen(req, timeout=5)
            if response.status == 200:
                data = response.read().decode("utf-8")
                # 必须返回 "healthy" 字样才算真正就绪
                if "healthy" in data:
                    print("[System] Backend is ready!")
                    return True
        except Exception:
            pass

        time.sleep(2)
        elapsed = int(time.time() - start_time)
        print(f"[System] Waiting for backend... ({elapsed}s / {max_wait}s)")

    return False


# ==========================================
# 进程清理与主流程
# ==========================================

def stop_process(name, proc):
    """优雅停止单个子进程"""
    if not proc:
        return

    print(f"[System] Stopping {name}...")
    proc.terminate()  # 发送 SIGTERM 信号
    try:
        proc.wait(timeout=5) # 等待 5 秒让其自行退出
    except Exception:
        proc.kill() # 如果超时未退出，强制杀死 (SIGKILL)


def main():
    print("=" * 60)
    print("         DeepAgent Web Application Launcher")
    print("=" * 60)
    print()
    print("NOTE: Agent initialization takes 30-60 seconds")
    print("      Please wait patiently for the service to be ready")
    print()

    # 提前声明进程句柄，方便在 cleanup 中统一回收
    mcp_proc = None
    agent_protocol_proc = None
    backend_proc = None
    frontend_proc = None

    def cleanup():
        """清理所有已启动的服务（按依赖的反向顺序停止）"""
        print("\n\n[System] Stopping services...")
        stop_process("Frontend", frontend_proc)
        stop_process("Backend", backend_proc)
        stop_process("AgentProtocol", agent_protocol_proc)
        stop_process("MCP", mcp_proc)
        print("[System] All services stopped")

    def signal_handler(sig, frame):
        """捕获 Ctrl+C / kill 信号，优雅退出"""
        cleanup()
        sys.exit(0)

    # 注册信号监听
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except Exception:
        pass

    try:
        # ── 1. 启动 MCP 服务并等待就绪 ──
        mcp_proc = run_mcp_server()
        if not check_mcp_ready(mcp_proc, max_wait=60):
            print("[ERROR] MCP server failed to start within 60 seconds")
            print("[ERROR] Please check the logs above for errors")
            cleanup()
            return

        # ── 2. 预热沙箱 (同步阻塞) ──
        if not prewarm_global_sandbox():
            print("[ERROR] Global OpenSandbox prewarm failed")
            print("[ERROR] Please check the logs above for errors")
            cleanup()
            return

        # ── 3. 启动 Agent Protocol 服务并等待就绪 ──
        agent_protocol_proc = run_agent_protocol_server()
        if not check_agent_protocol_ready(agent_protocol_proc, max_wait=120):
            print("[ERROR] Agent Protocol server failed to start within 120 seconds")
            print("[ERROR] Please check the logs above for errors")
            cleanup()
            return

        # ── 4. 启动 FastAPI 后端并等待就绪 ──
        backend_proc = run_backend()
        if not check_backend_ready(backend_proc, max_wait=180):
            print("[ERROR] Backend failed to start within 180 seconds")
            print("[ERROR] Please check the logs above for errors")
            cleanup()
            return

        # ── 5. 启动前端 (异步启动，不阻塞主流程) ──
        frontend_proc = run_frontend()

        # ── 所有服务启动完毕，打印访问地址 ──
        print()
        print("=" * 60)
        print("  All services started successfully!")
        print("=" * 60)
        print()
        print("  Please access:")
        print("    - MCP Server: http://127.0.0.1:8000/mcp")
        print(
            "    - Agent Protocol: "
            f"http://{AGENT_PROTOCOL_HOST}:{AGENT_PROTOCOL_PORT}"
        )
        print("    - Frontend: http://localhost:3000")
        print("    - API docs: http://localhost:8090/docs")
        print()
        print("  Press Ctrl+C to stop all services")
        print("=" * 60)

        # 主线程进入死循环，保持脚本一直运行
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        cleanup()
    except Exception as e:
        print(f"[ERROR] An error occurred: {e}")
        cleanup()


if __name__ == "__main__":
    main()