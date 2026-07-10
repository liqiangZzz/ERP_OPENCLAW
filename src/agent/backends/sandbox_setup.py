# =============================================================================
# ★ 模块：OpenSandbox 沙箱的初始化与文件播种
# =============================================================================
"""
OpenSandbox 沙箱的初始化与文件播种模块。

职责:
1. 获取或创建 OpenSandbox 沙箱，包装为 OpenSandboxBackend。
2. 播种技能文件（技能包 SKILL.md）。

注意：AGENTS.md 已迁移到 StoreBackend（全局共享），不经过沙箱。
用户长期记忆（/memories/）由 CompositeBackend 路由到 StoreBackend 持久化。
运行时的增量技能同步由 SkillsSyncMiddleware 负责。

使用方式:
    from agent.backends.sandbox_setup import setup_sandbox
    from agent.config import SANDBOX_CONFIG

    backend = setup_sandbox(SANDBOX_CONFIG)       # 创建新沙箱
    backend = setup_sandbox(SANDBOX_CONFIG, sandbox_id="xxx")  # 连接已有沙箱
"""
from datetime import timedelta
from pathlib import Path
from typing import List, Tuple

from openai.types.responses.container_auto_param import NetworkPolicy
from opensandbox import SandboxSync
from opensandbox.models import NetworkRule

from agent.backends.custom_opensandbox import OpenSandboxBackend
from agent.config import LOCAL_SKILLS_DIR, SANDBOX_SKILLS_ROOT


# =============================================================================
# ★ 1. 公开 API：setup_sandbox
# =============================================================================

def setup_sandbox(config, sandbox_id=None, image=None) -> OpenSandboxBackend:
    """
    获取或创建一个沙箱实例

    执行顺序：
    1.若提供 sandbox_id，尝试连接已有沙箱；连接失败则创建新沙箱
    2. 预创建运行时所需目录 （_ensure_dirs）
    3. 上传技能文件到沙箱（_send_files）
    4. 创建 Python venv 并预装依赖 （_create_venv_）

    Args:
        config: ConnectionConfigSync 配置
        sandbox_id: 可选，要连接的现有 沙箱Id
        image: 可循啊，创建新沙箱时使用的镜像。

    Returns:
        OpenSandboxBackend: 沙箱实例
    """

    if sandbox_id:
        print(f"[INFO] 正在连接到现有沙箱: {sandbox_id}")
        try:
            sandbox = SandboxSync.connect(sandbox_id, connection_config=config)
            print(f"[INFO] 成功连接到沙箱: {sandbox_id}")
        except Exception as e:
            print(f"[WARNING] 连接沙箱失败: {e}，将创建新沙箱")
            sandbox_id = None

    if not sandbox_id:
        if not image:
            image = "sandbox-registry.cn-zhangjiakou.cr.aliyuncs.com/opensandbox/code-interpreter:v1.0.2"

        print(f"[INFO] 正在创建新沙箱,使用镜像: {image}")
        sandbox = SandboxSync.create(
            image,
            entrypoint=["/opt/opensandbox/code-interpreter.sh"],
            env={"PYTHON_VERSION": "3.11"},
            resource={"cpu": "2", "memory": "4Gi"},
            timeout=timedelta(hours=2),
            connection_config=config,
            network_policy=NetworkPolicy(  # 沙箱网络路由限制策略
                domain_secrets="deny",
                egress=[
                    NetworkRule(action="allow", target="pypi.org"),
                    NetworkRule(action="allow", target="*.github.com"),
                ]
            )
        )

        backend = OpenSandboxBackend(sandbox=sandbox)
        print(f"[INFO] 沙箱就绪，ID：{sandbox.id}")

        # 预创建 skills 需要的目录，避免 Agent 运行时遇到 FileNotFoundError
        _ensure_dirs(backend)

        _send_files(backend)

        # 创建 Python venv + 预装第三方依赖
        _create_venv(backend)

        return backend


# =============================================================================
# ★ 2. 内部常量：目录、venv 路径、预装包列表
# =============================================================================

# skills 运行时依赖的目录（需在沙箱中预创建）
_SKILL_DIRS = ["/analysis/tmp"]

# 所有 Python 依赖统一安装到此 venv，避开系统 Python 的 externally managed 限制
_VENV_PATH = "/opt/skills-venv"
_VENV_PIP = f"{_VENV_PATH}/bin/pip"

# skills 运行时需要的Python 第三方包
_PREINSTALL_PACKAGES = ["numpy", "pandas", "matplotlib", "requests", "beautifulsoup4"]


# =============================================================================
# ★ 3. 内部函数：_ensure_dirs
# =============================================================================

def _ensure_dirs(backend: OpenSandboxBackend) -> None:
    """
    预创建 skills 运行所需的目录，避免 FileNotFoundError。

    遍历 _SKILL_DIRS 定义的目录列表，在沙箱中执行 mkdir -p 创建。
    mkdir -p 具有幂等性：目录已存在时不会报错。

    Args:
        backend: 沙箱后端实例。
    """

    for d in _SKILL_DIRS:
        backend.execute(f"mkdir -p {d}")


# =============================================================================
# ★ 4. 内部常量：PyPI 镜像与 pip 参数
# =============================================================================

# 阿里云 PyPI 镜像，沙箱内走内网加速
_PYPI_INDEX = "https://mirrors.aliyun.com/pypi/simple/"
# pip install 通用参数
_PIP_INSTALL_ARGS = f"-i {_PYPI_INDEX} --default-timeout=60 --no-input -q"


# =============================================================================
# ★ 5. 内部函数：_seed_files
# =============================================================================
def _send_files(backend: OpenSandboxBackend) -> None:
    """
    将本地技能文件上传到沙箱。

    策略：
     - 遍历 LOCAL_SKILLS_DIR 下所有子目录的文件
     - 映射到沙箱的 SANDBOX_SKILLS_ROOT 路径
     - 仅上传在沙箱中尚不存在的文件，或内容已变更的文件
     - AGENTS.md 已迁移到 StoreBackend （全局共享，不经过沙箱）

     Args：
        backend: 沙箱后端实例。
    """

    file_mapping: List[Tuple[Path, str]] = []

    # 遍历 skills 目录，添加所有技能文件
    skills_base = Path(LOCAL_SKILLS_DIR)
    if skills_base.exists():
        for skill_dir in skills_base.iterdir():
            if not skill_dir.is_dir():
                continue
            for local_file in skill_dir.rglob("*"):
                if local_file.is_file():
                    rel = local_file.relative_to(skills_base).as_posix()
                    sandbox_path = f"{SANDBOX_SKILLS_ROOT}/{rel}"
                    file_mapping.append((local_file, sandbox_path))

    # 收集需要上传的文件
    to_upload: List[Tuple[str, bytes]] = []
    for local_path, sandbox_path in file_mapping:
        if not local_path.exists():
            continue

        local_content = local_path.read_bytes()
        # 用 test -f 检测文件是否存在（无 ERROR 日志），避免 download_files 对 404 打 ERROR
        check = backend.execute(f"test -f {sandbox_path}")
        if check.exit_code == 0:
            try:
                results = backend.download_files([sandbox_path])
                if results and results[0].content and not results[0].error:
                    remote_content = results[0].content
                    if isinstance(remote_content, str):
                        remote_content = remote_content.encode("utf-8")
                    if remote_content == local_content:
                        # 内容相同，无需上传
                        continue
            except Exception:
                pass
        to_upload.append((sandbox_path, local_content))

    if to_upload:
        print(f"[INFO] 正在上传 {len(to_upload)} 个基础文件...")
        backend.upload_files(to_upload)
        print("[INFO] 基础文件上传完成。")
    else:
        print("[INFO] 所有基础文件已就绪，无需上传。")


# =============================================================================
# ★ 6. 内部函数：_create_venv
# =============================================================================
def _create_venv(backend: OpenSandboxBackend) -> None:
    """创建沙箱级 Python venv，并预装 skills 所需的第三方包。

    系统 Python 设置了 externally managed 限制（PEP 668），--system 安装会被拒绝。
    因此创建一个统一的 venv，并将 /opt/skills-venv/bin 注入 SANDBOX_PATH 最前面，
    所有 skill 的 python/pip 命令自动路由到 venv，无需改任何脚本。

    Args:
        backend: 沙箱后端实例。
    """
    # 1. 创建 venv（幂等：已存在则跳过）
    result = backend.execute(f"python3 -m venv {_VENV_PATH}")
    if result.exit_code != 0:
        print(f"[WARNING] venv 创建失败: {result.output[:200]}")
        return
    print(f"[INFO] Python venv 就绪: {_VENV_PATH}")

    # 2. 升级 pip（镜像加速，60s 超时）
    backend.execute(f"{_VENV_PIP} install --upgrade pip {_PIP_INSTALL_ARGS}", timeout=60)

    # 3. 预装依赖（sentinel 避免重复安装）
    # 每个包安装成功后创建 /tmp/.venv_installed_{pkg} 标记文件
    # 下次初始化时检测该标记文件存在则跳过，提升重复启动速度
    for pkg in _PREINSTALL_PACKAGES:
        sentinel = f"/tmp/.venv_installed_{pkg}"
        check = backend.execute(f"test -f {sentinel}")
        if check.exit_code == 0:
            continue
        print(f"[INFO] 正在安装 Python 依赖: {pkg}...")
        result = backend.execute(
            f"{_VENV_PIP} install {pkg} {_PIP_INSTALL_ARGS}",
            timeout=120,
        )
        if result.exit_code == 0:
            backend.execute(f"touch {sentinel}")
            print(f"[INFO]   {pkg} 安装成功")
        else:
            print(f"[WARNING] {pkg} 安装失败: {result.output[:200]}")
