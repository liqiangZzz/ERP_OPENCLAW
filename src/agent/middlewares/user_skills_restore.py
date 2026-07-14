"""
技能恢复中间件

在每个 Agent 运行周期开始前，将 StoreBackend 中持久化的技能
恢复到沙箱 /skills/{scope}/{skill_name}/ 路径下，使子 Agent 可以通过
渐进式披露发现和使用。

与 SkillsSyncMiddleware 分工：
  - SkillsSyncMiddleware: 本地 src/skills/ --> 沙箱（预置技能）
  - UserSkillsRestoreMiddleware: StoreBackend --> 沙箱（持久化技能）
"""
from typing import Dict, Any, Optional, Tuple, List

from langchain.agents.middleware import AgentMiddleware


# =============================================================================
# ★ 1. 类 UserSkillsRestoreMiddleware —— 技能恢复中间件
# =============================================================================
class UserSkillsRestoreMiddleware(AgentMiddleware):
    """
    从 StoreBackend 恢复持久化技能到沙箱中间件。

    与 SkillsSyncMiddleware 互补：
    - SkillsSyncMiddleware 同步本地代码仓库中的预置技能
    - 本中间件恢复用户自定义/持久化的技能（存储在 StoreBackend 中）

    两者合在一起确保沙箱中技能目录的完整性。
    """

    def __init__(self, backend, skills_namespace):
        """
        Args:
            backend: OpenSandBoxBackend 实例，负责文件上传
            skills_namespace: StoreBackend中技能的命名空间元组，例如 "skills"
        """
        super().__init__()
        self.backend = backend          # 沙箱后端，用于上传技能文件
        self.namespace = skills_namespace  # Store 中存储技能数据的命名空间

    # =============================================================================
    # ★ 2. 异步钩子 —— 恢复持久化技能
    # =============================================================================
    async def abefore_agent(
            self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        """
        运行前：从StoreBackend 读取持久化技能，上传到沙箱。

        从 runtime.store 获取存储后端，收集所有持久化技能文件，
        批量上传到沙箱 /skills/ 路径下。

        Args:
            state: Agent 当前状态（未使用）
            runtime: 运行时上下文，包含 store 属性

        Returns：
            总是返回 None（不修改 Agent 状态）
        """
        # 从 runtime 取出 StoreBackend 实例
        store = runtime.store
        # 收集所有需要恢复的技能文件
        files = await self._collect_skills(store)
        if files:
            # 批量上传到沙箱，使子 Agent 可通过 /skills/ 路径访问
            await self.backend.upload_files(files)

        return None

    # =============================================================================
    # ★ 2. 同步钩子（不执行操作）
    # =============================================================================
    def before_agent(self, state: Dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        """
        同步版本：不执行操作 （技能恢复仅支持异步）
        """
        return None

    # =============================================================================
    # ★ 3. 内部方法 —— 收集持久化技能
    # =============================================================================
    async def _collect_skills(self, store) -> List[Tuple[str, bytes]]:
        """
        从 StoreBackend 收集所有持久化技能文件。

        StoreBackend key 格式：/{scope}/{skill_name}/...
        沙箱目录路径：/skills/{scope}/{skill_name}/...

        转化规则：
        - key 中的 scope（如 "user"）保持不变
        - 在目录路径上前加上 /skills/ 前缀

        Args：
            store: StoreBackend 实例，用于查询持久化数据
        Returns:
            （沙箱路径，文件内容字节）的列表
        """
        files: List[Tuple[str, bytes]] = []

        # 从 StoreBackend 异步查询指定命名空间下的所有条目
        try:
            items = await store.asearch(self.namespace)
        except Exception:
            # 查询失败（如 StoreBackend 未配置），返回空列表，不影响 Agent 运行
            return files

        for item in items:
            # 去除 key 前导 /，得到 {scope}/{skill_name}/... 格式
            key = str(item.key).lstrip("/")

            # 按 "/" 拆分，分离 scope 和剩余路径
            # key 格式: {scope}/{skill_name}/... → 映射到 /skills/{scope}/{skill_name}/...
            parts = key.split("/", 1)
            if len(parts) != 2:
                continue  # 格式不符合预期（缺少 scope 或子路径），跳过

            scope, rest = parts
            sandbox_path = f"/skills/{scope}/{rest}"

            # 提取文件内容：value 可能是 dict 或 str，统一转为 bytes
            content = item.value
            if isinstance(content, dict):
                # dict 结构取 "content" 字段（常见于带元数据的存储格式）
                content = content.get("content", "")
            if isinstance(content, str):
                content = content.encode("utf-8")
            if not content:
                continue  # 内容为空，跳过

            files.append((sandbox_path, content))

        return files
