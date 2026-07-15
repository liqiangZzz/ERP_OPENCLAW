"""
自动记忆更新中间件。

在每轮 Agent 回复完成后（aafter_agent 钩子），自动提取对话中涉及的
供应商名称和查询摘要，更新 StoreBackend 中的用户偏好文件。

Agent 无需手动维护 recent_suppliers / recent_queries —— 系统自动处理。

使用方式:
    from agent.middlewares.memory_update import MemoryUpdateMiddleware
    middleware = MemoryUpdateMiddleware(model=SUMMARY_MODEL)
"""
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)

# =============================================================================
# ★ 1. 常量定义
# =============================================================================
# 触发自动更新的 ERP 业务关键词（中文）
_TRIGGER_KEYWORDS = [
    "供应商", "物料", "采购", "订单", "价格", "分析", "对比",
    "比价", "报价", "评估", "筛选", "推荐", "行情", "预算",
    "库存", "订货", "交期", "质量", "成本", "报价", "招标",
    "supplier", "part", "order", "price", "analysis",
]

# 跳过更新的无意义消息模式
_SKIP_PATTERNS = [
    "你好", "在吗", "嗨", "hello", "hi", "hey",
    "你能做什么", "你有哪些功能", "你是谁",
    "我之前的偏好", "我的偏好", "我的记忆",
]


# =============================================================================
# ★ 2. 辅助函数 —— 判断有意义的 ERP 交互
# =============================================================================
def _is_meaningful_erp_exchange(messages: List[BaseMessage]) -> Optional[str]:
    """
    检查最后一条用户消息是否有意义的ERP 交互

    判断逻辑：
    1. 定位最后一条 human 的消息
    2. 排除无意义的打招呼/功能询问
    3. 要求包含 ERP 业务关键词，或存在子 Agent（task 工具）调用

    Returns：
        用户消息文本（有意义时），或 None（应跳过）
    """

    # 从后往前找最后一条用户消息
    last_user_msg = None
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", None)
        if msg_type == "human":
            last_user_msg = msg
            break

    if last_user_msg is None:
        return None

    # content 可能是字符串或列表（多模态消息），统一转为纯文本
    content = last_user_msg.content
    if isinstance(content, list):
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in content
        )
    content = str(content).strip()

    if not content:
        return None

    # 跳过无意义消息（去除空格和大小写后匹配，避免 "你 好" 等变体绕过）
    content_lower = content.lower().replace(" ", "")
    for pattern in _SKIP_PATTERNS:
        if pattern.lower().replace(" ", "") in content_lower:
            return None

    # 检查是否包含 ERP 关键词
    has_erp_keyword = any(
        kw.lower() in content_lower for kw in _TRIGGER_KEYWORDS
    )
    if not has_erp_keyword:
        # 兜底：检查是否委派了子 Agent（messages 中有 task 工具调用）
        # 即使不含 ERP 关键词，子 Agent 调用也说明发生了业务交互
        has_subagent_call = False

        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    # "task" 是子 Agent 委派工具的名称
                    if tc.get("name") == "task":
                        has_subagent_call = True
                        break

            if has_subagent_call:
                break
        # 既无 ERP 关键词，又无子 Agent 调用 → 不是有意义的 ERP 交互
        if not has_subagent_call:
            return None
    return content


# =============================================================================
# ★ 3. 辅助函数 —— 提取 AI 摘要
# =============================================================================
def _extract_ai_summary(messages: List[BaseMessage]) -> str:
    """提取最后一条 AI 消息的前 300 字符作为摘要。

    摘要将作为 LLM 提取实体的辅助上下文，帮助理解对话内容。
    取前 300 字符是为了控制 token 开销，同时保留足够语义。
    """
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "ai":
            content = msg.content
            # 兼容多模态 content（列表格式），拼接为纯文本
            if isinstance(content, list):
                content = " ".join(
                    part.get("text", "") if isinstance(part, dict) else str(part)
                    for part in content
                )
            # 截取前 300 字符，兼顾语义完整性和 token 成本
            return str(content)[:300]
    return ""


# =============================================================================
# ★ 4. 辅助函数 —— LLM 实体提取
# =============================================================================
async def _extract_entities(model: BaseChatModel, user_message: str, ai_summary: str) -> Dict[str, Any]:
    """
    使用 LLM 从对话中提取供应商和查询摘要。

    Args：
        model: 用于实体提取的 LLM 实例。
        user_message: 用户原始消息文本
        ai_summary: AI 恢复的前 300字符摘要

     Returns:
        {"suppliers": [...], "query": "..."} 或 {"suppliers": [], "query": ""}
    """
    prompt = f"""Extract procurement-related entities from this conversation.

Rules:
1. "suppliers": Company/supplier names mentioned. Include both Chinese and English names. Empty list if none.
2. "query": One-line summary of the user's procurement need. Empty string if not procurement-related.

User message: {user_message}

Assistant response summary: {ai_summary}

Return ONLY a JSON object, no other text:
{{"suppliers": ["CompanyA", "CompanyB"], "query": "brief summary"}}"""

    try:
        response = await model.ainvoke(prompt)

        # 从回复中提取 JSON
        text = response.content
        if isinstance(text, list):
            text = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in text
            )
        text = str(text).strip()

        # 提取 JSON 块（兼容 LLM 可能输出 Markdown 代码块包裹的情况）
        start = text.find("{")
        end = text.rfind("}")  # 用 rfind 取最后一个 }，避免嵌套对象被截断
        if start != -1 and end != -1 and end > start:
            result = json.loads(text[start:end + 1])
            return {
                "suppliers": result.get("suppliers", []),
                "query": result.get("query", "")
            }
    except Exception:
        logger.warning("MemoryUpdateMiddleware: LLM 提取失败，跳过本次更新", exc_info=True)

    return {"suppliers": [], "query": ""}


# =============================================================================
# ★ 5. 辅助函数 —— 创建 StoreBackend 文件值
# =============================================================================
def _create_file_value(content_str: str) -> dict:
    """
    创建 StoreBackend 兼容的文件值（与 deepagents.backends.utils.create_file_data 一致）

    将多行文本拆分为行列表，并附加创建/修改时间戳。
    StoreBackend 要求文件值包含 content（行列表）和时间戳字段。

    Args：
        content_str: 多行文本内容
    Returns:
        包含 content（行列表）、created_at、modified_at 的字典。
    """
    # 按换行拆分为列表，与 StoreBackend 的文件存储格式一致
    lines = content_str.split("\n")
    now = datetime.now(timezone.utc).isoformat()
    return {
        "content": lines,
        "created_at": now,
        "modified_at": now,
    }


# =============================================================================
# ★ 6. 类 MemoryUpdateMiddleware —— 自动记忆更新中间件
# =============================================================================
class MemoryUpdateMiddleware(AgentMiddleware):
    """
    在 Agent 回复后自动更新用户记忆文件的 recent_suppliers / recent_queries

    不依赖 Agent 自觉——中间件自动提取、合并、写回。
    """

    def __init__(self, model: BaseChatModel) -> None:
        super().__init__()
        self.model = model  # 用于实体提取的 LLM 模型

    # ------------------------------------------------------------------
    # ★ 6.1同步钩子（不执行操作）
    # ------------------------------------------------------------------
    def after_agent(
            self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        return None

    # ------------------------------------------------------------------
    # ★ 6.2 异步钩子（核心逻辑）
    # ------------------------------------------------------------------
    async def aafter_agent(
            self, state: Dict[str, Any], runtime: Any
    ) -> Optional[Dict[str, Any]]:
        """
        Agent 回复完成后触发：提取实体并更新记忆

        处理流程：
        1. 从 runtime 获取 user_id 和 store 后端
        2. 判断对话是否为有意义的 ERP 交互
        3. 通过 LLM 提取供应商和查询摘要
        4. 读取现有偏好文件，合并新数据后写回

        Args:
            state: Agent 状态，包含对话消息列表。
            runtime: 运行时上下文，包含 user_id 和 store。
        """
        try:
            # ── 步骤 1：获取 user_id（用于 StoreBackend 命名空间隔离） ──
            ctx = getattr(runtime, "context", None)
            if ctx is None:
                logger.warning("MemoryUpdateMiddleware: 无法获取用户上下文，跳过记忆更新")
                return None
            user_id = ctx.get("user_id") if isinstance(ctx, dict) else getattr(ctx, "user_id", None)
            if user_id is None:
                logger.warning("MemoryUpdateMiddleware: 无法获取用户 ID，跳过记忆更新")
                return None

            # ── 步骤 2：获取消息列表 ──
            messages: List[BaseMessage] = state.get("messages", [])
            if not messages:
                logger.warning("MemoryUpdateMiddleware: 消息列表为空，跳过记忆更新")
                return None

            # ── 步骤 3：判断是否需要更新（过滤无意义的对话） ──
            user_message = _is_meaningful_erp_exchange(messages)
            if user_message is None:
                logger.info("MemoryUpdateMiddleware: 对话无意义，跳过记忆更新")
                return None

            # ── 步骤 4：提取 AI 摘要（用于辅助 LLM 实体提取，提供上下文） ──
            ai_summary = _extract_ai_summary(messages)

            # ── 步骤 5：LLM 提取实体（供应商名称 + 查询摘要） ──
            extracted = await _extract_entities(self.model, user_message, ai_summary)
            suppliers = extracted.get("suppliers", [])
            query = extracted.get("query", "")

            if not suppliers and not query:
                logger.info("MemoryUpdateMiddleware: 未提取到供应商和查询，跳过记忆更新")
                return None

            logger.info(
                f"MemoryUpdateMiddleware: user={user_id}, "
                f"suppliers={suppliers}, query={query[:50]}"
            )

            # ── 步骤 6：从 StoreBackend 读取当前偏好文件 ──
            store = getattr(runtime, "store", None)
            if store is None:
                logger.warning("MemoryUpdateMiddleware: runtime.store 不可用")
                return None

            namespace = (user_id,)  # store 命名空间按用户隔离
            key = f"/{user_id}/preferences.md"

            try:
                item = await store.get(namespace, key)
            except Exception:
                logger.warning("MemoryUpdateMiddleware: 无法获取偏好文件，跳过记忆更新")
                return None

            # ── 步骤 7：解析现有偏好内容（兼容 dict 和 str 两种存储格式） ──
            current_lines: List[str] = []
            if item is not None and hasattr(item, "value"):
                value = item.value
                if isinstance(value, dict):
                    # dict 格式：content 字段为行列表或字符串
                    content = value.get("content", [])
                    if isinstance(content, list):
                        current_lines = [str(line) for line in content]
                    elif isinstance(content, str):
                        current_lines = content.split("\n")
                elif isinstance(value, str):
                    # 直接存储的字符串格式
                    current_lines = value.split("\n")

            # 合并新旧偏好数据
            updated_content = _merge_preferences(current_lines, suppliers, query)

            # ── 步骤 8：写回 StoreBackend ──
            file_value = _create_file_value(updated_content)
            await store.aput(namespace, key, file_value)

            logger.info(
                f"MemoryUpdateMiddleware: 已更新 {user_id} 的记忆 "
                f"(suppliers={len(suppliers)}, query={'yes' if query else 'no'})"
            )
        except Exception:
            logger.warning("MemoryUpdateMiddleware: 更新失败", exc_info=True)

        return None


# =============================================================================
# ★ 7. 辅助函数 —— 合并偏好数据
# =============================================================================
def _merge_preferences(
        current_lines: List[str], new_suppliers: List[str], new_query: str
) -> str:
    """将新的 suppliers/query 合并到现有偏好内容中。

    策略：先移除旧 recent_suppliers / recent_queries 区块，再在末尾追加合并后的版本。
    这样可以保证偏好文件中同一字段只出现一次，且内容是最新的。

    Args:
        current_lines: 当前偏好文件的行列表。
        new_suppliers: 本轮新提取的供应商名称列表。
        new_query: 本轮新提取的查询摘要。

    Returns:
        合并后的完整偏好文件文本（含尾部换行）。
    """
    # ── 阶段 1：解析旧的 suppliers 和 queries ──
    existing_suppliers: List[str] = []
    existing_queries: List[str] = []

    def _parse_list_items(lines: List[str], start_idx: int) -> tuple:
        """从 start_idx 行（recent_xxx: 标题行）解析列表项。

        支持两种格式：
        - inline: recent_suppliers: [a, b, c]
        - multiline: 下一行起每行以 "- xxx" 开头

        Args:
            lines: 全部行列表。
            start_idx: 标题行索引。

        Returns:
            (解析出的项列表, 区块占用的行数)
        """
        items: List[str] = []
        title_line = lines[start_idx].strip()

        # 检查 inline 格式: recent_suppliers: [a, b]
        colon_pos = title_line.find(":")
        if colon_pos != -1:
            inline = title_line[colon_pos + 1:].strip()
            if inline.startswith("[") and inline.endswith("]"):
                inner = inline[1:-1].strip()
                if inner:
                    # 拆分逗号分隔的项，去除引号
                    return [s.strip().strip("'").strip('"') for s in inner.split(",") if s.strip()], 1

        # 多行格式: 从下一行开始收集 "- xxx" 项
        count = 1  # 标题行自身占 1 行
        for j in range(start_idx + 1, len(lines)):
            stripped = lines[j].strip()
            if stripped.startswith("- "):
                # 提取 "- " 后面的内容，去除引号
                items.append(stripped[2:].strip().strip("'").strip('"'))
                count += 1
            elif stripped and not lines[j].startswith(" "):
                break  # 遇到下一个顶级字段（非缩进的非空行），区块结束
            else:
                count += 1  # 空行或注释，仍属于当前区块
        return items, count

    # ── 阶段 2：找出旧区块的位置和值 ──
    suppliers_start = -1
    suppliers_len = 0
    queries_start = -1
    queries_len = 0

    for i, line in enumerate(current_lines):
        stripped = line.strip()
        if stripped.startswith("recent_suppliers:"):
            suppliers_start = i
            existing_suppliers, suppliers_len = _parse_list_items(current_lines, i)
        elif stripped.startswith("recent_queries:"):
            queries_start = i
            existing_queries, queries_len = _parse_list_items(current_lines, i)

    # ── 阶段 3：从原内容中移除旧区块（从后往前删，避免索引偏移） ──
    clean_lines = list(current_lines)
    # 按起始位置降序排列，确保后出现的区块先删除，不影响前面区块的索引
    removals = []
    if suppliers_start >= 0:
        removals.append((suppliers_start, suppliers_len))
    if queries_start >= 0:
        removals.append((queries_start, queries_len))
    removals.sort(key=lambda x: x[0], reverse=True)

    for start, length in removals:
        del clean_lines[start:start + length]

    # ── 阶段 4：合并新值和旧值（新在前、旧在后，去重） ──
    # 供应商：新提取的排在前面，已有的追加在后面，去重后最多保留 10 个
    merged_suppliers = list(new_suppliers)
    for s in existing_suppliers:
        if s not in merged_suppliers:
            merged_suppliers.append(s)
    merged_suppliers = merged_suppliers[:10] # 最多保留 10 个供应商

    # 查询摘要：新的排在前面，旧的追加在后面，去重后最多保留 5 条
    merged_queries = [new_query] if new_query else []
    for q in existing_queries:
        if q.strip() not in [m.strip() for m in merged_queries]:
            merged_queries.append(q)
    merged_queries = merged_queries[:5]

    # ── 阶段 5：在清理后的内容末尾追加合并后的区块 ──
    result_lines = list(clean_lines)

    # 确保末尾有空行分隔，避免与原有内容粘连
    if result_lines and result_lines[-1].strip():
        result_lines.append("")

    # 追加 recent_suppliers 区块
    result_lines.append("recent_suppliers:")
    if merged_suppliers:
        for s in merged_suppliers:
            result_lines.append(f"  - {s}")
    else:
        result_lines[-1] = "recent_suppliers: []"  # 空列表用 inline 格式

    # 追加 recent_queries 区块
    result_lines.append("recent_queries:")
    if merged_queries:
        for q in merged_queries:
            result_lines.append(f"  - {q}")
    else:
        result_lines[-1] = "recent_queries: []"  # 空列表用 inline 格式

    return "\n".join(result_lines).strip() + "\n"
