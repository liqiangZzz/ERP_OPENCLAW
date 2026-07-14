# =============================================================================
# ★ 模块描述 —— 自定义网络搜索工具
# =============================================================================
"""
自定义网络搜索工具。

基于智谱 AI 的搜狗 Web Search API，供主 Agent 和所有子 Agent 使用。
"""

import logging

from langchain_core.tools import tool
from zai import ZhipuAiClient

from agent.env_utils import ZHIPU_API_KEY

logger = logging.getLogger(__name__)

# 初始化智谱 AI 客户端，使用环境变量中的 API Key
# 如果 ZHIPU_API_KEY 为空，会在首次调用时报错（延迟失败）
if ZHIPU_API_KEY:
    client = ZhipuAiClient(api_key=ZHIPU_API_KEY)
else:
    client = None
    logger.warning("ZHIPU_API_KEY 未配置，web_search 工具将无法使用")


# =============================================================================
# ★ 1. 工具定义 —— web_search
# =============================================================================
@tool('web_search', parse_docstring=True)
def web_search(query: str) -> str:
    """
    使用搜狗的API进行Web搜索。

    适用于：市场行情调研、供应商背景调查、物料价格趋势查询、行业资讯获取等。

    Args:
        query: 需要搜索的内容或者关键字。

    Returns:
        返回搜索之后的结果。
    """
    # 空值校验
    if not query or not query.strip():
        return "搜索失败: 查询关键词不能为空"

    # 检查客户端是否初始化成功
    if client is None:
        return "搜索失败: ZHIPU_API_KEY 未配置，请检查 .env 文件"

    try:
        # 调用智谱 AI 的搜狗 Web Search API
        response = client.web_search.web_search(
            search_engine="search_pro",  # 使用专业版搜索引擎
            search_query=query,
            count=3,  # 返回结果的条数，范围1-50，默认10
            search_recency_filter="noLimit",  # 不限制搜索结果的日期范围
        )
        # 提取搜索结果内容并拼接返回
        if response.search_result:
            return "\n\n".join([d.content for d in response.search_result])
        return '没有搜索到任何内容！'
    except Exception as e:
        logger.warning(f"web_search 调用失败: {e}")
        return f"搜索失败: {e}"
