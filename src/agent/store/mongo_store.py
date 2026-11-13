# =============================================================================
# ★ MongoDBStore —— langgraph BaseStore 的 MongoDB 持久化实现
# =============================================================================
"""
基于 MongoDB 的 langgraph Store 持久化实现，替代 InMemoryStore。

【为什么需要它】
langgraph 的 Store（BaseStore）默认实现 InMemoryStore 存在内存里，服务一重启
数据全丢。而这个项目里 Store 承载的都是“必须跨会话存活”的数据：
- 用户偏好记忆（memory_update 中间件读写的 preferences.md）
- 持久化技能（assign_skill / UserSkillsRestoreMiddleware）
- 会话自定义标题（history API 的 PATCH 接口）

它与 MongoDBSaver（checkpoint 持久化）形成对称的两条持久化通道：
    MongoDBSaver -> 对话状态 / interrupt 断点（短期记忆）
    MongoDBStore -> 偏好 / 技能 / 标题（长期记忆）

【为什么只实现 batch / abatch 两个方法】
BaseStore 把所有读写操作统一抽象为“操作列表”（Op 对象）：
    get/put/delete/search/list_namespaces 这些同步方法内部都会把参数
    打包成 GetOp/PutOp/... 列表调用 self.batch(ops)；
    aget/aput/... 异步版本同理调用 self.abatch(ops)。
所以子类只需要实现 batch / abatch 的“分发 + 执行”，其余 10 个方法
全部由基类自动派生，无需逐个手写。

【存储结构】一条 Store 条目 = 一个 MongoDB 文档
    {
        "namespace": ["u1"],                    # 命名空间（数组，天然支持索引）
        "ns_path": "u1",                        # namespace 用 \x00 拼接的字符串，专用于前缀查询
        "key": "/memories/u1/preferences.md",   # 条目键
        "value": {...},                         # 业务数据（dict）
        "created_at": ..., "updated_at": ...    # 时间戳
    }
    - ns_path 为什么需要：namespace 是数组，MongoDB 对“数组前缀匹配”没有
      直接语法；拼成字符串后用 ^前缀(\x00|$) 正则即可完成前缀过滤。

【索引】(namespace, key) 唯一索引保证 upsert 幂等；在首次操作时惰性创建，
避免模块导入阶段（config.py import）就强连 MongoDB。

【边界】不支持语义检索（asearch 带 query 的向量搜索），score 恒为 None；

 如果本机无 mongod 时可用 mongomock 做冒烟测试（接口完全兼容）。
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import Optional, Sequence

from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    PutOp,
    SearchItem,
    SearchOp,
)
from pymongo import MongoClient
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)

# namespace 元组的拼接分隔符（\x00 控制字符）
# 选它的原因：正常业务字符串（key、用户名）不可能包含 \x00，
# 保证“拼接 -> 拆分”是无损往返，且前缀匹配不会跨界（["a"] 匹配不到 ["ab"]）
_NS_SEP = "\x00"


def _ns_path(namespace: Sequence[str]) -> str:
    """namespace 元组 -> 可做前缀查询的拼接字符串

    例：("u1", "sub") -> "u1\\x00sub"
    写入时由 _handle_put 存进文档的 ns_path 字段，查询时用正则做前缀匹配。
    """
    return _NS_SEP.join(namespace)


def _prefix_regex(prefix_path: str) -> str:
    """拼接字符串的前缀匹配正则：^prefix(\\x00|$)

    关键点：前缀后面必须是“分隔符或字符串结尾”，二者取其一。
    这样 ["a", "b"] 只能匹配 ["a", "b", ...]，不会误匹配 ["ab"]——
    如果只写 ^a，"ab" 也会被命中，namespace 的层级语义就被破坏了。
    re.escape 防止 namespace 里的特殊字符（如 "."）被当作正则语法。
    """
    return f"^{re.escape(prefix_path)}({_NS_SEP}|$)"


class MongoDBStore(BaseStore):
    """langgraph Store 的 MongoDB 持久化实现

    用法（见 config.py）：
        STORE = MongoDBStore(client=_mongodb_client, db_name=MONGODB_DB_NAME)

    Args:
        client: pymongo MongoClient 实例
            （复用 config.py 的全局客户端——与 MongoDBSaver 共用一个连接池，
            避免重复建连；MongoClient 构造本身不建连，首次操作才连）
        db_name: 数据库名（与 checkpoint 使用同一个库，如 langchain_db）
        collection_name: 存储集合名（默认 agent_store）
    """

    def __init__(
        self,
        client: MongoClient,
        db_name: str,
        collection_name: str = "agent_store",
    ):
        # client[db][coll] 只是拿引用，不触发网络请求
        self._coll = client[db_name][collection_name]
        # 索引是否已创建的标记（配合 _ensure_index 做一次性惰性初始化）
        self._index_ready = False

    # ------------------------------------------------------------------
    # 索引管理
    # ------------------------------------------------------------------
    def _ensure_index(self) -> None:
        """惰性创建 (namespace, key) 唯一索引

        为什么要惰性：
            MongoClient 构造时不连库，但 create_index 会真正发起网络请求。
            如果在 __init__ 里建索引，config.py 被 import 时就必须连上 MongoDB
            ——Mongo 没启动会导致整个服务起不来。
            放到首次 batch 调用时执行，就把“连接”推迟到了真正用的时候。

        为什么失败只告警不中断：
            索引的作用是保证 upsert 幂等 + 加速查询，建不出来顶多在极端并发下
            可能写重，读写功能本身不受影响，不值得为它让整个请求失败。
        """
        if self._index_ready:
            return  # 已建过，直接返回（每次 batch 都会进来，用标记挡掉重复请求）
        try:
            self._coll.create_index(
                [("namespace", 1), ("key", 1)], unique=True
            )
            self._index_ready = True
        except PyMongoError as e:
            logger.warning(f"MongoDBStore 创建索引失败（不影响功能）: {e}")

    # ------------------------------------------------------------------
    # 核心：batch / abatch（基类的所有读写方法都经由这两个方法派生）
    # ------------------------------------------------------------------
    def batch(self, ops) -> list:
        """同步批量执行 Store 操作

        把基类打包来的 Op 列表逐个分发到对应处理函数，
        结果按顺序一一对应（基类按位置取回每个操作的结果）。
        """
        self._ensure_index()  # 首次操作时惰性建索引
        results = []
        for op in ops:
            if isinstance(op, GetOp):
                results.append(self._handle_get(op))
            elif isinstance(op, PutOp):
                results.append(self._handle_put(op))
            elif isinstance(op, SearchOp):
                results.append(self._handle_search(op))
            elif isinstance(op, ListNamespacesOp):
                results.append(self._handle_list_namespaces(op))
            else:
                # 未识别的操作类型：返回 None 占位，保持结果与 ops 等长
                results.append(None)
        return results

    async def abatch(self, ops) -> list:
        """异步批量执行 Store 操作

        pymongo 是同步阻塞客户端，这里用 asyncio.to_thread 把整批操作
        扔进线程池执行，避免阻塞事件循环（Agent 主流程是 asyncio 驱动的，
        一旦阻塞会卡住所有并发请求）。
        """
        return await asyncio.to_thread(self.batch, ops)

    # ------------------------------------------------------------------
    # 各操作的处理实现
    # ------------------------------------------------------------------
    def _handle_get(self, op: GetOp) -> Optional[Item]:
        """按 (namespace, key) 精确取一条；不存在返回 None（基类约定）"""
        doc = self._coll.find_one(
            {"namespace": list(op.namespace), "key": op.key}
        )
        if doc is None:
            return None
        return self._doc_to_item(doc)

    def _handle_put(self, op: PutOp) -> None:
        """写入/更新一条；PutOp.value 为 None 时按 langgraph 约定表示删除"""
        if op.value is None:
            # langgraph 没有 DeleteOp，“删除”就是 value=None 的 PutOp
            # （基类的 store.delete() 内部就是发一个 value=None 的 PutOp）
            return self._handle_delete_from(op.namespace, op.key)

        now = datetime.utcnow()
        self._coll.update_one(
            # 定位条件：(namespace, key) 唯一索引，upsert 的幂等性靠它
            {"namespace": list(op.namespace), "key": op.key},
            {
                # $set：每次写入都要更新的字段（新值 + 更新时间）
                "$set": {"value": dict(op.value), "updated_at": now},
                # $setOnInsert：只在“首次插入”生效的字段
                # （namespace/ns_path/key/created_at 一旦写入不再变；
                #   upsert 覆盖写时不会碰它们，保证 created_at 是真实创建时间）
                "$setOnInsert": {
                    "namespace": list(op.namespace),
                    "ns_path": _ns_path(op.namespace),
                    "key": op.key,
                    "created_at": now,
                },
            },
            upsert=True,  # 不存在则插入，存在则覆盖 value —— 一条语句完成“写或更新”
        )
        return None

    def _handle_delete_from(self, namespace, key) -> None:
        """按 (namespace, key) 精确删除一条（put(None) 与 delete 共用）"""
        self._coll.delete_one({"namespace": list(namespace), "key": key})
        return None

    def _handle_search(self, op: SearchOp) -> list:
        """按 namespace 前缀 + 可选 value 字段过滤，返回条目列表

        典型调用：store.asearch(("u1",)) -> 用户 u1 命名空间下的全部条目。
        """
        # 1) namespace 前缀过滤：用 ns_path 正则（见 _prefix_regex 的边界说明）
        query: dict = {
            "ns_path": {"$regex": _prefix_regex(_ns_path(op.namespace_prefix))}
        }
        # 2) filter 等值过滤：直接映射为 value 内字段的精确匹配
        #    （如 filter={"kind": "skill"} -> {"value.kind": "skill"}）
        if op.filter:
            for field, expected in op.filter.items():
                query[f"value.{field}"] = expected

        cursor = (
            self._coll.find(query)
            # 排序固定：最新更新在前、同时间按 key 名稳定排序（结果可复现）
            .sort([("updated_at", -1), ("key", 1)])
            .skip(op.offset)
            .limit(op.limit)  # 注意基类默认 limit=10，调用方要更多需显式传
        )
        return [self._doc_to_search_item(doc) for doc in cursor]

    def _handle_list_namespaces(self, op: ListNamespacesOp) -> list:
        """列出库中所有 namespace（去重、排序），支持 prefix/suffix/max_depth

        典型调用：store.alist_namespaces(prefix=("u1",))
        """
        # distinct("ns_path") 一次拿全量去重的 namespace 拼接串，
        # 拆回元组形式（数据量 = namespace 个数，通常很小，内存过滤足够）
        namespaces = [
            tuple(p.split(_NS_SEP))
            for p in sorted(self._coll.distinct("ns_path"))
        ]

        # match_conditions：本版本 langgraph 用它表达 prefix / suffix 过滤
        # （op 上没有直接的 prefix 字段，条件在 cond.match_type / cond.path 里）
        result = namespaces
        for cond in op.match_conditions or []:
            path = tuple(cond.path)
            if cond.match_type == "prefix":
                # 元组切片比较：ns 的前 len(path) 个元素必须完全一致
                result = [ns for ns in result if ns[:len(path)] == path]
            elif cond.match_type == "suffix":
                result = [ns for ns in result if ns[-len(path):] == path]

        # max_depth 截断：只保留每层 namespace 的前 N 段
        # 截断会把 ("u1","sub") 和 ("u1","other") 都变成 ("u1",)——所以要去重
        if op.max_depth is not None:
            result = [ns[:op.max_depth] for ns in result]

        result = sorted(set(result))
        # offset/limit 分页与基类语义一致
        return result[op.offset: op.offset + op.limit]

    # ------------------------------------------------------------------
    # 文档 <-> Item 转换
    # ------------------------------------------------------------------
    @staticmethod
    def _doc_to_item(doc: dict) -> Item:
        """MongoDB 文档 -> langgraph Item（get / put 的返回格式）"""
        return Item(
            namespace=tuple(doc["namespace"]),  # 存的是数组，转回元组
            key=doc["key"],
            value=doc["value"],
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
        )

    @staticmethod
    def _doc_to_search_item(doc: dict) -> SearchItem:
        """MongoDB 文档 -> langgraph SearchItem（search 的返回格式）"""
        # SearchItem 比 Item 多一个 score 字段（语义检索相关性得分）。
        # 本实现不支持向量检索（query 参数会被忽略），score 恒为 None。
        return SearchItem(
            namespace=tuple(doc["namespace"]),
            key=doc["key"],
            value=doc["value"],
            created_at=doc["created_at"],
            updated_at=doc["updated_at"],
            score=None,
        )