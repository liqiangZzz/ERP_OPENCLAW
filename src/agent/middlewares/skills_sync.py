"""
模块文档 —— 技能同步中间件

在每个 Agent 运行周期开始前，将本地 src/skills/ 下的技能文件与沙箱同步。
检测到变化时，向对话中插入系统通知，提醒 Agent 有新技能可用。

同步策略（批量哈希比对）：
  用一次 `find ... -exec md5sum` 拿到沙箱侧全部技能文件的哈希，与本地哈希集合
  在内存里比对，只上传真正有差异的文件。把原来「逐文件 test -f + download +
  MD5」的 O(N) 次远程往返压成 O(1) 次，同时不再依赖任何跨请求缓存。

并发安全：
  同一用户的多个会话（不同 thread_id）共享同一个 per-user 沙箱，会并发进入
  _sync_files。这里用一把按 user_id 维度持有的进程内 threading.Lock，把整段
  「读沙箱哈希 → 比对 → 上传」串行化，避免两路并发对同一沙箱重复/交错写入。
  技能同步只在 FastAPI 进程内发生（本中间件仅在 create_main_agent 里实例化），
  不存在跨进程竞态，因此进程内锁即可保证数据安全。
"""
import hashlib
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

from deepagents.backends.sandbox import BaseSandbox
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from agent.config import LOCAL_SKILLS_DIR, SANDBOX_SKILLS_ROOT


# =============================================================================
# ★ 0. 并发保护 —— 按 user 维度的进程内同步锁
# =============================================================================
# 锁粒度 = user_id：同一用户的多个 thread 共享沙箱必须串行，不同用户互不阻塞。
# 每个活跃用户一把锁，常驻内存（threading.Lock 体量可忽略）。
_SYNC_LOCKS: Dict[str, threading.Lock] = {}
_SYNC_LOCKS_GUARD = threading.Lock()


def _sync_lock_for(user_key: str) -> threading.Lock:
    """取得（或创建）某用户的同步锁。纯同步、中间无 await，不存在创建竞态。"""
    with _SYNC_LOCKS_GUARD:
        lock = _SYNC_LOCKS.get(user_key)
        if lock is None:
            lock = threading.Lock()
            _SYNC_LOCKS[user_key] = lock
        return lock


def _resolve_user_key(runtime: Any) -> str:
    """从 runtime.context 解析 user_id 作为锁 key；拿不到时退化为全局 key（更保守）。"""
    ctx = getattr(runtime, "context", None)
    user_id = None
    if isinstance(ctx, dict):
        user_id = ctx.get("user_id")
    elif ctx is not None:
        user_id = getattr(ctx, "user_id", None)
    return str(user_id) if user_id is not None else "__default__"


# =============================================================================
# ★ 0.5 本地扫描过滤 —— 跳过编译产物 / 系统垃圾文件
# =============================================================================
# .pyc 会随 Python 每次运行不断重生成、哈希一直变，若不排除会导致每轮无谓上传，
# 完全违背「只同步真正变更」的初衷。
_SKIP_DIR_NAMES = {"__pycache__"}
_SKIP_FILE_SUFFIXES = (".pyc", ".pyo")
_SKIP_FILE_NAMES = {".DS_Store"}


def _should_skip(dir_parts: Tuple[str, ...], file_name: str) -> bool:
    """判断某技能文件是否应跳过同步。"""
    if any(part in _SKIP_DIR_NAMES for part in dir_parts):
        return True
    if file_name.endswith(_SKIP_FILE_SUFFIXES):
        return True
    if file_name in _SKIP_FILE_NAMES:
        return True
    return False


# =============================================================================
# ★ 1. 内部函数 —— 扫描本地技能文件并算哈希
# =============================================================================
def _collect_local_files() -> List[Tuple[str, str, bytes, str]]:
    """扫描本地技能目录，返回 [(key, skill_name, content_bytes, local_md5), ...]。

    key = 相对 LOCAL_SKILLS_DIR 的 posix 路径，与沙箱内 /skills/<key> 一一对应。
    只收集「位于某个技能包子目录下」的文件（rel 至少两段），与旧实现按子目录遍历
    的口径保持一致；跳过编译产物与系统垃圾文件。
    """
    local_skills_dir = Path(LOCAL_SKILLS_DIR)
    if not local_skills_dir.exists():
        return []

    entries: List[Tuple[str, str, bytes, str]] = []
    for local_file in local_skills_dir.rglob("*"):
        if not local_file.is_file():
            continue
        rel = local_file.relative_to(local_skills_dir)
        # 必须在某个技能包子目录内（parts[0] = 技能包名），顶层散文件忽略
        if len(rel.parts) < 2:
            continue
        if _should_skip(rel.parts[:-1], local_file.name):
            continue
        try:
            content = local_file.read_bytes()
        except OSError:
            continue
        key = rel.as_posix()
        skill_name = rel.parts[0]
        entries.append((key, skill_name, content, hashlib.md5(content).hexdigest()))
    return entries


# =============================================================================
# ★ 2. 内部函数 —— 一次远程往返取回沙箱侧全部技能哈希
# =============================================================================
def _fetch_remote_hashes(backend: BaseSandbox) -> Dict[str, str]:
    """用一条 find + md5sum 批量拿到沙箱侧技能文件的 md5，返回 {key: md5}。

    以本地文件集合为驱动去查这张表（见 _sync_files），所以这里即使把沙箱里
    「非本地来源」的文件（Store 恢复 / assign_skill 持久化的技能）也列出来也无妨——
    它们不在本地集合里，永远不会被当作差异，更不会被删除。

    命令失败 / 目录不存在时返回空表：调用方据此把所有本地文件视为「需上传」，
    属于保守但正确的兜底（宁可多传，也绝不让沙箱缺技能）。
    """
    cmd = (
        f"find {SANDBOX_SKILLS_ROOT} -type f "
        "! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.pyo' "
        "-exec md5sum {} +"
    )
    result = backend.execute(cmd)
    if result.exit_code != 0:
        return {}

    remote: Dict[str, str] = {}
    prefix = SANDBOX_SKILLS_ROOT.rstrip("/") + "/"
    for line in (result.output or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)  # [hash, path]
        if len(parts) != 2:
            continue
        md5, path = parts[0], parts[1].strip()
        # md5sum 对含特殊字符的文件名会加反斜杠前缀，这里去掉
        if path.startswith("\\"):
            path = path[1:]
        if path.startswith(prefix):
            remote[path[len(prefix):]] = md5
    return remote


# =============================================================================
# ★ 3. 类 SkillsSyncMiddleware —— 技能文件同步中间件
# =============================================================================
class SkillsSyncMiddleware(AgentMiddleware):
    """
    技能文件同步中间件，依赖沙箱后端的文件操作能力。

    每次 Agent 运行前，用一次批量哈希比对找出真正变更的技能文件并增量上传；
    检测到变更时注入一条 SystemMessage，提醒 Agent 重新发现技能。
    """

    def __init__(self, backend: BaseSandbox):
        super().__init__()
        self.backend = backend  # 沙箱后端实例（per-user SandboxBackendProxy）

    # =============================================================================
    # ★  钩子方法 —— before_agent / abefore_agent
    # =============================================================================
    def before_agent(self, state: Dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        """
        同步钩子：执行技能文件同步，有变更时返回通知消息。

        Args：
            state : Agent 当前状态（未使用）。
            runtime : 运行时上下文，用于解析 user_id 作为并发锁 key。

        Returns：
            若有技能更新，返回包含 SystemMessage 通知的字典；否则返回 None。
        """
        new_skills = self._sync_files(_resolve_user_key(runtime))
        if new_skills:
            return self._make_notification(new_skills)
        return None

    async def abefore_agent(self, state: Dict[str, Any], runtime: Any) -> Optional[Dict[str, Any]]:
        """
        异步钩子：把同步版 _sync_files 丢到线程执行器，避免阻塞事件循环。

        Args：
            state : Agent 当前状态（未使用）。
            runtime : 运行时上下文，用于解析 user_id 作为并发锁 key。

        Returns：
            若有技能更新，返回包含 SystemMessage 通知的字典；否则返回 None。
        """
        import asyncio
        loop = asyncio.get_running_loop()
        # 从 runtime.context 解析 user_id 作为锁 key；拿不到时退化为全局 key（更保守）
        user_key = _resolve_user_key(runtime)
        new_skills = await loop.run_in_executor(None, self._sync_files, user_key)
        if new_skills:
            return self._make_notification(new_skills)
        return None

    # =============================================================================
    # ★ 4. 内部方法 —— 文件同步（批量比对 + 按 user 串行）
    # =============================================================================
    def _sync_files(self, user_key: str) -> List[str]:
        """批量比对本地与沙箱技能文件，只上传有差异的；按 user 维度串行保证并发安全。

        流程（整段在同一把 user 锁内完成，构成一次原子的 read-modify-write）：
        1. 扫描本地技能文件并算 MD5（跳过 __pycache__ / *.pyc 等产物）
        2. 一次远程往返取回沙箱侧全部技能哈希
        3. 以本地集合为驱动比对：沙箱哈希一致就跳过，缺失或不同才上传
        4. 把所有差异文件合并成一次 upload_files 批量上传

        Returns：
            发生变化的技能包名称列表（用于生成通知）。
        """
        lock = _sync_lock_for(user_key)
        with lock:
            local_entries = _collect_local_files()
            if not local_entries:
                return []

            # 一次远程往返拿到沙箱侧全部哈希；失败/目录不存在 → 空表 → 保守全量上传
            remote_hashes = _fetch_remote_hashes(self.backend)

            file_to_upload: List[Tuple[str, bytes]] = []
            changed_skills: set = set()
            for key, skill_name, content, local_md5 in local_entries:
                # 沙箱侧哈希与本地一致 → 文件已是最新，跳过（不再逐文件下载）
                if remote_hashes.get(key) == local_md5:
                    continue
                file_to_upload.append((f"{SANDBOX_SKILLS_ROOT}/{key}", content))
                changed_skills.add(skill_name)

            # 所有差异文件合并为一次批量上传
            if file_to_upload:
                self.backend.upload_files(file_to_upload)

            return sorted(changed_skills)

    # =============================================================================
    # ★ 5. 内部方法 —— 通知生成
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
