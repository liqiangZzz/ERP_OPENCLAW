"""
模块文档 —— 技能同步中间件

在每个 Agent 运行周期开始前，将本地 src/skills/ 下的技能文件与沙箱同步。
检测到变化时，向对话中插入系统通知，提醒 Agent 有新技能可用。
"""
import asyncio
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from deepagents.backends.sandbox import BaseSandbox
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from agent.config import LOCAL_SKILLS_DIR, SANDBOX_SKILLS_ROOT


# =============================================================================
# ★ 1. 类 SkillsSyncMiddleware —— 技能文件同步中间件
# =============================================================================
class SkillsSyncMiddleware(AgentMiddleware):
    """
    技能文件同步中间件，依赖沙箱后端的文件操作能力。

    在Agent 启动扫描本地技能目录，通过 MD5 哈希对比本地和沙箱中的文件差异，
    仅上传有变更的文件，同事缓存文件哈希避免重复计算。
    """

    def __init__(self, backend: BaseSandbox):
        super().__init__()
        self.backend = backend  # 沙箱后端实例，用于执行命令和文件传输
        self._last_hashes: Dict[str, str] = {}  # 缓存本地文件哈希，避免重复同步

    # =============================================================================
    # ★  钩子方法 —— before_agent / abefore_agent
    # =============================================================================
    def before_agent(self, state: Dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        """
        同步钩子：执行技能文件同步，有变更时返回通知消息

        Args：
            state : Agent 当前状态（未使用）。
            runtime : 运行时上下文（未使用）。

        Returns：
              若有技能更新，返回包含 SystemMessage 通知的字典；否则返回 None。
        """

        new_skills = self._sync_files()
        if new_skills:
            return self._make_notification(new_skills)
        return None

    async def abefore_agent(self, state: Dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        """
            异步钩子：通过线程执行器调用同步版本的 _sync_files.

             Args：
                 state : Agent 当前状态（未使用）。
                 runtime : 运行时上下文（未使用）。

             Returns：
                   若有技能更新，返回包含 SystemMessage 通知的字典；否则返回 None。
        """
        loop = asyncio.get_running_loop()
        new_skills = await loop.run_in_executor(None, self._sync_files)
        if new_skills:
            return self._make_notification(new_skills)
        return None

    # =============================================================================
    # ★ 2. 内部方法 —— 文件同步
    # =============================================================================
    def _sync_files(self) -> list[str]:
        """
        扫描本地技能目录，将新增/修改的文件上传到沙箱。

        同步策略：
        1.遍历 LOCAL_SKILLS_DIR 下每个子目录（一个子目录 = 一个技能包）
        2.对每个文件计算 MD5，与本地缓存的沙箱中的文件对比
        3.仅对文件确实发生改变时才上传

        Returns：
        发生变化的技能名称列表（不包含具体文件名）
        """

        local_skills_dir = Path(LOCAL_SKILLS_DIR)
        if not local_skills_dir.exists():
            return []

        update_skills: List[str] = []

        # ── 外层循环：逐个技能包目录 ──
        for skill_dir in local_skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_name = skill_dir.name
            sandbox_skills_dir = f"{SANDBOX_SKILLS_ROOT}/{skill_name}"

            # 收集本技能包下需要上传的 (沙箱路径, 文件内容) 对
            file_to_upload: List[Tuple[str, bytes]] = []
            has_changes = False

            # ── 内层循环：递归遍历技能包中的所有文件 ──
            for local_file in skill_dir.rglob("*"):
                if not local_file.is_file():
                    continue

                # 计算相对于技能包根目录的路径，用于拼接沙箱绝对路径
                relative_path = local_file.relative_to(skill_dir).as_posix()
                sandbox_path = f"{sandbox_skills_dir}/{relative_path}"

                with open(local_file, "rb") as f:
                    local_content = f.read()

                    # 计算 MD5，用于快速判断文件是否有变动
                    local_hash = hashlib.md5(local_content).hexdigest()
                    cache_key = f"{skill_name}/{relative_path}"

                    # 【快速跳过】本地哈希与上次同步的缓存一致 → 文件未变，直接跳过
                    if self._last_hashes.get(cache_key) == local_hash:
                        continue

                    # 【慢速对比】缓存未命中，需要和沙箱侧实际文件对比
                    # 先 test -f 判断文件是否存在，避免 download_file 对 404 打 ERROR 日志
                    check = self.backend.execute(f"test -f {sandbox_path}")
                    if check.exit_code == 0:
                        try:
                            results = self.backend.download_files([sandbox_path])
                            if results and results[0].content and not results[0].error:
                                remote_content = results[0].content
                                # 统一为 bytes 再计算哈希
                                if isinstance(remote_content, str):
                                    remote_content = remote_content.encode("utf-8")
                                remote_hash = hashlib.md5(remote_content).hexdigest()
                                # 沙箱文件内容一致 → 无需上传，刷新缓存后跳过
                                if remote_hash == local_hash:
                                    self._last_hashes[cache_key] = local_hash
                                    continue
                        except Exception:
                            pass  # 下载/读取失败，保守策略：视为需要上传

                    # 走到这里说明：新增文件 / 本地已修改 / 沙箱读取失败 → 加入上传队列
                    file_to_upload.append((sandbox_path, local_content))
                    self._last_hashes[cache_key] = local_hash
                    has_changes = True

            # ── 本技能包遍历完毕，统一上传所有变更文件 ──
            if has_changes:
                self.backend.upload_files(file_to_upload)
                update_skills.append(skill_name)

        # 所有技能包处理完毕，返回有变更的技能名列表
        return update_skills

    # =============================================================================
    # ★ 3. 内部方法 —— 通知生成
    # =============================================================================
    def _make_notification(self, skill_names: List[str]) -> Dict[str, Any]:
        """为更新的技能包生成系统通知消息。

        Args:
            skill_names: 已同步更新的技能包名称列表。

        Returns:
            包含 SystemMessage 的字典，通知 Agent 新技能已可用。
        """
        skills_list = "\n".join(f"- {name}" for name in skill_names)
        notice = (
            f"[系统通知] 以下技能包已更新：\n{skills_list}\n"
            "请使用 `ls /skills/` 查看详情，对当前任务可能有帮助。"
        )
        return {"messages": [SystemMessage(content=notice)]}
