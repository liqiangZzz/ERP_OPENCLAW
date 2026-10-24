"""
由FastAPI和Agent Protocol进程共享的全局OpenSandbox管理器。
该项目在FastAPI中运行主代理，并在异步分析师图中运行
独立代理协议进程。Python对象无法跨进程共享
进程，因此本模块将全局沙盒ID持久化存储至磁盘，并
让我们让每个进程重新连接到同一个沙盒。
"""
import argparse
import asyncio
import json
import logging
import threading
from datetime import timedelta, timezone, datetime
from pathlib import Path

import httpx
from opensandbox import SandboxSync
from opensandbox.config import ConnectionConfigSync

from agent.backends.custom_opensandbox import OpenSandboxBackend
from agent.backends.sandbox_proxy import SandboxBackendProxy
from agent.env_utils import OPENSANDBOX_API_KEY

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = PROJECT_ROOT / "src"  # 项目源代码目录
STATE_DIR = PROJECT_ROOT / ".langgraph_api"  # 状态文件目录
STATE_FILE = STATE_DIR / "global_sandbox.json"  # 全局沙盒状态文件

LOCAL_SKILLS_DIR = SRC_DIR / "skills"  # 本地技能目录
LOCAL_AGENTS_MD = SRC_DIR / "agent" / "memory" / "AGENTS.md"  # 本地代理内存文件
AGENTS_MD_FILENAME = "/AGENTS.md"  # 本地代理内存文件名
SANDBOX_SKILLS_ROOT = "/skills"  # 沙箱技能根目录

# 沙箱配置
SANDBOX_IMAGE = (
    "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/"
    "code-interpreter:v1.0.2"
)

# 沙箱连接配置
SANDBOX_CONFIG = ConnectionConfigSync(
    domain="http://192.168.0.188:8080",
    use_server_proxy=True,
    request_timeout=timedelta(seconds=60),
    transport=httpx.HTTPTransport(limits=httpx.Limits(max_connections=20)),
    api_key=OPENSANDBOX_API_KEY,
)

_lock = threading.RLock()
_proxy: SandboxBackendProxy | None = None


def _read_state() -> dict:
    """
    从磁盘读取全局沙盒状态。

    Returns: 全局沙盒状态字典
    """
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Failed to read global sandbox state", exc_info=True)
        return {}


def _write_state(sandbox_id: str) -> None:
    """
    Args:
        sandbox_id: 新的全局沙盒ID
    """

    STATE_DIR.mkdir(parents=True, exist_ok=True)

    payload = {
        "sandbox_id": sandbox_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # 以原子方式写入状态文件
    tmp = STATE_FILE.with_suffix(".tmp")
    # 写入临时文件
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    # 替换原始文件
    tmp.replace(STATE_FILE)


def _connect_sandbox(sandbox_id: str) -> OpenSandboxBackend:
    """
    Args:
        sandbox_id: 沙盒ID
    Returns: OpenSandboxBackend实例
    """
    sandbox = SandboxSync.connect(sandbox_id, connection_config=SANDBOX_CONFIG)
    backend = OpenSandboxBackend(sandbox=sandbox)
    # 确保沙盒运行
    _ping_backend(backend)
    # 确保沙盒准备就绪
    _ensure_sandbox_ready(backend)
    return backend


def _create_sandbox() -> OpenSandboxBackend:
    """
    创建一个新的OpenSandbox实例。
    """
    sandbox = SandboxSync.create(
        SANDBOX_IMAGE,
        entrypoint=["/opt/opensandbox/code-interpreter.sh"],
        env={"PYTHON_VERSION": "3.11"},
        resource={"cpu": "2", "memory": "4Gi"},
        timeout=timedelta(minutes=10),
        connection_config=SANDBOX_CONFIG,
    )
    backend = OpenSandboxBackend(sandbox=sandbox)
    # 确保沙盒准备就绪
    _ensure_sandbox_ready(backend)
    # 写入状态文件
    _write_state(backend.id)
    return backend


def _ping_backend(backend: OpenSandboxBackend | SandboxBackendProxy) -> None:
    """
    确保OpenSandbox实例运行。
    Args:
        backend: OpenSandboxBackend或SandboxBackendProxy实例
    """
    result = backend.execute("echo ok", timeout=30)
    if result.exit_code != 0:
        raise RuntimeError(f"Failed health check failed: {result.output[:500]}")


def _ensure_sandbox_ready(backend: OpenSandboxBackend | SandboxBackendProxy) -> None:
    """
    确保OpenSandbox实例准备就绪。
    Args:
        backend: OpenSandboxBackend或SandboxBackendProxy实例
    """

    backend.execute("mkdir -p /analysis/tmp")
    backend.execute("python3 -m venv /opt/skills-venv", timeout=120)
    sentinel = "/tmp/.global_sandbox_packages_installed"

    if backend.execute(f"test -f {sentinel}").exit_code != 0:
        result = backend.execute(
            "/opt/skills-venv/bin/pip install requests beautifulsoup4"
            "-i https://mirrors.aliyun.com/pypi/simple/ --default-timeout=60 --no-input -q",
            timeout=300,
        )
        if result.exit_code == 0:
            backend.execute(f"touch {sentinel}")

    _seed_files(backend)


def _seed_files(backend: OpenSandboxBackend | SandboxBackendProxy) -> None:
    """
    种子文件。
    Args:
        backend: OpenSandboxBackend或SandboxBackendProxy实例
    """

    uploads: list[tuple[str, bytes]] = []

    if LOCAL_AGENTS_MD.exists():
        # 上传内存文件
        uploads.append((AGENTS_MD_FILENAME, LOCAL_AGENTS_MD.read_bytes()))

    if LOCAL_SKILLS_DIR.exists():
        for local_file in LOCAL_SKILLS_DIR.rglob("*"):
            if local_file.is_file():
                # 相对路径
                rel = local_file.relative_to(LOCAL_SKILLS_DIR).as_posix()
                # 沙箱路径
                uploads.append((f"{SANDBOX_SKILLS_ROOT}/{rel}", local_file.read_bytes()))

    if uploads:
        backend.upload_files(uploads)


def get_global_sandbox_sync() -> SandboxBackendProxy:
    """
    获取全局沙盒同步实例。
    """
    global _proxy
    with _lock:
        if _proxy is not None:
            try:
                _ping_backend(_proxy)
                return _proxy
            except Exception:
                logger.warning("Cached global sandbox is unhealthy; recreating", exc_info=True)

        # 从状态文件中读取沙盒ID
        state = _read_state()
        sandbox_id = state.get("sandbox_id")

        # 如果有沙盒ID，尝试连接到已存在的沙盒
        if sandbox_id:
            try:
                # 尝试连接到已存在的沙盒
                backend = _connect_sandbox(sandbox_id)
                # 种子文件
                _proxy = SandboxBackendProxy(backend=backend)
                return _proxy
            except Exception:
                logger.warning("Failed to reconnect global sandbox %s", sandbox_id, exc_info=True)

        # 创建新的沙盒
        backend = _create_sandbox()
        # 种子文件
        _proxy = SandboxBackendProxy(backend=backend)
        return _proxy


async def ensure_global_sandbox() -> SandboxBackendProxy:
    """
    确保全局沙盒实例存在。
    Returns: SandboxBackendProxy实例
    """
    return await asyncio.to_thread(get_global_sandbox_sync)


async def ping_global_sandbox() -> bool:
    """
    检查全局沙盒是否健康。
    Returns: 是否健康
    """
    try:
        backend = await ensure_global_sandbox()
        await asyncio.to_thread(_ping_backend, backend)
        return True
    except Exception:
        return False


async def recreate_global_sandbox() -> SandboxBackendProxy:
    global _proxy

    def _recreate() -> SandboxBackendProxy:
        """
        重新创建全局沙盒。

        Returns: SandboxBackendProxy实例
        """
        global _proxy
        with _lock:
            # 创建新的沙盒
            backend = _create_sandbox()
            # 种子文件
            if _proxy is None:
                # 创建新的代理
                _proxy = SandboxBackendProxy(backend=backend)
            else:
                # 替换代理
                _proxy.replace_backend(backend)

            return _proxy

    return await asyncio.to_thread(_recreate)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ensure", action="store_true", help="Create or reconnect the global sandbox.")
    args = parser.parse_args()
    if args.ensure:
        backend = get_global_sandbox_sync()
        print(f"Global sandbox ready: {backend.id}")


if __name__ == "__main__":
    main()