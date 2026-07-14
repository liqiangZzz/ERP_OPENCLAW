"""
技能分配工具

将 /skills/main/ 下已验证的技能分配给指定 Agent（主 Agent 或子 Agent）。
支持 StoreBackend 持久化 + 压缩包清理。
"""
from datetime import timezone, datetime

from langchain_core.tools import tool

from agent.config import SCOPE_MAP


# =============================================================================
# ★ 1. 工具工厂 —— 创建 assign_skill 工具
# =============================================================================
def create_assign_skill_tool(sandbox_backend, store, skills_namespace):
    """
    创建 assign_skill 工具工厂函数。

    Args:
        sandbox_backend: OpenSandboxBackend 实例，用于沙箱内文件操作。
        store: BaseStore 实例（来自 config.STORE），用于持久化技能到 StoreBackend。
        skills_namespace: StoreBackend 中技能的命名空间元组。

    Returns:
        assign_skill 工具函数（异步）。
    """

    # =============================================================================
    # ★ 2. 工具定义 —— assign_skill
    # =============================================================================
    @tool
    async def assign_skill(skill_name: str, agent_name: str) -> str:
        """
        将已验证的技能分配给指定 Agent（主 Agent 或子 Agent），并持久化到长期存储。

        前提条件：技能已下载/创建到 /skills/main/{skill_name}/ 并通过测试。

        Args:
            skill_name: 技能目录名（如 "web-scraper"）
            agent_name: 目标 Agent：
                - "main" — 分配给主 Agent 自身（技能已就位，直接持久化）
                - "procurement-analyst" — 分配给采购分析子 Agent
                - "procurement-order" — 分配给采购订单子 Agent

        Returns:
            分配确认或错误信息。
        """
        # 1. 校验目标 Agent → scope
        if agent_name not in SCOPE_MAP:
            available = ", ".join(SCOPE_MAP.keys())
            return f"错误：未知 Agent '{agent_name}'。可用: {available}"

        # SCOPE_MAP 存储 agent_name → scope 的映射（如 "main" → "user"）
        scope = SCOPE_MAP[agent_name]
        source_dir = f"/skills/main/{skill_name}"
        target_dir = f"/skills/{scope}/{skill_name}"

        # 2.  检查源目录是否存在
        check = sandbox_backend.execute(f"test -f {source_dir}/SKILL.md")
        if check.exit_code != 0:
            return (
                f"错误：技能 '{skill_name}' 在 {source_dir}/ 下不存在。\n"
                f"请先完成技能下载/创建和测试。"
            )

        # 3.复制到目标 scope 目录（主Agent 已就位，跳过复制）
        if agent_name == "main":
            return f"技能 '{skill_name}' 已就位，跳过复制。"
        else:
            result = sandbox_backend.execute(
                f"mkdir -p {target_dir} && cp -r {source_dir}/* {target_dir}/"
            )
            if result.exit_code != 0:
                return f"错误：复制技能文件失败:\n{result.output}"
            verify = sandbox_backend.execute(f"ls {target_dir}/")
            cp_result = (
                f"✅ 已复制到沙箱 {target_dir}/\n"
                f"文件:\n{verify.output.strip()}"
            )

        # 4. 持久化到 StoreBackend （读取/skills/main/ 下的文件 → store.aput）
        persist_report = await  _persist_skill(sandbox_backend, store, skills_namespace, skill_name, scope)

        # 5. 清理压缩包（/skills/main/ 下的 *.zip、*.tar.gz、*.tar、*.tgz）
        cleanup_report = _cleanup_packages(sandbox_backend)

        return (
            f"✅ 技能 '{skill_name}' 已分配给 Agent '{agent_name}'（scope: {scope}）\n"
            f"{cp_result}\n"
            f"{persist_report}\n"
            f"{cleanup_report}"
        )

    assign_skill.name = 'assign_skill'
    return assign_skill


# =============================================================================
# ★ 3. 内部辅助函数 —— 技能持久化与清理
# =============================================================================

async def _persist_skill(sandbox_backend, store, namespace, skill_name: str, scope: str) -> str:
    """将技能文件写入 StoreBackend 持久化。

    从沙箱 /skills/main/{skill_name}/ 读取所有文件，
    写入 store namespace 下 key: /{scope}/{skill_name}/...

    Returns:
        持久化结果描述。
    """

    source_dir = f"/skills/main/{skill_name}"
    now = datetime.now(timezone.utc).isoformat()

    # 列出源目录下所有文件
    ls_result = sandbox_backend.execute(f"find {source_dir} -type f")
    if ls_result.exit_code != 0:
        return f"错误：列出文件失败:\n{ls_result.output}"

    file_paths = [p.strip() for p in ls_result.output.strip().split("\n") if p.strip()]
    if not file_paths:
        return f"错误：源目录 {source_dir} 下无文件。"

    persisted_count = 0
    for sandbox_path in file_paths:
        # 计算相对路径→ StoreBackend key
        # 例如 /skills/main/web-fetcher/SKILL.md → /main/web-fetcher/SKILL.md
        rel = sandbox_path[len(f"/skills/main/"):]
        store_key = f"{scope}/{rel}"

        # 读取文件内容
        try:
            dl = sandbox_backend.download_files([sandbox_path])
            # 有错误或下载结果为空时，跳过该文件
            if not dl or dl[0].error:
                continue
            content_bytes = dl[0].content
            content_str = content_bytes.decode("utf-8") if isinstance(content_bytes, bytes) else str(content_bytes)
        except Exception:
            continue

        # 写入 Store（格式与 StoreBackend 一致）
        try:
            await store.aput(
                namespace,
                store_key,
                {
                    "content": [content_str],
                    "created_at": now,
                    "updated_at": now,
                }
            )
            persisted_count += 1
        except Exception as e:
            return f"⚠️ 持久化部分失败（{store_key}: {e}），已成功 {persisted_count} 个文件"

    return f"💾 持久化完成：{persisted_count} 个文件 → StoreBackend /persisted-skills/{scope}/{skill_name}/"


def _cleanup_packages(sandbox_backend) -> str:
    """删除 /skills/main/ 下的压缩包文件。

    Returns:
        清理结果描述。
    """
    patterns = "*.zip *.tar.gz *.tar *.tgz *.tar.bz2 *.tar.xz"
    cmd = f"cd /skills/main/ && rm -f {patterns} 2>/dev/null; ls {patterns} 2>/dev/null || echo 'none'"
    result = sandbox_backend.execute(cmd)

    output = result.output.strip()
    if output == "none" or not output:
        return "🧹 压缩包已清理"
    else:
        return f"🧹 压缩包已清理（残留: {output}）"