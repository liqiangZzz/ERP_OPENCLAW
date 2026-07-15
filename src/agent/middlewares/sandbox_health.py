"""
沙箱健康检查 + 自动恢复中间件。

在每个 Agent 步骤开始前 ping 沙箱，发现不可用时自动重建沙箱
（skills + venv），并重新上传 AGENTS.md。依赖 SandboxBackendProxy
的热替换能力，重建后其他中间件/工具无需感知变化。

与 SandboxCircuitBreakerMiddleware 配合：
- 本中间件：主动健康检查 → 自动恢复
- SandboxCircuitBreakerMiddleware：恢复后仍失败 → 熔断保护
"""
import asyncio
import logging
from typing import Any, Optional, Dict

from langchain.agents.middleware import AgentMiddleware

from agent.backends.sandbox_proxy import SandboxBackendProxy

logger = logging.getLogger(__name__)


# =============================================================================
# ★ 1. 类 SandboxHealthMiddleware —— 沙箱健康守护中间件
# =============================================================================
class SandboxHealthMiddleware(AgentMiddleware):
    """
    沙箱健康守护中间件，每次 agent step 前 ping 沙箱，发现不可用时自动恢复。

    工作流程（与 SandboxCircuitBreakerMiddleware 配合）：
    1. abefore_agent 钩子中调用 _check() ping 沙箱
    2. 沙箱可达 → 零开销，透传
    3. 沙箱不可用 → 调用 sandbox_manager 重建沙箱 → 重新播种 AGENTS.md → 继续执行
    4. 重建后仍失败 → 由 SandboxCircuitBreakerMiddleware 触发熔断保护

    为什么分离为两个中间件？
    - 健康检查：高频检测（每次 agent step），快速恢复，容错
    - 熔断保护：低频触发（连续失败），防止无限循环
    """

    def __init__(self,
                 *,
                 sandbox_backend: SandboxBackendProxy,
                 user_id: str,
                 agents_md_content: bytes) -> None:
        """
        Args:
            sandbox_backend: 沙箱后端代理，用于执行命令和上传文件。
            user_id: 当前用户的标识，用于沙箱恢复日志和路由。
            agents_md_content: AGENTS.md 文件内容，沙箱重建后需要重新上传。
        """
        super().__init__()
        self._backend = sandbox_backend
        self.user_id = user_id
        self._agents_md = agents_md_content

    # ------------------------------------------------------------------
    # ★ 同步钩子（不执行操作）
    # ------------------------------------------------------------------
    def before_agent(self, state: dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        return None

    # ------------------------------------------------------------------
    # ★ 异步钩子 —— 健康检查 + 自动恢复
    # ------------------------------------------------------------------
    async def abefore_agent(
            self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        """
        Agent 步骤开始前触发：先检查沙箱健康，失败则自动恢复。

        Args:
            state: Agent 当前状态（未使用）。
            runtime: 运行时上下文（未使用）。

        Returns:
            始终返回 None，不修改 Agent 状态。恢复操作在后台完成。
        """
        # 先 ping 检查沙箱是否可达
        ok = await self._check()
        if ok:
            return None

        # 沙箱不可用，触发自动恢复
        logger.warning("用户 %s 沙箱无响应，触发自动修复...", self.user_id)
        await self._recover()
        return None

    # ------------------------------------------------------------------
    # ★ 内部方法 —— 健康检查
    # ------------------------------------------------------------------
    async def _check(self) -> bool:
        """
        通过向沙箱发生 echo ok 命令检测沙箱是否存活

        Returns:
            bool: 沙箱是否存活，正常返回True，任何异常返回False
        """
        try:
            await asyncio.to_thread(self._backend.execute, "echo ok")
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # ★ 内部方法 —— 自动恢复
    # ------------------------------------------------------------------
    async def _recover(self) -> None:
        """
        重建用户沙箱并重新上传 AGENTS.md 文件

        分两步进行：
        1. 重建沙箱（skills + venv）
        2. 再上传种子文件。
        """
        from agent.backends.sandbox_manager import recreate_user_sandbox

        await recreate_user_sandbox(self.user_id)
        await asyncio.to_thread(
            self._backend.upload_files,
            [("/AGENTS.md", self._agents_md)]
        )
        logger.info("用户 %s 沙箱自动恢复完成", self.user_id)
