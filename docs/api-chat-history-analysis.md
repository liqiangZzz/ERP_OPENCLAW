# ERP_OPENCLAW 对话系统 API 文档

> 基于代码分析的完整技术文档，涵盖 `chat.py`、`history.py` 及其上下游依赖模块

---

## 目录

- [1. 系统架构总览](#1-系统架构总览)
- [2. 应用入口 — web_main.py](#2-应用入口--web_mainpy)
- [3. 配置 — web_config.py](#3-配置--web_configpy)
- [4. 数据模型 — scheam.py](#4-数据模型--scheampy)
- [5. 核心桥梁 — agent_loader.py](#5-核心桥梁--agent_loaderpy)
- [6. 实时对话 — chat.py](#6-实时对话--chatpy)
- [7. 会话历史 — history.py](#7-会话历史--historypy)
- [8. 数据流详解](#8-数据流详解)
- [9. SSE 事件类型速查表](#9-sse-事件类型速查表)
- [10. MongoDB 集合结构](#10-mongodb-集合结构)
- [11. 已知问题与待办](#11-已知问题与待办)

---

## 1. 系统架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                        FastAPI 应用 (web_main.py)                    │
│                     lifespan: initialize → shutdown                  │
│                     CORS: 允许所有来源                                 │
└──────────┬──────────────────────────────────────────┬───────────────┘
           │                                          │
    ┌──────▼──────┐                           ┌──────▼──────┐
    │  chat.py    │                           │  history.py  │
    │  /api/chat  │                           │ /api/history │
    │  实时对话引擎 │                           │  会话历史管理  │
    └──────┬──────┘                           └──────┬──────┘
           │                                          │
           │         ┌──────────────────┐             │
           └────────►│  agent_loader.py  │◄────────────┘
                     │  (单例，全局唯一)    │
                     │  - Agent 生命周期   │
                     │  - MongoDB 操作    │
                     │  - 沙箱管理       │
                     └────────┬─────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        ┌──────────┐   ┌──────────┐   ┌──────────────┐
        │ MongoDB  │   │ Sandbox  │   │ LangGraph    │
        │ Checkpoint│  │ Manager  │   │ Agent Graph  │
        │ (短期记忆) │  │ (用户沙箱) │  │ (主代理+子代理)│
        └──────────┘   └──────────┘   └──────────────┘
```

**核心协作关系**：

| 模块 | 角色 | 一句话描述 |
|------|------|-----------|
| `chat.py` | **生产者** | 对话过程中实时产生消息并推送 SSE + 保存到 MongoDB |
| `history.py` | **消费者** | 用户查看历史时从 MongoDB 读取并展示 |
| `agent_loader.py` | **桥梁** | 管理 Agent 生命周期，提供 MongoDB 读写操作 |
| `scheam.py` | **契约** | 定义所有请求/响应的 Pydantic 数据模型 |
| `web_main.py` | **入口** | FastAPI 应用生命周期 + 路由注册 |
| `web_config.py` | **配置** | MongoDB 连接信息 + API 元信息 |

---

## 2. 应用入口 — web_main.py

| 项目 | 说明 |
|------|------|
| 文件路径 | `src/api_view/web_main.py` |
| 框架 | FastAPI |
| API 标题 | DeepAgent Chat API |
| API 版本 | 1.0.0 |

### 2.1 生命周期管理

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：初始化 Agent（MongoDB + 沙箱 + 预计算 + 预热）
    await agent_loader.initialize()
    yield
    # 关闭：清理所有沙箱 + 关闭 MongoDB 连接
    await agent_loader.shutdown()
```

### 2.2 路由注册

```python
app.include_router(chat.router,   prefix="/api", tags=["对话"])
app.include_router(history.router, prefix="/api", tags=["历史记录"])
```

所有 API 端点统一挂载在 `/api` 前缀下。

### 2.3 辅助端点

| 方法 | 路径 | 作用 |
|------|------|------|
| GET | `/` | 返回 API 名称、版本、文档链接 |
| GET | `/health` | 健康检查 |

### 2.4 CORS 配置

当前为开发模式：允许所有来源、所有方法、所有请求头。

---

## 3. 配置 — web_config.py

| 配置项 | 值 | 说明 |
|--------|-----|------|
| `MONGODB_URI` | `mongodb://root:root@localhost:27017/?authSource=admin` | MongoDB 连接地址 |
| `MONGODB_DB_NAME` | `langchain_db` | 数据库名称 |
| `MONGODB_CHECKPOINT_COLLECTION` | `checkpoints` | Checkpoint 存储集合 |
| `API_TITLE` | DeepAgent Chat API | API 标题 |
| `API_VERSION` | 1.0.0 | API 版本 |
| `API_DESCRIPTION` | 基于 DeepAgent 的 AI 对话系统 API | API 描述 |

---

## 4. 数据模型 — scheam.py

> 文件路径：`src/agent/scheam.py`

### 4.1 对话相关模型

#### ChatRequest — 对话请求

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | `str` | ✅ | 用户消息 |
| `thread_id` | `str \| None` | ❌ | 会话 ID，为空则创建新会话 |
| `user_id` | `str` | ✅ | 用户唯一标识 |

#### Message — 消息（核心模型，chat 和 history 共用）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 消息唯一标识 |
| `role` | `str` | 消息角色：`user` / `assistant` / `tool` |
| `content` | `str` | 消息内容（默认空字符串） |
| `created_at` | `datetime` | 创建时间 |
| `tool_calls` | `List[Dict] \| None` | 工具调用信息（assistant 消息） |
| `tool_call_id` | `str \| None` | 工具调用 ID |
| `source` | `str \| None` | 消息来源：`main` 或子代理名称 |
| `tool_name` | `str \| None` | 工具名称（仅 role=tool） |
| `tool_status` | `str \| None` | 工具状态：`calling` / `done`（仅 role=tool） |
| `text` | `str \| None` | 工具结果文本（仅 role=tool） |
| `images` | `List[str] \| None` | 工具结果图片 URL 列表（仅 role=tool） |
| `args` | `str \| None` | 工具调用参数（仅 role=tool） |

#### ChatResponse — 对话响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `thread_id` | `str` | 会话 ID |
| `messages` | `List[Message]` | 消息列表 |

### 4.2 历史记录相关模型

#### Session — 会话

| 字段 | 类型 | 说明 |
|------|------|------|
| `thread_id` | `str` | 会话 ID |
| `title` | `str` | 会话标题（取首条用户消息前 50 字） |
| `created_at` | `datetime` | 创建时间 |
| `updated_at` | `datetime` | 最后更新时间 |
| `message_count` | `int` | 消息数量 |

#### SessionListResponse — 会话列表响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessions` | `List[Session]` | 会话列表 |
| `total` | `int` | 会话总数 |
| `page` | `int` | 当前页码 |
| `limit` | `int` | 每页数量 |

#### SessionMessagesResponse — 会话消息响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `thread_id` | `str` | 会话 ID |
| `messages` | `List[Message]` | 消息列表 |

#### DeleteSessionResponse — 删除会话响应

| 字段 | 类型 | 说明 |
|------|------|------|
| `success` | `bool` | 是否成功 |
| `message` | `str` | 响应消息 |

---

## 5. 核心桥梁 — agent_loader.py

> 文件路径：`src/api_view/agent_loader.py`
>
> 单例模式，全局唯一实例 `agent_loader`，被 `chat.py` 和 `history.py` 共同依赖。

### 5.1 类结构

```
AgentLoader (单例)
├── _instance          — 单例实例
├── _mongodb_client    — MongoDB 客户端
├── _initialized       — 初始化标志
├── _precomputed       — 预计算上下文（MCP 工具/YAML 配置）
├── _agent             — 最近创建的 agent graph 引用
│
├── 生命周期方法
│   ├── initialize()           — 启动初始化
│   ├── cleanup_user()         — 销毁用户沙箱
│   └── shutdown()             — 关闭清理
│
├── Agent 实例管理
│   ├── agent (property)       — 获取最近的 Agent 实例
│   └── get_agent_for_user()   — 获取 per-user agent graph
│
├── 配置与会话
│   └── create_config()        — 创建 LangGraph 配置字典
│
├── 状态与消息查询
│   ├── get_state_history()        — 获取历史状态快照列表
│   └── get_current_messages()     — 获取当前消息列表（从 checkpoint）
│
├── 会话查询与删除
│   ├── get_all_thread_ids()       — 获取所有 thread_id
│   ├── get_session_updated_at()   — 获取会话最后更新时间
│   └── delete_session()           — 删除会话（checkpoint + 展示消息）
│
└── 展示消息存取
    ├── save_display_messages()    — 保存展示消息到 MongoDB
    ├── get_display_messages()     — 从 MongoDB 读取展示消息
    └── _truncate_message_fields() — 截断过长字段（> 500KB）
```

### 5.2 初始化流程

```
initialize()
  │
  ├── 1. 创建 MongoDB 连接
  │
  ├── 2. 初始化沙箱管理器（MongoDB 连接 + 索引）
  │      └── sandbox_manager.initialize(mongodb_client)
  │
  ├── 3. 预计算 MCP 工具 + 图表工具 + YAML 配置
  │      └── precompute_agent_context() → _precomputed
  │
  └── 4. 预热第一个沙箱（~15s，首个用户零等待）
         └── sandbox_manager.pre_warm()
```

### 5.3 per-user Agent 获取

```
get_agent_for_user(user_id)
  │
  ├── 1. 获取/创建用户沙箱
  │      └── sandbox_manager.ensure_sandbox_for_user(user_id)
  │
  ├── 2. 创建配置
  │      └── create_config(user_id=user_id)
  │
  ├── 3. 创建 agent graph
  │      └── create_main_agent(config, sandbox_backend, precomputed)
  │
  └── 4. 保留引用（用于状态查询）
         └── self._agent = agent_graph
```

### 5.4 供 chat.py 调用的方法

| 方法 | 用途 |
|------|------|
| `get_agent_for_user(user_id)` | 获取当前用户的 agent graph |
| `create_config(thread_id, user_id)` | 创建 LangGraph 运行配置 |
| `get_display_messages(thread_id)` | 中断恢复时加载已有展示消息 |
| `save_display_messages(thread_id, messages)` | 流结束/中断时保存展示消息 |
| `get_current_messages(thread_id)` | GET `/chat/{thread_id}` 端点获取消息 |

### 5.5 供 history.py 调用的方法

| 方法 | 用途 |
|------|------|
| `get_all_thread_ids()` | 获取所有会话 ID 列表 |
| `get_session_updated_at(thread_id)` | 获取会话更新时间 |
| `get_current_messages(thread_id)` | 回退方案：从 checkpoint 序列化消息 |
| `get_display_messages(thread_id)` | 优先方案：读取流式保存的展示消息 |
| `delete_session(thread_id)` | 删除会话的所有数据 |

---

## 6. 实时对话 — chat.py

> 文件路径：`src/api_view/api/chat.py`
>
> 核心职责：**让用户和 AI Agent 实时对话，边生成边推送 SSE 事件**

### 6.1 API 端点

| 方法 | 路径 | 请求体 | 响应 | 说明 |
|------|------|--------|------|------|
| POST | `/api/chat/stream` | `ChatRequest` | `SSE Stream` | 流式对话（核心端点） |
| POST | `/api/chat/{thread_id}/resume` | `ResumeRequest` | `SSE Stream` | 中断恢复 |
| GET | `/api/chat/{thread_id}` | — | `ChatResponse` | 获取对话消息列表 |
| GET | `/api/chat/{thread_id}/history` | `?limit=50` | `JSON` | 获取会话历史状态列表 |

### 6.2 请求模型

#### ResumeRequest — 中断恢复请求

| 字段 | 类型 | 说明 |
|------|------|------|
| `resume` | `dict` | 恢复数据，格式取决于中断类型 |
| `user_id` | `str \| None` | 用户 ID |

**resume 格式**：

| 中断类型 | resume 格式 | 说明 |
|----------|------------|------|
| 数据补充 | `{"supplement": "用户输入的补充信息"}` | 用户提供缺失字段 |
| HITL 审批 | `{"decisions": [{"type": "approve"}]}` | 审批或拒绝 |

### 6.3 辅助函数

#### 调试日志

| 函数 | 作用 |
|------|------|
| `get_debug_log_path(thread_id)` | 生成日志文件路径：`/tmp/deepagent_debug/stream_{id}_{timestamp}.log` |
| `write_debug_log(filepath, event_type, data, raw_token)` | 追加写入调试日志，记录事件类型、数据、token 信息。失败不影响主流程 |

**日志格式**：
```
[2026-07-08T10:30:00.123456] on_chat_model_stream
   data: {"chunk_index": 1}
   token: {"type": "AIMessageChunk", "content_preview": "你好"}
```

#### 消息处理

| 函数 | 输入 | 输出 | 作用 |
|------|------|------|------|
| `extract_subagent_name(namespace)` | `("main", "tools:code_interpreter", "step_3")` | `"code_interpreter"` | 从 LangGraph namespace 提取子代理名 |
| `is_likely_uuid(text)` | `"3576bba4-42e5-a769-c2f7-ea8444829951"` | `True` | 判断文本是否为 UUID（过滤无意义 ID） |
| `extract_content_from_token(token)` | `AIMessageChunk` | `"你好"` | 从流式 token 提取纯文本（兼容 str/list/dict） |
| `serialize_tool_result(content)` | 工具返回内容 | `{"text": "...", "images": [...]}` | 将工具结果序列化为 text + images 结构 |
| `create_sse_message(data)` | `{"type": "token", "content": "你好"}` | `"data: {...}\n\n"` | 封装 SSE 协议格式 |

**`extract_content_from_token` 处理逻辑**：

```
token.content
├── str   → 直接返回（过滤 UUID）
├── list  → 逐项提取 text 类型，忽略 image_url 等
│         ├── {"type": "text", "text": "..."} → 提取
│         ├── {"type": "image_url", ...}      → 跳过（由 serialize_tool_result 处理）
│         └── 其他 → 兜底转字符串
└── other → 兜底转字符串
```

**`serialize_tool_result` 处理逻辑**：

```
content
├── str  → text=content，正则提取 markdown 图片和 base64 图片
├── list → 逐项分类：
│         ├── {"type": "text"}      → 追加到 text
│         ├── {"type": "image_url"} → 追加到 images
│         ├── {"type": "image"}     → 追加到 images（base64）
│         └── 其他                   → 兜底转字符串追加到 text
└── other → 兜底转字符串

二次扫描：从合并后的 text 中提取遗漏的 markdown 图片和 base64 图片
```

### 6.4 核心函数 `stream_chat_response` 详解

这是整个对话系统的核心，负责流式生成 AI 响应并实时推送给前端。

#### 函数签名

```python
async def stream_chat_response(
    message: str = None,        # 用户消息（初始对话模式）
    thread_id: str = None,      # 会话线程 ID
    resume_data: dict = None,   # 中断恢复数据（恢复模式）
    user_id: str = None,        # 用户 ID
) -> AsyncIterator[str]:        # SSE 格式字符串的异步生成器
```

#### ★ 阶段 1：初始化准备

```
├── 创建 context（user_id, username）
├── 创建 config（thread_id, user_id）
├── 获取 per-user agent graph
├── 初始化 collected_content（累积 AI 文本）
├── 初始化 tool_call_stack（工具调用栈，支持嵌套）
└── 创建调试日志文件
```

#### ★ 阶段 2：根据模式构建 input 和 display_messages

两种调用模式：

| 模式 | 触发条件 | input | display_messages |
|------|---------|-------|------------------|
| 初始对话 | `message` 有值 | `{"messages": [{"role": "user", "content": message}]}` | 新建，含一条 user 消息 |
| 中断恢复 | `resume_data` 有值 | `Command(resume=resume_data)` | 从 MongoDB 加载已有消息 |

#### ★ 阶段 3：流式调用 `agent.astream()`

```python
async for chunk in agent_graph.astream(
    input=current_input,
    config=config,
    context=context,
    stream_mode=["messages", "values"],  # messages=消息流, values=状态流
    subgraphs=True,                       # 启用子代理流式输出
    version="v2",
):
```

**chunk 处理流程**：

```
chunk
├── type == "value"
│   └── 有 interrupts → ★ 3.1 中断处理
│       ├── order_info_supplement  → 发送 interrupt 事件（数据补充）
│       ├── hitl_approval          → 发送 interrupt 事件（审批确认）
│       └── unknown                → 发送 interrupt 事件（透传原始值）
│
│       后续操作：
│       ├── 兜底：所有 calling 工具标记为 done
│       ├── 清理空的 assistant 消息
│       ├── 保存展示消息到 MongoDB
│       ├── 发送 done 事件（interrupted=True）
│       └── return（结束流，等待前端 POST /resume）
│
└── type == "messages"
    ├── ★ 3.2.1 tool_call_chunks → 工具开始调用
    │   ├── tool_name 存在 → 添加到 tool_call_stack
    │   │   ├── 发送 SSE: tool_start
    │   │   └── 添加到 display_messages（role=tool, status=calling）
    │   └── tool_args 存在 → 追加参数到栈顶工具
    │       ├── JSON 合并逻辑（避免字符串拼接破坏 JSON）
    │       ├── 发送 SSE: tool_args
    │       └── 更新 display_messages 中对应工具的 args
    │
    ├── ★ 3.2.2 type == "tool" → 工具执行结果
    │   ├── 序列化工具结果（serialize_tool_result）
    │   ├── 从 tool_call_stack 弹出对应工具
    │   ├── 发送 SSE: tool_result
    │   ├── 更新 display_messages 中对应工具（text, images, status=done）
    │   ├── 发送 SSE: tool_end
    │   └── 兜底：确保工具标记为 done
    │
    └── ★ 3.2.3 AI 文本内容（排除工具调用和工具结果）
        ├── 提取纯文本（extract_content_from_token）
        ├── 发送 SSE: token（打字机效果）
        └── 更新 display_messages：
            ├── 最后一条是同 source 的 assistant → 追加内容
            └── 否则 → 新建 assistant 消息
```

#### ★ 阶段 4：流正常结束

```
├── 兜底：所有 calling 工具标记为 done
├── 清理空的 assistant 消息
├── 保存展示消息到 MongoDB：
│   ├── 初始对话 → 追加到已有历史（existing + display_messages）
│   └── resume 模式 → 直接保存（display_messages 已含历史）
├── 写调试日志：STREAM_DONE
└── 发送 SSE: done（thread_id, content）
```

#### ★ 阶段 5：异常处理

```
├── 写调试日志：STREAM_ERROR
└── 发送 SSE: error（message）
```

---

## 7. 会话历史 — history.py

> 文件路径：`src/api_view/api/history.py`
>
> 核心职责：**管理对话的"存档"——列表、查看、删除**

### 7.1 API 端点

| 方法 | 路径 | 参数 | 响应 | 说明 |
|------|------|------|------|------|
| GET | `/api/history` | `page=1&limit=20` | `SessionListResponse` | 分页获取会话列表 |
| GET | `/api/history/{thread_id}/messages` | — | `SessionMessagesResponse` | 获取会话消息历史 |
| DELETE | `/api/history/{thread_id}` | — | `DeleteSessionResponse` | 删除会话 |
| PATCH | `/api/history/{thread_id}?title=xxx` | `title` | `JSON` | 更新会话标题（**待实现**） |

### 7.2 辅助函数

#### 消息属性提取

| 函数 | 作用 | 说明 |
|------|------|------|
| `get_message_attr(msg, attr, default)` | 从消息对象获取属性 | 兼容 dict 和对象两种格式 |
| `get_message_role(msg)` | 获取消息角色 | LangChain → 前端映射：`human→user`、`ai→assistant`、`ToolMessage→tool` |
| `get_message_content(msg)` | 提取纯文本内容 | 兼容 str/list/dict 三种 content 格式 |

**`get_message_role` 映射逻辑**：

```
msg.role 属性:
  "human"     → "user"
  "ai"        → "assistant"
  其他        → 原值保留

msg 类型推断（无 role 属性时）:
  HumanMessage → "user"
  AIMessage    → "assistant"
  ToolMessage  → "tool"
  其他         → "assistant"
```

**`get_message_content` 处理逻辑**：

```
msg.content
├── dict → 取 content.get("content", "")
├── str  → 直接返回
├── list → 逐项提取：
│         ├── str           → 直接追加
│         ├── {"type":"text"} → 取 text 字段
│         ├── {"text": ...}   → 取 text 字段
│         ├── {"content":...} → 取 content 字段
│         └── 其他             → 转字符串追加
└── dict → 取 "text" 字段，无则转字符串
```

#### 图片提取

**`extract_images_from_content(content)`**：

```
content
├── list → 逐项提取：
│         ├── {"type": "image_url", "image_url": {"url": "..."}} → 提取 URL
│         └── {"type": "image", "data": "base64..."}              → 提取 base64
│
└── str → 正则提取：
          ├── ![alt](url)                      → markdown 图片语法
          └── data:image/...;base64,...         → 内嵌 base64 图片
```

#### 工具调用格式化

**`format_tool_calls(msg)`**：

从 AIMessage 中提取 `tool_calls` 列表，转换为统一格式：

```json
[
  {
    "name": "工具名",
    "args": "参数（字符串）",
    "result": "",
    "source": "main"
  }
]
```

### 7.3 核心函数 `serialize_messages_from_checkpoint` 详解

将 MongoDB checkpoint 中的 LangChain 原始消息转换为前端需要的 Message 格式。

#### 处理逻辑

```
遍历 messages:
│
├── role == "assistant"
│   ├── 有 content → 添加 AI 文本消息
│   │   {id, role:"assistant", content, source:"main", created_at}
│   │
│   └── 有 tool_calls → 为每个 tool_call 创建占位消息
│       {id, role:"tool", tool_name, args, text:"", images:[],
│        source:"main", tool_status:"calling", created_at}
│
├── role == "tool"
│   ├── 尝试匹配前面同名的 calling 状态 tool 占位消息
│   │   ├── 找到 → 填充 text/images，标记 tool_status="done"
│   │   └── 未找到 → 新建 tool 消息（tool_status="done"）
│   └── 提取 images（extract_images_from_content）
│
└── role == "user"
    └── 直接添加用户消息
        {id, role:"user", content, created_at}
```

#### 为什么要拆分 AIMessage？

LangGraph 的 checkpoint 中，一条 AIMessage 可能同时包含文本内容和工具调用：

```
原始 AIMessage:
  content: "我来帮你搜索一下"
  tool_calls: [{name: "web_search", args: {"query": "..."}}]

转换后：
  1. {role: "assistant", content: "我来帮你搜索一下"}
  2. {role: "tool", tool_name: "web_search", args: ..., tool_status: "calling"}
     └── 后续 ToolMessage 到达时填充结果，变为 tool_status: "done"
```

这样前端展示与流式过程一致：AI 说"我来搜索" → 工具调用中 → 工具返回结果。

### 7.4 会话列表获取流程 (`GET /history`)

```
get_sessions(page, limit)
│
├── 1. 获取所有 thread_id
│      └── agent_loader.get_all_thread_ids()
│
├── 2. 遍历每个 thread_id
│      ├── 获取当前消息列表
│      │   └── agent_loader.get_current_messages(thread_id)
│      ├── 提取标题：首条 user 消息前 50 字（超长加 "..."）
│      ├── 计算消息数量：len(messages) // 2
│      └── 获取更新时间
│          └── agent_loader.get_session_updated_at(thread_id)
│
├── 3. 按更新时间倒序排序
│
└── 4. 分页返回
       ├── total: 总数
       ├── page: 当前页码
       └── sessions: 当前页的会话列表
```

### 7.5 会话消息获取流程 (`GET /history/{thread_id}/messages`)

```
get_session_messages(thread_id)
│
├── 1. 优先从 MongoDB 读取流式保存的展示消息
│      └── agent_loader.get_display_messages(thread_id)
│
├── 2. 如果不存在 → 回退到 checkpoint 序列化（兼容旧数据）
│      ├── agent_loader.get_current_messages(thread_id)
│      └── serialize_messages_from_checkpoint(messages)
│
└── 3. 将字典列表转换为 Message 对象列表返回
```

**为什么有两级读取？**

| 优先级 | 来源 | 包含子代理消息 | 适用场景 |
|--------|------|---------------|---------|
| 1（优先） | `display_messages` 集合 | ✅ 包含 | chat.py 流式过程中保存的完整消息 |
| 2（回退） | checkpoint 序列化 | ❌ 不包含 | 旧会话数据，无 display_messages 记录 |

---

## 8. 数据流详解

### 8.1 初始对话完整流程

```
前端                    chat.py                  Agent Graph              MongoDB
 │                        │                         │                      │
 │  POST /chat/stream     │                         │                      │
 │───────────────────────►│                         │                      │
 │                        │  get_agent_for_user()   │                      │
 │                        │────────────────────────►│                      │
 │                        │    agent_graph          │                      │
 │                        │◄────────────────────────│                      │
 │                        │                         │                      │
 │                        │  astream(input)          │                      │
 │                        │────────────────────────►│                      │
 │                        │                         │                      │
 │   SSE: token "你"      │  chunk(AI文本)          │                      │
 │◄───────────────────────│◄────────────────────────│                      │
 │   SSE: token "好"      │  chunk(AI文本)          │                      │
 │◄───────────────────────│◄────────────────────────│                      │
 │                        │                         │                      │
 │   SSE: tool_start      │  chunk(tool_call)       │                      │
 │◄───────────────────────│◄────────────────────────│                      │
 │   SSE: tool_args       │  chunk(tool_args)       │                      │
 │◄───────────────────────│◄────────────────────────│                      │
 │                        │                         │──执行工具──►          │
 │   SSE: tool_result     │  chunk(ToolMessage)     │                      │
 │◄───────────────────────│◄────────────────────────│                      │
 │   SSE: tool_end        │                         │                      │
 │◄───────────────────────│                         │                      │
 │                        │                         │                      │
 │   SSE: done            │  流结束                  │                      │
 │◄───────────────────────│                         │                      │
 │                        │  save_display_messages() │                      │
 │                        │────────────────────────────────────────────────►│
```

### 8.2 中断恢复流程

```
前端                    chat.py                  Agent Graph              MongoDB
 │                        │                         │                      │
 │   SSE: interrupt       │  chunk(interrupt)       │                      │
 │◄───────────────────────│◄────────────────────────│                      │
 │   SSE: done            │  保存部分消息            │                      │
 │◄───────────────────────│───────────────────────────────────────────────►│
 │                        │  return（流结束）        │                      │
 │                        │                         │                      │
 │  ... 用户补充信息/审批 ...                        │                      │
 │                        │                         │                      │
 │  POST /resume          │                         │                      │
 │───────────────────────►│                         │                      │
 │                        │  get_display_messages()  │                      │
 │                        │◄───────────────────────────────────────────────│
 │                        │  astream(Command(resume))│                      │
 │                        │────────────────────────►│                      │
 │   SSE: token ...       │  继续流式输出            │                      │
 │◄───────────────────────│◄────────────────────────│                      │
```

### 8.3 历史查看流程

```
前端                    history.py               agent_loader            MongoDB
 │                        │                         │                      │
 │  GET /history          │                         │                      │
 │───────────────────────►│                         │                      │
 │                        │  get_all_thread_ids()    │                      │
 │                        │────────────────────────►│                      │
 │                        │                         │  查询 distinct tid   │
 │                        │                         │─────────────────────►│
 │                        │    [tid1, tid2, ...]    │                      │
 │                        │◄────────────────────────│◄─────────────────────│
 │                        │                         │                      │
 │                        │  对每个 tid 获取消息/时间  │                      │
 │                        │────────────────────────►│                      │
 │                        │                         │  查询 checkpoint     │
 │                        │                         │─────────────────────►│
 │  SessionListResponse   │                         │                      │
 │◄───────────────────────│                         │                      │
 │                        │                         │                      │
 │  GET /history/tid/messages │                     │                      │
 │───────────────────────►│                         │                      │
 │                        │  get_display_messages()  │                      │
 │                        │────────────────────────►│  查询 display_messages│
 │                        │                         │─────────────────────►│
 │                        │    messages or None     │                      │
 │                        │◄────────────────────────│◄─────────────────────│
 │                        │                         │                      │
 │                        │  (如果 None) 回退到 checkpoint 序列化           │
 │                        │────────────────────────►│                      │
 │  SessionMessagesResponse │                       │                      │
 │◄───────────────────────│                         │                      │
```

### 8.4 展示消息（display_messages）的生命周期

```
初始对话:
  用户发消息 → display_messages=[user消息]
  → 流式处理 → 逐步添加 assistant/tool 消息
  → 流结束 → 保存到 MongoDB (追加到已有历史)

中断对话:
  用户发消息 → display_messages=[user消息]
  → 中断发生 → 保存部分消息到 MongoDB
  → return（流结束）

恢复对话:
  加载已有 → display_messages=[历史消息]
  → 继续流式处理 → 逐步添加新消息
  → 流结束 → 保存到 MongoDB (覆盖旧记录)
```

---

## 9. SSE 事件类型速查表

### chat.py 发出的 SSE 事件

| 事件类型 | 方向 | 数据字段 | 说明 |
|----------|------|---------|------|
| `token` | 服务端→前端 | `content`, `source` | AI 文本内容（打字机效果） |
| `tool_start` | 服务端→前端 | `tool_call_id`, `tool_name`, `source` | 工具开始调用 |
| `tool_args` | 服务端→前端 | `args`, `source` | 工具参数（流式追加） |
| `tool_result` | 服务端→前端 | `tool_name`, `tool_call_id`, `text`, `images`, `source` | 工具执行结果 |
| `tool_end` | 服务端→前端 | `tool_name`, `tool_call_id`, `source` | 工具调用结束 |
| `interrupt` | 服务端→前端 | `interrupt_type`, `thread_id`, + 类型特定字段 | Human-in-the-Loop 中断 |
| `done` | 服务端→前端 | `thread_id`, `content`, `interrupted?` | 流结束标记 |
| `error` | 服务端→前端 | `message` | 异常信息 |

### interrupt 事件的三种子类型

| interrupt_type | 附加字段 | 说明 |
|---------------|---------|------|
| `order_info_supplement` | `missing_fields`, `collected_data` | 数据补充中断（缺字段） |
| `hitl_approval` | `action_requests`, `review_config` | HITL 审批中断（需人工确认） |
| `unknown` | `interrupt_value` | 未知中断类型（透传原始值） |

---

## 10. MongoDB 集合结构

### 10.1 checkpoints 集合

> 由 `MongoDBSaver` (langgraph-checkpoint-mongodb) 自动管理

| 字段 | 类型 | 说明 |
|------|------|------|
| `thread_id` | `str` | 会话线程 ID |
| `checkpoint` | `dict` | Checkpoint 数据（含 channel_values.messages） |

**用途**：Agent 的短期记忆，保存完整的 LangChain 消息历史。

### 10.2 session_display_messages 集合

> 由 `agent_loader.py` 的 `save_display_messages` / `get_display_messages` 管理

| 字段 | 类型 | 说明 |
|------|------|------|
| `thread_id` | `str` | 会话线程 ID |
| `index` | `int` | 消息顺序索引 |
| `message` | `dict` | 消息内容（Message 格式） |
| `created_at` | `datetime` | 保存时间 |

**索引**：`{thread_id: 1, index: 1}`

**用途**：保存流式过程中生成的完整展示消息（包含子代理消息），供历史查看时读取。

**与 checkpoints 的区别**：

| 对比项 | checkpoints | session_display_messages |
|--------|------------|------------------------|
| 数据格式 | LangChain 原始消息 | 前端友好的 Message 格式 |
| 子代理消息 | 不包含 | 包含 |
| 图片提取 | 未处理 | 已提取为 images 列表 |
| 工具状态 | 无 | 有 tool_status (calling/done) |
| 写入时机 | Agent 自动保存 | chat.py 流结束/中断时保存 |
| 读取优先级 | 低（回退方案） | 高（优先方案） |

---

## 11. 已知问题与待办

### 代码 Bug

| 位置 | 问题 | 严重程度 |
|------|------|---------|
| `agent_loader.py:252` | `get_all_thread_ids()` 条件写反：`if self._mongodb_client is not None: return []`，应为 `is None` | 🔴 严重 — 永远返回空列表 |
| `agent_loader.py:272` | `get_session_updated_at()` 同样的条件写反 | 🔴 严重 — 永远返回当前时间 |
| `agent_loader.py:303` | `delete_session()` 同样的条件写反 | 🔴 严重 — 永远返回 False |
| `agent_loader.py:358` | `save_display_messages()` 同样的条件写反 | 🔴 严重 — 永远返回 False |
| `agent_loader.py:400` | `get_display_messages()` 同样的条件写反 | 🔴 严重 — 永远返回 None |
| `agent_loader.py:372` | `_truncate_message_fields(msg, i)` 传了多余参数 `i`，方法只接受 `msg` | 🟡 中等 — 运行时不报错但不符合预期 |
| `agent_loader.py:284` | `"-id"` 应为 `"_id"`（typo） | 🟡 中等 — 逻辑分支永远不会命中 |
| `history.py:256` | `limit` 参数 `ge=100` 应为 `le=100`，当前要求 limit≥100 | 🟡 中等 — 分页参数无法正常使用 |
| `history.py:218` | `serialize_messages_from_checkpoint` 中 tool 占位消息缺少 `tool_status` 字段 | 🟡 中等 — 后续匹配 `prev["tool_status"]` 会 KeyError |
| `history.py:10` | `from docutils.nodes import title` 未使用且引入无关依赖 | 🟢 轻微 |
| `chat.py:12` | `from idlelib.undo import Command` 应为 `from langgraph.types import Command` | 🔴 严重 — 使用了错误的 Command 类 |
| `chat.py:150` | `from agent.main_agent import create_main_agent` 在 agent_loader.py 中使用但未导入 | 🟡 中等 — 运行时会 NameError |

### 功能待办

| 位置 | 说明 |
|------|------|
| `history.py:373` | `update_session_title` 端点返回"标题更新功能待实现" |
| `agent_loader.py:144-155` | 多处 `# todo` 标记的沙箱/配置逻辑待完善 |

### 优化建议

| 建议 | 说明 |
|------|------|
| 提取公共消息处理逻辑 | `chat.py` 的 `extract_content_from_token` 和 `history.py` 的 `get_message_content` 功能重叠，可提取为公共模块 |
| 统一 SSE 事件类型定义 | 当前事件类型散落在代码中，建议用枚举集中管理 |
| 添加消息类型判断 | `chat.py` 中 `token.type == "tool"` 判断依赖 LangChain 内部实现，建议更健壮的类型判断 |
| MongoDB 连接池配置 | 当前使用默认连接池，生产环境建议显式配置 |
| 错误日志规范化 | 当前使用 `print()` 输出错误，建议替换为标准 logging 模块 |