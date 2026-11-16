# ERP_OPENCLAW Agent 代码跟读路线

---

## 一、 跟读路线（第 0-7 步）

### 第 0 步： 全局视图：进程拓扑与架构全貌

`start_web.py:328 main()` 按序拉起 4 个进程，加上 2 个外部依赖。**先记住这张表，后面所有"为什么这里要落盘 / 为什么要轮询"的疑问都来自它。**

| 面 | 进程 / 地址 | 启动位置 | 职责 |
|---|---|---|---|
| 呈现 | Vite :3000 → `/api` 代理到 8090 | `frontend/vite.config.js:8-15` | SSE 消费、run 归属、任务轮询 |
| 控制 | FastAPI :8090 | `start_web.py:161-180`、`src/api_view/web_main.py` | 会话编排、SSE、历史读写 |
| 决策 | deepagents graph（进程内，每请求新建） | `src/agent/main_agent.py:379` | 工具与子 Agent 编排 |
| 后台作业 | Agent Protocol :2024 | `langgraph dev`，`langgraph.json` 声明 `procurement_analyst_async` | 异步子 Agent 独立进程 |
| 执行 | OpenSandbox 远端 | 外部；env 默认 `http://localhost:8081`（`env_utils.py:46`） | Agent 的「手」，所有 execute / 文件操作 |
| 数据 | MCP :8000 `/mcp` | `src/mcp_server/server_main.py`、`server_config.py:26-28` | ERP 防腐层，背后是 Java API |
| 状态 | MongoDB :27017 | 外部 | checkpoint 与 Store（用户偏好/持久化技能/会话标题）都落这里 |

> 自测：能说出「哪几个进程共享 Python 对象，哪几个不共享」——这个问题的答案决定了后面一半的设计。
>
> **唯一共享 Python 对象的地方是 `:8090` 的进程内（所以三把锁、预计算、沙箱缓存都设在这里）；**
>
> **其余一切跨边界的东西——对话、偏好、任务结果、沙箱 ID——全都锚在外部（MongoDB / 落盘 JSON），这正是文档第 4 节「三问」里“跨请求、跨进程时它靠什么活下来”的答案。**
>
> 文档里 `global_sandbox_manager` 那条（`STATE_FILE = global_sandbox.json`）我核对了，全局沙箱走的是**落盘 JSON**，而 per-user 沙箱（`sandbox_manager.py`）走 **MongoDB** 持久化——两种机制、同一个目的，读文档时别混成一条。

---

### 第 1 步：前置准备：阅读系统宪法与规格

**读什么**

- `src/agent/memory/AGENTS.md` —— 全文精读
- `src/agent/memory/prompts.py:18` —— 紧凑人设 `system_prompt`
- `src/agent/scheam.py` —— `ProcurementContext`(:12) + API 的 pydantic I/O 模型

**为什么先读它**

`AGENTS.md` 是这个系统的**宪法**，它以自然语言规定了：什么必须委派子 Agent、记忆文件什么格式、什么时候必须主动调用 工具compact_conversation 压缩对话历史，释放上下文空间、技能怎么扫、安全边界在哪。后面你读的每一行代码，都是在实现这份文件的某一条。**先读它，你才知道代码在为什么服务；后读它，你只会觉得代码莫名其妙。**

注意 `AGENTS.md` 的运行时位置：它被上传到沙箱 `/AGENTS.md` 且对 Agent **只读**（`main_agent.py:268` Phase 2 上传，`create_deep_agent` 的 `memory=[AGENTS_MD_FILENAME]` 加载）——「宪法不允许被 Agent 改写」本身就是一个设计决策。

---

### 第 2 步：链路追踪：一条消息的端到端流转

只看「一条消息怎么进出」，不看业务逻辑。

**读什么（按调用顺序）**

```python
App.vue:415 handleSend
  → api/chat.js:44 streamChat        (POST /api/chat/stream)
  → api/chat.py:672 chat_stream
  → api/chat.py:642 _stream_with_thread_lock
  → api/chat.py:237 stream_chat_response
  → api/chat.py:312 agent_graph.astream(stream_mode=["messages","values"], subgraphs=True)
```

停在 `astream` 这一行，不要继续往 graph 里面钻（第 3 步才钻）。然后读 `api/chat.js:180-264` 的 switch 分发，把 **SSE 事件类型清单**抄下来：

`token` / `tool_start` / `tool_args` / `tool_result` / `tool_end` / `queued` / `interrupt`（子类型 `order_info_supplement`、`hitl_approval`、`unknown`）/ `done`（带 `interrupted` 标记）/ `error`

**两个值得注意的点**

- `App.vue:418-420`：新会话必须在**发送前**就 `crypto.randomUUID()` 定下 `thread_id`，否则连续两条消息会落到不同 thread，既拿不到上下文也共享不了 `async_tasks`。
- `chat.py:230`：`user_id` 默认值是 `"liqiang"`，没有鉴权。

---

### 第 3 步：核心装配：Graph Factory 与中间件栈

**读什么**：`src/agent/main_agent.py:200-406` 全文（`create_main_agent()`）+ `src/agent/config.py` 全文；想先热身就读 `:137-193` 的 `precompute_agent_context()`。

这是所有设计决策汇聚成一个函数的地方。装配分**两条轨道**：

**Phase 0.x = 启动只跑一次的预计算**（`precompute_agent_context()`）；**Phase 1-9 = 每次请求的装配**（`create_main_agent()`）；

0.x 的产物通过 `PrecomputedContext` 交给请求轨道，在 Phase 3 消费。先读函数体开头的总纲 `main_agent.py:221-229`——「★ 核心架构设计（Graph Factory 模式）」三条：**无状态计算（每次重建）／记忆不丢（状态外置）／故障无感（代理热替换）**，这是本步的地图。然后按 Phase 读（`定位符号` 一列是行号漂了之后的 grep 锚点）：

------

|  Phase  | 轨道     | 代码位置                | 核心定位符号                                             | 做什么                   | 为什么必须在这里                                             |
| :-----: | :------- | :---------------------- | :------------------------------------------------------- | :----------------------- | :----------------------------------------------------------- |
| **0.1** | 启动一次 | `main_agent.py:153-162` | `load_mcp_tools()`                                       | MCP 工具加载             | 连 MCP 慢，只做一次                                          |
| **0.2** | 启动一次 | `main_agent.py:164-172` | `create_generate_chart_tool`                             | 26 个图表工具合并为 1 个 | 不依赖用户沙箱                                               |
| **0.3** | 启动一次 | `main_agent.py:174-182` | `load_subagent_configs()`                                | 子 Agent YAML 加载       | 不依赖用户沙箱                                               |
|  **1**  | 每请求   | `main_agent.py:234-263` | `backend = CompositeBackend()`                           | 路径路由装配             | 0.7版移除工厂模式必须传实例；闭包防并发覆盖（见 `:249-254`） |
|  **2**  | 每请求   | `main_agent.py:265-268` | `upload_files`                                           | 上传只读 `AGENTS.md`     | 宪法进执行环境                                               |
|  **3**  | 每请求   | `main_agent.py:270-273` | `Phase 3` (注释锚点)                                     | 取回 Phase 0.x 的产物    | 两条轨道的交接点                                             |
|  **4**  | 每请求   | `main_agent.py:275-284` | `create_assign_skill_tool()`<br>`create_download_tool()` | 创建依赖沙箱的工具       | 工具闭包捕获沙箱句柄                                         |
|  **5**  | 每请求   | `main_agent.py:286-301` | `available_tools = (`                                    | 汇总工具池               | MCP + 内置 + 图表 + HITL                                     |
|  **6**  | 每请求   | `main_agent.py:305-310` | `extra_middleware = {`                                   | 子 Agent 中间件字典      | 中间件无法写进 YAML，只能代码注入                            |
|  **7**  | 每请求   | `main_agent.py:318-344` | `sync_subagent_configs`<br>`async_subagents`             | 同步/异步子 Agent 分流   | `procurement-analyst` 被剔除并改为异步声明                   |
|  **8**  | 每请求   | `main_agent.py:350-375` | `main_middleware = [`                                    | 10 层中间件栈            | 顺序承载语义                                                 |
|  **9**  | 每请求   | `main_agent.py:377-403` | `create_deep_agent(`                                     | 传入 11 个参数完成装配   | 核心锚点：关注 checkpointer 与 context_schema                |

------

**读到什么程度算够**：能在 `create_deep_agent` 每一行旁边写出「这行实现了 `AGENTS.md` 里的哪条要求」，以及能复述中间件那 10 行注释的顺序为什么不能换。

**主 Agent 对外只暴露 3 个工具**（`tools=[web_search, assign_skill, download_sandbox_file]`，`:384`），其余全部下沉给子 Agent——这是「主 Agent 只做路由，不做业务」的直接证据。

---

### 第 4 步：细节深潜：可靠性、记忆与能力支线

**三条互不依赖。**

#### 4a 可靠性线：自愈、翻译与熔断的闭环

`sandbox_health.py` → `tool_error.py` → `sandbox_breaker.py`，三个加起来 474 行，却构成一个完整闭环：**自愈 → 翻译 → 熔断**。

关键交叉点：`tool_error.py:131-134` 把异常变成 `ToolMessage(status="error")`，而 `sandbox_breaker.py` 只有在遍历对话历史（倒叙遍历），**成功匹配到 `status=="error"` 这个条件时，错误计数器才会加 1**。如果没有前者生成这种特定格式的消息，熔断器永远找不到它要统计的错误，也就永远不会触发。

#### 4b 记忆线：双视图映射与并发控制边界

**总纲：同一份数据，两个视图。**

| 视图 | 使用者 | 形态 | 通道 |
|---|---|---|---|
| 文件 | Agent | `/memories/{uid}/preferences.md` | `read_file` / `write_file` 工具 |
| Store 条目 | middleware | `namespace=(user_id,)` + key | LangGraph Store API（`aget` / `aput`） |

> 两视图经 CompositeBackend 前缀路由（`main_agent.py:247-262`）落到同一物理存储（`config.py` 的 `STORE`——`MongoDBStore`，数据落 MongoDB `agent_store` 集合，重启不丢）。耦合点是「路径 ↔ (namespace, key)」这条**命名约定**，不是接口——一侧改写法，另一侧不报错、只静默失效。
>
> **隔离靠闭包，不靠变量**：`namespace=lambda _rt: (user_id,)`（`:255`）必须写成 lambda，因为 graph 每请求新建、多用户并发组装——闭包把当前请求的 user_id 锁在这个函数里，直接传变量则可能被后续请求覆盖（`:251-254`）。`_rt` 是框架注入的 runtime，这里用不到，下划线前缀明示忽略（`:254`）。对照 `/persisted-skills/` 用的是**全局** namespace `("skills",)`、不带 user_id（`:259`）：技能不区分用户，这个选择在权限模型上是有代价的（见扩展思维 D）。

**注入侧（读回）** —— `context_injection.py`，90 行：

- 钩子：`before_agent` / `abefore_agent`（`:37` / `:77`）
- 形式：`{"messages": [SystemMessage]}` → `add_messages` reducer **追加**，每轮累积一条
- 内容：user_id / username + 记忆路径 + 「请先 read_file」
- 取值：`getattr(runtime, "context", None)`（`:50`，`:54`/`:59` 兼容 dict / dataclass 两种形态）
- 关键点：middleware 只注入「去读」指令，自己不读文件；真正读取是 Agent 调 `read_file` 经路由落 Store

**回写侧（写入）** —— `memory_update.py`，515 行，五步链路（最后一列是实测状态）：

| 步骤  | 阶段动作     | 核心代码锚点                           | 核心机制与容错策略                                           | 当前状态           |
| :---- | :----------- | :------------------------------------- | :----------------------------------------------------------- | :----------------- |
| **1** | **前置过滤** | `_is_meaningful_erp_exchange` `:80`    | 词表过滤问候/闲聊；若无 ERP 关键词，兜底检查是否有子 Agent（`task`）调用。避免无意义请求浪费 LLM Token。 | 正常（省 Token）   |
| **2** | **实体抽取** | `_extract_entities` `:173`             | 调用 `SUMMARY_MODEL` 提取 JSON；**容错**：兼容 LLM 输出被 Markdown 包裹的情况，解析失败则静默跳过。 | 正常               |
| **3** | **加锁读取** | `store.aget` `:343`                    | 进入 `_get_user_lock(user_id)` 临界区；读取失败仅打 warning 并跳过。**防并发**：单进程内同用户回写串行化。 | 正常（防并发）     |
| **4** | **骨架兜底** | `_DEFAULT_PREFERENCE_LINES` `:363-366` | **关键分支**：若为空文件（新用户），强制注入 `preferred_*` 四个基础字段，确保 LLM 后续 `edit_file` 不扑空。 | 正常（兜底）       |
| **5** | **幂等合并** | `_merge_preferences` `:388`            | 降序删旧区块 → 新数据前置 → 去重。**防膨胀**：内置滑动窗口限流，最多保留 10 个供应商、5 条查询。 | 正常               |
| **6** | **安全落盘** | `store.aput` `:373`                    | 格式化为带时间戳的 dict 写入 Store，随后释放锁。异常被 `except Exception` 全局兜底吞掉。 | 正常（见并发边界） |

**三个工程边界**：

| # | 边界 | 真实后果与演进方向 |
|---|---|---|
| **1** | **写入非确定性**<br/>写什么完全由 LLM 决定 | **后果**：同一轮对话两次运行，产出的抽取结果可能不同。<br/>**限制**：不能按常规的确定性逻辑去写单元测试。 |
| **2** | **并发窗口非原子**<br/>read-modify-write 不是原子操作 | **现状**：单进程内已通过 `_get_user_lock`（`:341`）持锁解决，同用户串行、跨用户并行。<br/>**演进**：一旦多进程部署，进程内锁失效，必须升级为 **MongoDB 乐观锁（版本号）或 Redis 分布式锁**。 |
| **3** | **静默降级吞异常**<br/>两层 `except Exception`（`:344` / `:379`） | **现状**：异常被吞掉，业务不崩。dev 日志调成 `WARNING` 后，至少能在控制台看到痕迹。<br/>**风险**：一旦线上出现大面积静默失败，缺乏告警机制（Alert），排查依然困难。 |

#### 4c 能力线：渐进式披露与单向同步拓扑

**总纲：三个存储位置、一个单向拓扑、唯一写路径。**

| 位置 | 身份 | 锚点 |
|---|---|---|
| `src/skills/` | 开发者预置，随仓库走 | `config.py:78 LOCAL_SKILLS_DIR` |
| `Store ("skills",)` | 用户持久化，跨会话，无用户隔离 | `SKILLS_STORE_NAMESPACE` |
| 沙箱 `/skills/` | 运行时工作副本，Agent `ls` 到的就是它 | 沙箱文件系统 |

**三条路径拓扑对照（单向同步与唯一写路径）**

技能生命周期在系统中分属三个位置：`本地 src/skills/`（预置）、`沙箱 /skills/`（运行时工作副本）、`Store 持久化`（用户私有）。它们在同步方向上呈现严格的单向拓扑：

| 路径组件                          | 数据流向                  | 触发时机                                         | 同步粒度                      | 底层实现锚点                                                 |
| :-------------------------------- | :------------------------ | :----------------------------------------------- | :---------------------------- | :----------------------------------------------------------- |
| **① SkillsSyncMiddleware**        | 本地 `src/skills/` ➡️ 沙箱 | 每轮 `before_agent` / `abefore_agent`            | **增量比对** 批量 `md5sum`    | `_fetch_remote_hashes` (`:119`) 加锁比对 `_sync_files` (`:213`) 批量上传差异 (`:244`) |
| **② UserSkillsRestoreMiddleware** | Store 持久化 ➡️ 沙箱       | 每轮 `abefore_agent` *(注：同步钩子为空)*        | **全量覆盖** 按存在性，无哈希 | 异步查 Store指定命名空间下的所有条目 `asearch` (`:102`) 批量上传到沙箱 `aupload_files` (`:66`) |
| **③ assign_skill 工具**           | 沙箱 ➡️ Store 持久化       | Agent 主动调用 *(唯一写路径)*`main_agent.py:278` | **单技能**                    | 下载沙箱文件 `download_files` (`:134`) 写入 Store `aput` (`:145`) |

**五大硬核实现细节（源码级拆解）**

这两百多行代码里，藏着 5 个极度考究的工程细节，它们共同支撑了“无状态计算”下的技能热更与并发安全：

- | 细节亮点                  | 源码锚点                        | 深度拆解（为什么这么写？）                                   |
  | :------------------------ | :------------------------------ | :----------------------------------------------------------- |
  | **热更新靠消息 不靠重启** | `_make_notification` `:252`     | **机制**：检测到技能变更时，注入一条 `SystemMessage`（如“技能已更新，请 `ls /skills/`”）。                           **收益**：Agent 在下一轮会自动发现新能力，全程无需重启服务。 |
  | **阻塞 IO 丢线程池**      | `run_in_executor` `:205`        | **痛点**：`_sync_files` 包含远程 `execute` 和文件上传，若直接在异步 `abefore_agent` 中运行，会卡死整个事件   循环。                                                                                                                                                                                   **解法**：利用 `loop.run_in_executor` 将同步阻塞逻辑丢进后台线程池。 |
  | **O(1) 批量哈希比对**     | `_fetch_remote_hashes` `:119`   | **机制**：摒弃逐文件检查，改用一次 `find ... -exec md5sum {} +` 命令拿回沙箱所有哈希，在内存中比对。     **容错**：如果命令失败或目录为空，兜底为“全量上传”，宁可多传也绝不让沙箱缺技能。 |
  | **排除编译垃圾文件**      | `_should_skip` `skills_sync.py` | **痛点**：Python 的 `__pycache__` 和 `.pyc` 文件每次运行都会重新生成，哈希不断变化。                                         **解法**：在扫描时强制跳过，避免每轮触发无意义的海量上传。 |
  | **per-user 并发锁**       | `_sync_lock_for` `:40`          | **痛点**：同用户的多个会话共享同一个沙箱，而底层的 `_THREAD_LOCKS` 只按 thread 串行。两路并发会同时执行 `_sync_files`，导致文件互相覆盖。                                                                                                                                **解法**：加一把按 `user_id` 维度的进程内锁。第二路进来时发现已更新，直接 no-op 空转。 |

------

**核心铁律**：
**middleware 只负责“把技能灌进沙箱”，绝不负责“把技能存回数据库”。** 沙箱里的技能更新，只有通过 Agent 主动调用 `assign_skill` 工具这一条路才能被真正持久化。不要把“界面上现在有的技能”误认为是“已经安全落盘的技能”。

**框架侧的发现机制**：
deepagents 按 `skills=["/skills/main/"]`（`main_agent.py:382`）用 `backend.ls` 扫描 `SKILL.md`——只有 frontmatter 的 `name` / `description` 会常驻上下文，正文按需读取，即渐进式披露（Progressive Disclosure）。同时，通过 `SCOPE_MAP`（`config.py:105-109`）实现 scope 到子 Agent 的精准权限映射。

---

### 第 5 步：执行面剖析：沙箱抽象层与生命周期管理

> **建议阅读顺序：自底向上。从最底层的 SDK 适配一路向上，看至跨进程的全局管理器，理解这套闭环的沙箱运行时系统。

1. **`custom_opensandbox.py`（底层适配层）**
   - **职责**：把第三方 SDK `SandboxSync` 适配成 deepagents 的 `BaseSandbox` 协议。
   - **核心亮点**：只实现了 `execute/upload/download` 三个底层方法，而 `ls/read/write/glob` 等全由框架基类基于 `execute` 以 Shell 实现，项目零覆写。
   - **细节**：
     - 执行前强行注入 `PATH`（`:110-119`），解决非交互式 shell 不加载 `/etc/profile` 导致找不到 Python/Pip 的痛。
     - 超时异常优雅映射为 Linux 标准退出码 `exit_code=124`（`:201-204`）。
     - 上传时对非 UTF-8 字节的 `latin-1` 降级容错（`:270`）。
2. **`sandbox_proxy.py`（代理热替换层）**
   - **职责**：稳定引用句柄，屏蔽底层真实沙箱的生命周期。
   - **核心亮点**：文件头注释（`:11-18`）记录了极佳的框架迁移踩坑经验。
     - **机制陷阱**：协议基类将 `ls/glob` 等实现为 `raise NotImplementedError` 的具体方法（而非 `abstractmethod`）。
     - **后果**：MRO 属性查找会优先命中基类，导致 `__getattr__` 永远不会被触发。如果漏写方法，会抛出 `str(e)==""` 的空异常，前端表现为“静默无输出”。
3. **`sandbox_manager.py`（单用户生命周期层）**
   - **职责**：管理单个用户的沙箱生命周期与预热。
   - **核心亮点**：
     - **隔离粒度**：基于 `user_id` 而非 `thread_id` 进行隔离（`ensure_sandbox_for_user` `:117`）。
     - **四状态机设计**：认领预热沙箱 → 内存缓存 Ping 验证 → MongoDB 重连 → 全新创建。
     - **持久化**：依赖 MongoDB 的 `sandbox_registry` 集合，保证服务重启后状态无损重连。
4. **`global_sandbox_manager.py`（跨进程协调层）**
   - **职责**：解决 FastAPI 主进程与 `:2024` (Agent Protocol) 进程无法共享 Python 对象的问题。
   - **核心亮点**：降级使用本地磁盘文件 `.langgraph_api/global_sandbox.json`（仅存 ID 和时间戳）进行跨进程的状态同步和重连。
5. **`sandbox_setup.py`（初始化播种层）**
   - **职责**：沙箱初次启动的基建。
   - **核心亮点**：
     - **环境隔离**：创建独立的 venv 并预装 `numpy/pandas` 等依赖，避开系统 Python 限制。
     - **幂等启动**：利用 `/tmp/.venv_installed_{pkg}` **Sentinel（哨兵）文件防止重复安装**（`:233-247`），大幅加速重复启动。

------

**基于 Proxy 代理与多级缓存的沙箱生命周期时序图**

![image.png](https://fynotefile.oss-cn-zhangjiakou.aliyuncs.com/fynote/fyfile/32693/1777391593080/63f21ccccd4d46d782b50028a15d3c82.png)

---

### 第 6 步：数据面剖析：MCP 防腐层与动态路由

> **建议阅读顺序：先分清「两个 MCP Server」，再看消费端怎么合并 / 分组 / 瘦身，最后读那份返回值契约。** 这里不是一套 MCP，而是「自建 ERP MCP + 外部魔搭 MCP」两路，Agent 侧用一个客户端同时接。

**两个 MCP Server（连接配置 `mcp_client.py:23-36`）**

| Server | key / 地址 | 来源 | 提供什么 | 必需性 |
|---|---|---|---|---|
| 本地 ERP MCP | `erp-api` @ `ERP_MCP_URL`（默认 `127.0.0.1:8000/mcp`） | 自建 `src/mcp_server/` | 8 个业务工具（`supplier_`/`part_`/`inventory_`/`order_`），防腐层转发 Java REST | 必需 |
| 魔搭分析 MCP | `analysis` @ `ANALYSIS_MCP_URL` | 外部 · 魔搭社区 | 27 个 `generate_*` 图表 / 地图 + `generate_spreadsheet` 等 | 可选（不配则只有 ERP 工具） |

1. **本地 ERP MCP（`src/mcp_server/`）—— 防腐层**
   - **职责**：FastMCP + streamable-http（`server_main.py:20-48`），把 Java 后端 REST 包成一组工具，Agent 不直连 Java。
   - **注册模式**：每个业务域一个 `register_xxx_tools(mcp)` + **闭包**（`tools/__init__.py:14-48`），三个动机——
     - **① 依赖注入**：`mcp` 从外部传入，便于传入 Mock 对象进行单元测试；
     - **② 签名干净**：`http_client` 等底层依赖隐藏于闭包中，不暴露给 LLM 造成干扰；
     - **③ 定义即注册**：利用 `@mcp.tool` 挂载，内层函数名（如 `create_order`）与对外暴露名（如 `order_create`）通过 `name=f"{GROUP}_xxx"` 解耦。
     - *扩展性*：新增业务域 = 加一个文件 + `server_main` 一行。
   - **工具清单**：4 模块 8 工具——`inventory_warning`；`order_create` / `order_update` / `order_search_details`；`part_query` / `part_search` / `part_by_supplier`；`supplier_query`。
2. **魔搭分析 MCP —— 外部可视化能力**
   - **核心职责**：仅当配置了 `ANALYSIS_MCP_URL` 时才连接（`mcp_client.py:32-36`、`:83-88`），提供 **27 种** `generate_*` 工具（包含 26 个图表/地图可视化工具 + `generate_spreadsheet` 非可视化工具等）。
   - **核心亮点（消费端瘦身与渐进式披露）**：
     - **合并入口与动态路由**：27 个工具直接塞入 LLM 上下文会导致 Prompt 臃肿且消耗大量 Token。因此 `chart_generator.py` 提供了工厂函数 `create_generate_chart_tool`，将 26 个可视化工具合并为 **1 个 `generate_visualization`** 工具。通过 `chart_type` 参数路由至底层 MCP，且路由规则极其精细：`_chart` 结尾会截断后缀（`bar_chart` → `bar`），而 `_map`/`_diagram` 结尾则保留全名（如 `pin_map`）。
     - **渐进式披露（Progressive Disclosure）**：不是把庞大的完整参数 schema 塞进 Prompt，而是通过 `_build_tool_description` 生成一份仅约 800 tokens 的**紧凑速查表**（`_COMPACT_TABLE`）；完整的参数说明来自 **手工维护的**  `src/skills/procurement/chart_params.md`（随技能同步上传到沙箱同名路径）。Agent 拿不准时，先 `read_file` 读取说明再调用，极大节省 Token 并提高生成准确率。
     - **非可视化工具剥离**：不符合图表后缀规则的独立工具（如 `generate_spreadsheet`），不会被合并，而是放入 `other_tools` 列表单独暴露给 Agent。
3. **消费端分组（`mcp_client.py`）**
   - **职责**：`MultiServerMCPClient` 同时连两个 server，`load_mcp_tools()` 合并后 **按前缀** 切成 analyst / order / chart 三组，交给不同子 Agent（`:44-51`、`:94-109`）。
   - **细节**：前缀规则 `supplier_`/`part_`/`inventory_` → 分析，`order_` → 订单，`generate_` → 图表；用 **前缀匹配而非子串**，防止吞掉拼写错误（呼应第 7 步 loader 的同一取舍）。
4. **返回值契约（`result_utils.py` + `tools/__init__.py:86-89`）**
   - **核心原则：绝不抛异常，只返回文本**。在传统开发中，遇到报错通常使用 `raise Exception`。但由于工具的结果是直接喂给大模型的，抛异常会导致模型看到一堆它看不懂的代码堆栈（Traceback），白白浪费 Token 并引发模型幻觉。因此，出错时一律**正常 `return` 一段人话**。
     - **写操作**（如 `order_tools.py` 的下单失败）：返回结构化字典 `{"error": "具体的错误原因"}`。
     - **查操作**（如 `parts_tools.py` 查不到数据）：返回包含错误信息的列表 `["没有查询到任何信息, 报错: xxx"]`。
     - *好处*：大模型看到这段话，能像人一样理解“哦，我参数传错了/没查到”，从而自动调整参数，进行**自我纠正并重试**。
   - **剥离外壳（省 Token）**：成功时，直接返回 Java 接口里的 `data` 里的数据，剥掉没用的 `code` 和 `msg`。
   - **教科书级避坑（防空数组）**：底层的 MCP 协议和大模型 API 有一个硬性规定——**不接受空的内容数组**。如果你的工具返回了空列表 `[]`，底层协议会给模型发送空 `content`，大模型 API 会直接报错罢工。因此 `result_utils.py → pyensure_nonempty_content` 做了一个兜底：如果查到的是空容器，就把它包装成字符串 `"[]"` 或 `"{}"`。大模型收到后能正常理解“哦，这是个空集合”。

**MCP 侧「查询失败」和「查无数据」共用同一套文案，会导致 Agent 得出什么错误结论？**

> 如果在 MCP 侧将「查询失败（网络/系统异常）」与「查无数据（正常空结果）」共用同一套文案，会导致大模型产生致命的业务误判：Agent 会**把系统故障误认为业务事实**（如断定数据不存在导致流失）、**把网络超时当作参数错误**而陷入疯狂重试的死循环（严重消耗 Token）、因无法自圆其说而**诱发胡编乱造的幻觉**，甚至**中断原本正确的业务流**（如把正常的空库存误判为系统灾难而停止后续下单）。因此，在 Agent 开发中，工具返回值不仅是数据，更是**引导大模型行为的隐形指令（Prompt）**，必须用明确的标签（如【查询成功】、【网络异常】）加以严格区分。

---

### 第 7 步：分布式编排：跨进程通信与底层暗坑

> **建议阅读顺序：** 先看 YAML 如何精准分发工具，再看异步子 Agent 如何隔离部署，接着看前端如何跨服务轮询，最后死磕 `langgraph` 4.x 底层存储的“天坑”。必须有前 6 步的直觉，才能读懂这里的架构取舍。

#### 1. 动态分发与防呆设计：`subagents/loader.py`

- **核心职责**：读取 `configs/*.yaml` 配置，将字符串工具名解析为实际可调用的工具对象（`load_subagent_configs` / `resolve_subagent_tools`）。
- **架构取舍（逐级降级匹配）**：匹配工具时，必须严格遵循 **精确匹配优先，前缀匹配兜底**（如 `supplier_` 匹配一组工具）。
- **防御性设计（禁用子串匹配）**：代码 **刻意不支持任意子串匹配**，拒绝静默容错。如果允许子串匹配，YAML 里把 `order_create` 误拼成 `create`，代码会静默容错把工具塞进去，导致大模型拿到错误的权限。宁可匹配失败记 WARNING 报错，也绝不吃掉配置拼写错误（`test_subagent_loader.py:50-54` 专门测了这条）。

#### 2. 跨进程隔离构建：`async_procurement_analyst.py`

- **核心职责**：在模块顶层直接调用 `create_deep_agent` 构建 `procurement_analyst_async` 图。它不跑在主 FastAPI 进程里，而是跑在 `:2024` 端口的独立 Agent Protocol 服务上。
- **架构意图（防状态污染）**：此处的 `checkpointer=None` 是刻意为之。子 Agent 作为独立服务，其状态持久化交由 `:2024` 协议服务自身闭环管理，从而彻底杜绝主/子进程并发抢写同一个 MongoDB Checkpoint 导致的状态混乱。

#### 3. 跨服务轮询 API：`api/async_tasks.py`

- **核心职责**：前端轮询获取子 Agent 任务状态。
- **架构意图**：不在本地直接读 checkpoint（因为子 Agent 状态不在主进程内存），而是用 `langgraph_sdk` 作为 HTTP 客户端，跨进程代理请求 `:2024` 服务的 `client.runs.list` 和 `threads.get_state`。配合前端的退避轮询策略（4s → 8s → 15s），实现完美的前后端解耦。

#### 4. 底层机制深坑：LangGraph 4.x 的增量存储（Delta Channel）

> `agent_loader.py:21-49` 记录了全项目最具技术含量的底层踩坑与填坑过程。

- **异常现象**：开发时发现刷新页面会话就丢失，但去 MongoDB 查数据却一条没少。
- **排查根源**：从 `langgraph-checkpoint` 4.x 开始，底层改用了 **Delta Channel（增量存储）**。每个 checkpoint 文档只保存「本步更新过的通道」。直接读 `checkpoint["channel_values"]` 拿到的是稀疏碎片，必须回放底层写入日志才能拼出完整状态。
- **巧妙解法**：不能直接读 MongoDB。作者极其巧妙地手写了一个空的 `_state_reader` 状态图（`StateGraph(AsyncSubAgentState)`），拿着同一个 `CHECKPOINTER` 编译。通过调用 `_state_reader.aget_state_history()` 触发 Pregel 引擎，让它按照时间线自动回放并合并所有 delta 增量，最终拿到了完整的 61 条消息历史。

------

**架构总结**：这一套分布式架构的精髓在于——**隔离运行时（独立进程），防呆分发（前缀匹配），巧读增量（空图回放）**。把子 Agent 当作微服务来对待，同时又用架构技巧抹平了底层存储引擎升级带来的巨坑。

---

## 二、核心设计思想（8 条）

### 1. 虚拟文件系统：用路径命名空间替代 API 设计

全项目最优雅的架构设计。`CompositeBackend`（`main_agent.py:245`）巧妙地通过 **路径前缀路由**，将三种语义完全不同的存储统一到了一起。Agent 侧无需学习任何新 API，只需掌握最基础的 `read_file / write_file / ls / execute` 四个动作，就能同时获得三种能力：

- **默认路径** (`default`) ➡️ 临时执行环境（沙箱）
- **`/memories/`** ➡️ 跨会话持久记忆 ➡️ `StoreBackend(namespace=(user_id,))`
- **`/persisted-skills/`** ➡️ 技能仓库 ➡️ `StoreBackend(("skills",))`

**微观设计精妙（防并发覆盖陷阱）：**
隔离粒度完全取决于业务需求，两条 route 刻意采用了不同的设计：

- **多用户隔离（`/memories/`）**：namespace 必须写成 **lambda 闭包**（`:255`，理由见 `:251-254`）。因为 Graph 是每个请求动态新建的，在多用户并发组装时，如果直接传变量，极易被其他用户的请求覆盖，导致“张三看到了李四的记忆”。用闭包可以确保在实例化时捕获当前上下文。
- **全局共享（`/persisted-skills/`）**：故意使用全局 namespace（`:259`），因为技能库对所有用户都是通用的。

> **通用价值（架构哲学）**：
> 新增一种持久化能力，不需要给 Agent 写新的工具（Tool），也不需要改 Prompt，只需要在 Backend 里加一条 route 即可。这是经典的 **“一切皆文件（Everything is a file）”** 思想在 AI Agent 领域的完美复现，极大降低了 Agent 的认知负担和工具编排成本。

### 2. 洋葱模型：中间件的执行顺序即业务语义

在 `main_agent.py` 的 `main_middleware` 列表中，中间件的注册顺序经过了严密的逻辑推演。要理解为什么这么排，必须先看框架底层的执行机制（已核对 LangChain `agents/factory.py`）：

**底层机制：洋葱模型（Onion Model）**

- `before_agent` / `before_model`：**正序执行**（从外向内剥洋葱）。
- `after_agent` / `after_model`：**逆序执行**（从内向外穿衣服）。
- `wrap_tool_call`：**最先注册的包裹在最外层**（可拦截所有内层的工具调用，类似 AOP 切面）。

基于这个底层机制，列表里隐藏着 **3 条绝对的硬依赖**（对应你提供的中间件 1~8）：

#### ① 先体检，后干活（生死依赖）

- **依赖关系**：`SandboxHealthMiddleware` (1) 必须排在 `SkillsSyncMiddleware` (4) 和 `UserSkillsRestoreMiddleware` (5) 之前。
- **因果推演**：`before_agent` 正序执行。如果沙箱已经宕机（如宿主机断电），健康守护中间件必须第一时间探活并触发重建/热替换。只有确保沙箱“活着”，后续的技能同步（MD5比对）和私有技能恢复才能把文件写入进去。否则，向已死的容器上传文件会引发灾难性的连环报错。

#### ② 先有错误标记，才能数错误熔断（统计依赖）

- **依赖关系**：`ToolErrorMiddleware` (2) 必须排在 `SandboxCircuitBreakerMiddleware` (8) 之前。
- **因果推演**：熔断器 (8) 在 `before_model` 阶段会回溯历史消息， 只数 `status=="error"` 且带沙箱关键字的 `ToolMessage`。如果 `ToolErrorMiddleware` (2) 不先在外面把底层抛出的异常（Exception）拦截并优雅地包装成 `ToolMessage`，熔断器根本捕捉不到这些错误，也就永远无法触发熔断，Agent 会陷入无限重试的死循环。

#### ③ 先压缩空间，再提炼记忆（成本依赖）

- **依赖关系**：`SummarizationMiddleware` (6) 必须在 `MemoryUpdateMiddleware` (7) 之前。
- **因果推演**：这俩都在 `after_agent` 逆序执行（所以 6 比 7 晚执行）。如果先更新记忆 (7)，大模型需要携带冗长的完整历史去请求 API，成本极高；如果先摘要 (6)，把历史压缩成精简版，然后 (7) 再基于精简后的轻量级上下文去提取实体、更新偏好，既能完美提取信息，又大幅省下了 Token 账单。

#### ④ 限制类兜底放最后（外层防线）

- **执行机制**：`ModelCallLimit` (9) 和 `ToolCallLimit` (10) 等拦截类中间件被放在了列表末尾。
- **因果推演**：因为在 `before_model` 和 `wrap_tool_call` 中，越往后越靠近核心模型/工具。把限制器放在最靠近模型的地方，相当于在它张嘴或伸手的最后一刻进行拦截。这样，前面 1~8 号中间件为了保证系统高可用而做的各种探测、纠错、重建尝试，都可以顺畅进行，但绝不会因为某一个 Bug 导致模型无限循环把账单烧穿。

### 3. 容错四重奏：面向 LLM 的 Error-as-Data 防线

在传统后端开发中，遇到错误我们习惯“抛出异常（Raise Exception）”，让上层去捕获。但在 Agent 架构中，**异常堆栈只会让大模型陷入“幻觉”和“死循环”**。因此，系统构建了从内到外、层层递减的“漏斗式”四重防线，核心思想只有一个：**将错误降级为大模型能读懂的“数据”。**

- **第一重：源头治理（MCP 侧）**
  - 在 MCP Server 接口层，无论是 `code != 200` 的业务失败，还是网络超时的异常，代码一律通过 `return` 返回结构化文本（如 `【网络超时】`、`【查询成功】无数据`）。这从源头上掐断了异常抛出的可能，让大模型拿到的是清晰的“行动指令”，而不是一堆看不懂的 Traceback。

- **第二重：切面翻译（ToolErrorMiddleware）**
  - 如果底层工具还是不可避免地抛出了 Exception（比如第三方库内部报错），`ToolErrorMiddleware` 会通过 `wrap_tool_call` 切面强制拦截。它把这些可怕的异常“翻译”并包装成模型能读懂的 `ToolMessage(status="error")`。这样，大模型在上下文中看到的不是系统崩溃，而是一个正常的工具报错，从而触发它的 **自我纠正（Self-Correction）**逻辑。

- **第三重：事前体检与热修复（SandboxHealthMiddleware）**
  - 把视角拉高到每轮对话的开始。`SandboxHealthMiddleware` 会在前置钩子（`before_agent`）里主动执行 `ping` 探活。一旦发现沙箱挂了，它不会报错，而是 **静默触发重建**。最精妙的是，它借助 `proxy.replace_backend()` 实现了底层实例的热替换。由于外层业务逻辑和 Agent 握着的只是 Proxy 句柄，所以 **底层沙箱换了，上层业务完全无感**。这种设计实现了真正的“自愈”能力。

- **第四重：物理断电（SandboxCircuitBreakerMiddleware）**
  - 如果前 3 重防线都失效了（比如宿主机彻底断电），Agent 开始陷入疯狂重试。此时熔断器作为终极防线登场。它会利用 `sandbox_breaker.py` 中的 `_looks_like_sandbox_error` 方法，精准识别那些真正代表沙箱崩溃的错误（排除掉普通的参数错误）。一旦连续错误达到阈值，直接 `jump_to="end"` 中断状态机，并向用户输出优雅的降级提示，**坚决不让 API 账单和死循环把整个系统拖垮**。

> **架构收益总结**：
> 这套机制完美诠释了 **“系统级容错”与“Agent级认知”的分离**。底层通过 Proxy、探活、熔断等传统高可用手段保证系统不死；而暴露给大模型的，永远是被清洗过的、人类友好的、可消费的“数据”。两者结合，才成就了真正能在生产环境运行的智能 Agent。

### 4. 状态即上下文：基于消息回溯的轻量级熔断机制

在传统微服务中，熔断通常依赖 Redis 或内存对象来记录时间窗口内的错误率。但 `sandbox_breaker.py` 采用了一种极具 Agent 原生特色的方案——**摒弃一切外部状态，直接把熔断逻辑寄生在 `messages` 列表里。**

**源码级的精妙机制：**

1. **尾部连续回溯（连续才算错）**：在每次 `before_model` 阶段，代码从消息尾部 `reversed(messages)` 向前遍历。遇到非沙箱错误的 `ToolMessage` 直接 `break`，遇到新的 `human`/`system` 消息也直接 `break`。它只抓取**“连续”**发生的沙箱错误（连续 2 次即触发阈值），绝不把跨轮次的偶发错误叠加在一起。
2. **精准特征匹配**：不是所有报错都算数。必须同时满足 `status == "error"` 且内容命中了 `_SANDBOX_ERROR_KEYWORDS`（如 `sandbox`、`连接`、`超时`、`不可达` 等硬核关键字），才会被判定为沙箱故障，防止误伤普通的业务参数报错。
3. **标记清零机制**：当熔断触发，代码返回 `{"jump_to": "end"}` 强制中断 Agent，并向历史里注入一条带有特殊标记（`CIRCUIT_BREAKER_MARKER = "SandboxCircuitBreaker" #用于标记熔断消息的特殊标识符，用于重置连续错误计数器`）的 `AIMessage`。下一次再回溯到这里时，只要检测到这个标记，计数器立刻归零，防止历史熔断记录引发“次生灾难”。

**为什么放弃 Redis 等外部组件？**

- 因为 LangGraph 的 Graph 是按需实例化、跑完即销毁的。如果引入外部组件，既要处理分布式并发锁，又要解决服务重启后的状态同步问题。而 `messages` 数组天生就是有序的、持久化的、且严格按时间线排列的。**用消息历史记录状态，极其轻量且绝对准确。**

> **通用价值：状态即上下文（State is Context）**
> 这是这类 Agent 脚手架与传统服务治理最不一样的地方。传统架构中，状态在数据库或缓存里；而在 Agent 架构中，**上下文本身就是状态，历史消息就是一部不容篡改的“事件溯源（Event Sourcing）日志”**。不需要外挂任何状态机，对话看到哪里，业务逻辑就执行到哪里。这是读懂 Agent 系统设计的一把核心钥匙。

### 5. 「无状态计算：Graph Factory 与代理热替换

#### 核心摘要

在构建高并发 AI Agent 系统时，`agent_loader.py` 采用了一种反直觉的设计：**不缓存 Graph 实例，每次请求都现场极速重新组装（Graph Factory 模式）**。
这种「无状态计算（Stateless Compute）」之所以能成立，靠的是**「三大状态外置」**与**「Proxy 稳定句柄」**的完美配合。

------

#### 1. 架构权衡（Why：为什么要频繁重建？）

在 `agent_loader.py:179-207` 的 `get_agent_for_user` 中，代码放弃了缓存，选择每次请求重建 Graph。

- **收益 1（绝对隔离）**：彻底杜绝了多用户并发时的状态污染与数据越权。
- **收益 2（极速热更）**：修改了 Graph 的编排逻辑（如增减中间件），不需要重启服务，下一个请求自动生效。
- **代价（性能开销）**：频繁重建有极小的 CPU 组装开销。但结合启动时预计算的 `PrecomputedContext`（包含加载好的 MCP 工具、YAML 配置等），每次请求的组装成本已被压到最低，完全可忽略。

------

#### 2. 凭什么敢频繁重建？（三大状态外置基石）

“无状态计算”能成立的前提是：**Graph 内部绝不能保存任何关键状态**。`main_agent.py:221-229` 的「★ 核心架构设计」精准地把三大状态挪到了外部：

1. **对话历史**：存在 LangGraph 的 `CHECKPOINTER` 中（基于 `thread_id` 自动恢复，与 Graph 实例无关）。
2. **用户记忆**：存在 `StoreBackend` 的 `/memories/` 路由下（基于 `user_id` 闭包隔离）。
3. **工作文件**：存在物理 `Sandbox` 沙箱的磁盘里（通过 `default=sandbox_backend` 路由）。

------

#### 3. 核心破局点：昂贵资源如何活过 Graph 的生死？（Proxy 稳定句柄）

既然每次请求 Graph 都是新的，那底层极其昂贵的物理沙箱（需要十几秒启动）怎么办？这就是 **`SandboxBackendProxy`（稳定句柄）** 的终极使命。

- **永久身份证**：它相当于给底层沙箱办了一张永久身份证。无论沙箱底层怎么崩溃、热重启，Graph 拿到的 Proxy 引用永远不变。
- **为何手写 21 个方法？** 单独看 `sandbox_proxy.py` 里手写 21 个方法逐个显式转发，会觉得极其啰嗦；但这是为了绕过 Python `__getattr__` 在基类 `NotImplementedError` 下的截胡陷阱。**连着“每请求新建 Graph”这个背景来看，它是保障系统高可用不可或缺的稳定锚点。**

------

#### 4. 底层踩坑与细节（极致的工程实现）

- **`_state_reader` 的巧妙救场**：
  - **隐患**：LangGraph 4.x 改用 **Delta Channel（增量存储）** 后，每个 checkpoint 文档只保存本步更新过的通道。直接读 `channel_values` 会拿到残缺数据（表现为“刷新页面会话消失”）。
  - **解法**：系统巧妙地构建了一个**空的只读状态图 `_state_reader`**，共享同一个 `CHECKPOINTER`。通过调用它的状态 API，完美触发底层的自动回放（Replay），查出包含完整消息历史的无损快照。既不污染真实 Graph，又化解了底层存储引擎升级带来的灾难。

------

> **架构升华：Agent 走向云原生**
> 这套设计本质上是把传统微服务中 **「无状态应用（Stateless App）」** 和 **「不可变基础设施（Immutable Infrastructure）」** 的思想完美移植到了 AI Agent 领域。在这个架构下，Graph 变成了无状态的计算节点，随时可生可灭；而状态、记忆、环境被彻底解耦到外部的 Checkpointer、Store 和 Sandbox 中。这正是 AI Agent 走向真正“云原生”的最佳实践。

### 6. 上下文工程：Prompt 的分层加载与渐进式披露

在大模型应用中，最宝贵的资源是 **Context Window（上下文窗口）**。如果把所有信息都塞进 `system_prompt`，不仅会烧穿 Token 账单，还会导致大模型“注意力涣散”。因此，系统将 Prompt 按照 **「变更频率 + 谁能改 + 加载成本」** 严格划分为四层：

| 层级         | 载体位置                   | 维护者                 | 加载机制与生命周期                                           | Token 成本       |
| :----------- | :------------------------- | :--------------------- | :----------------------------------------------------------- | :--------------- |
| **宪法**     | `memory/AGENTS.md`         | 开发者（Agent 只读）   | **全量常驻**：通过 `memory=[...]` 参数直接固化进上下文。     | 高（每次必带）   |
| **人设**     | `memory/prompts.py:18`     | 开发者                 | **全量常驻**：作为主 Agent 的 `system_prompt` 基础底座。     | 高（每次必带）   |
| **编排规则** | `subagents/configs/*.yaml` | 半配置化（如业务线）   | **按需组装**：根据 YAML 定义，将特定工具和提示词绑定到子 Agent。 | 中（按需分配）   |
| **知识包**   | `src/skills/**/SKILL.md`   | 动态增减（如业务专家） | **渐进式披露**：Agent 通过 `ls /skills/` 扫描目录，需要时用 `read_file` 按需读取。 | 极低（按需拉取） |

### 7. 敬畏抽象层：Delta Channel 陷阱与影子图回放

在持久化状态时，如果绕开框架 API 直读底层数据库，往往会在框架升级时遭遇“静默失效”的灾难。`agent_loader.py:21-49` 记录了这个全项目分量最重的踩坑与填坑过程。

#### 异常现象：会话看似“丢失”，实则完好无损

- **表现**：用户刷新页面后，历史会话全部消失，仿佛没有保存。
- **定位**：排查发现 `/api/history` 接口依赖 `get_current_messages()` 判断会话是否有消息，结果永远返回 `[]`，导致所有会话被 `if messages:` 无情过滤。

#### 根因剖析：底层存储升级，但直读方式未变

- **底层升级**：`langgraph-checkpoint` 从 4.x 开始，底层改为 **Delta Channel（增量存储）**。每个 checkpoint 文档只保存“本步更新过的通道”。
- **驱动缺失**：官方三方库 `langgraph-checkpoint-mongodb` 0.4.0 **完全没有实现 delta 回放逻辑**。
- **导致恶果**：如果直接读 `checkpoint["channel_values"]`，拿到的只是稀疏的碎片（实测 61 条消息的会话，最新 checkpoint 里 `messages` 根本不在，只有 `async_tasks`）。
- **真相**：数据其实一条没丢，全在底层的 `checkpoint_writes` 表里。

#### 解决方案：构建“影子图”触发底层回放

修法不是去魔改底层依赖（因为 PyPI 上最新版同样不支持），而是利用 Pregel 引擎自身的机制：复用一个只读极简的 `StateGraph(AsyncSubAgentState)`。

1. **借壳生蛋**：用 `AsyncSubAgentState` 作为 Schema，挂一个空节点（`lambda state: {}`），与同一个 `CHECKPOINTER` 编译。
2. **触发回放**：当调用 `_state_reader.aget_state()` 或 `_state_reader.aget_state_history()` 时，LangGraph 的底层引擎会自动按照时序，把所有 delta 增量合并，完美回放并拼装出完整的 61 条消息历史。

> **通用架构启示：**
> 任何绕过**状态 API** 直读底层存储的代码，都是在把框架的 **内部表示（Internal Representation）** 硬编码进自己的业务代码里。框架底层一旦重构（如引入增量存储），业务层就会在毫无报错的情况下静默失效。**永远敬畏抽象层，让它去处理回放与合并。**

### 8. 分布式的妥协：跨进程下框架能力的降级与补救

在单体脚本里跑得很溜的框架功能，一旦上了“多进程/分布式”部署，往往会因为底层通信机制失效而默默瘫痪。这是一个经典的**“静默断点”**。

#### 坑点一：跨部署导致「子 Agent 自动唤醒父 Agent」彻底失效

- **理想情况**：框架原本设计了 `_notify_parent` 机制，子 Agent 完成后主动 Push 通知父 Agent。
- **现实崩塌**：当子 Agent 跨 deployment 部署（独立 `:2024` 端口）时，底层 `_reference_for()` 会把父级 `assistant_id` 丢掉。由于父 Agent 本地也没有这个 ID，导致两者的通信桥梁断裂，`_notify_parent` **永远无法触发**。
- **最可怕的是**：该机制内部做了防御，**never raises（绝不抛异常）**，只写 debug/warning 日志。这导致系统表面风平浪静，实则功能已死，排查极其困难。

#### 工程补救：放弃 Push，改为 Pull（拉取模式）

既然底层推送断了，只能靠业务层自己“拉”：

- **父 Agent 轮询**：利用 `abefore_model` / `aafter_model` 钩子，仅在父 Agent 正在运行或用户新发消息时，才去拉取结果。空闲期不拉。
- **前端强行兜底**：前端必须自己写定时器去轮询 `GET /api/async-tasks/{task_id}`（见 `App.vue:593`）。拿到结果后，由前端**伪造（合成）一条 assistant 消息**注入到 UI 里。
- **真相**：其实父 Agent 完全不知道子任务完成了，全靠前端在演双簧。

#### 同源问题：跨进程无法共享 Python 对象

同理，由于进程隔离，全局沙箱实例无法在内存中共享。唯一的解法是把沙箱 ID **落盘写入本地 JSON 文件**，另一个进程再去读文件重连。

> **架构感悟**：
> 永远不要把“框架 Promise 的自动能力”当成绝对真理。跨进程/跨服务部署时，一定要去扒底层源码看它的通信依赖（如 `assistant_id`）。当框架的 Push 机制失效时，**Pull（轮询）往往是最稳定、最可靠的工程解。**

---

## 三、扩展思维（8 个方向，从「能改对」走向「能设计」）

### A. 平台化演进：底座与业务分离

把 ERP MCP 工具和采购 prompt 全部抽离，剩下的东西（沙箱管理层 + 中间件栈 + CompositeBackend 路由 + SSE 编排 + 熔断容错）本质上就是一个**可复用的「通用企业 Agent 平台」**。

- **重构切分线**：想清楚哪些文件属于“平台基建”，哪些属于“业务逻辑”，这是下一步做微服务拆分的核心前提。

### B. 记忆体系升级：从玩具到生产

> **现状**：记忆是 `MongoDBStore`（MongoDB `agent_store` 集合，与 checkpoint 同库）里存的一段 Markdown 文本，由大模型按需抽取字段；新用户首轮会先落 `preferred_*` 默认骨架再合并 `recent_*`。距离真正的生产级记忆还剩两大鸿沟。

1. **并发控制**：现状是“读-改-写”全程持按 `user_id` 的进程内回写锁（`memory_update.py:341`），单进程内同用户回写已串行化、不会互相覆盖；但仍没有幂等键/版本号——**多进程部署**时需要升级为 MongoDB 乐观锁或分布式锁。
2. **用户知情权缺失**：记忆是“暗箱操作”，用户无法查看系统到底记住了自己什么，也没有提供“一键清除/撤销”的入口。

### C. 异步唤醒演进：从 Pull 到 Push

目前因跨部署导致框架的自动唤醒失效，只能靠前端轮询（Pull）来兜底。要想实现真正的异步唤醒，父侧必须拥有**可寻址的 Run 实例** + **一条外部消息通道**（如 Redis Streams / Webhook / 消息队列）。这是本项目留给未来最大的想象空间。

### D. Agent 平台的权限模型（⚠️高危盲区）

当 Agent 成为操作系统，“安全”就成了生死线。当前代码潜伏着巨大隐患：

- **裸奔的身份**：`user_id` 无鉴权，直接用 localStorage 里的 UUID 即身份。
- **危险的技能**：Skill 是“提示词 + 可执行脚本”，用户能诱导 Agent 下载并运行任意技能；`scrape_page.py` 能访问内网任意 URL，而 `sandbox_setup.py` 里的网络出口白名单竟是**注释掉的**。
- **致命的越权（代码投递）**：`/persisted-skills/` 用的是全局 namespace（`main_agent.py:259`），**A 用户的技能会被恢复进 B 用户的沙箱**。这等于提供了一个跨用户的代码注入通道！
- **灵魂拷问**：谁能装 Skill？网络白名单怎么配？工具级 ACL 怎么控？审计日志怎么留？这四件事必须回答。

### E. 业务防腐层：MCP 的边界与设计原则

**必须想明白：**

> **为什么 Agent 不直连 Java ERP？**
>
> 因为 MCP 是完美的**收敛点**：收敛权限、方便 Mock 单测、当 ERP 接口变更时只需改 MCP tools 而绝不动 Prompt。

**设计原则**：

> **哪些只读接口可放开**？
>
> 哪些写操作（如 `procurement-order`）必须强行经过人工审批（HITL），目前已经是这个思路了。

### F. 框架依赖治理：锁版本与自建隔离层

本项目大量代码是在修补 `deepagents` / `langgraph` 底层的洞（如 Proxy 显式转发、只读回放图、异步唤醒降级）。这延伸出一个经典工程议题：是死死**锁住框架版本**，还是**自建一层 Interface 把框架 API 包起来**？`sandbox_proxy.py` 就是最好的自建隔离层活教材。

### G. Agent 行为的回归评测：从测代码到测行为

目前 8 个中间件只有极少数被覆盖。最致命的是：**改了 prompt 或中间件顺序，你怎么知道 Agent 没有变差？**

- **解法**：把 `AGENTS.md` 的委派规则当成契约，写一套回归集，专门测试“该不该委派 / 该委派哪个子 Agent”。根目录 `测试AI功能.md` 里的用例（火花塞对比、Excel 解析）就是最早的种子。

### H. 可观测性建设：拒绝系统盲飞

如果没有这一层，前面所有的容错和高可用设计都是盲的。必须按维度落盘打点：

- **性能维度**：每个 Middleware 的耗时、子 Agent 任务端到端时延。
- **质量维度**：工具调用成功率、大模型幻觉率。
- **成本维度**：按 User 归集的 Token 成本。
- **健康维度**：熔断触发次数。

---

## 四、架构师视角的灵魂三问

读每个文件时拿这三问对着问，基本能命中这个项目所有设计的驱动力：

### 1. 边界划分：这件事为什么不在 Prompt 里说？

- **核心逻辑**：分清「说服模型（软约束）」和「强制约束（硬拦截）」的边界。
- **落地体现**：说教归 `AGENTS.md`（靠大模型的自觉），兜底归 Middleware（如熔断、限流、沙箱探活，靠底层代码的强制执行）。Prompt 永远解决不了系统和网络级别的崩溃，只有代码能解决。

### 2. 填补空白：这个能力框架为什么不提供？

- **核心逻辑**：框架的边界，就是你的架构设计发挥的空间。
- **落地体现**：**补洞的位置，就是这个项目真正的设计位置。** 无论是 `SandboxBackendProxy` 对 21 个方法的显式转发，还是 `_state_reader` 影子图对 Delta Channel 的回放，都是在填补 `deepagents` 和 `langgraph` 底层的坑。这些补丁，就是你系统的高壁垒。

### 3. 状态锚点：跨请求、跨进程时它靠什么活下来？

- **核心逻辑**：认清无状态计算的本质，寻找状态的外部锚点。
- **落地体现**：对话靠 **MongoDB（Checkpointer）** 恢复；全局沙箱靠 **落盘 JSON** 重连；昂贵环境靠 **Proxy 句柄** 活过请求生死；异步子任务靠 **前端轮询** 跨越进程孤岛。这四种答案精准对应了计算、存储与网络的边界。
  - 两个沙箱的持久化口径不同，别混成一条：全局沙箱（异步分析师用）走**落盘 JSON**（`global_sandbox_manager.py` 的 `STATE_FILE = global_sandbox.json`）；per-user 沙箱（`sandbox_manager.py`）走 **MongoDB** 元数据。


> **💡 架构师体悟**：
> 顺着这三问，你其实已经回答了一个终极问题：**哪些交给概率（LLM），哪些交给确定性（代码），哪些交给外部基建（DB/Proxy）。** 能把这笔账算清楚，你就真正具备驾驭企业级复杂 Agent 的能力了。

---

## 五、几处容易被读错的地方

###  1. 概念混淆：`scheam.py` 不是 LangGraph State schema

- **真相**：项目里没有任何自定义 state 通道、reducer 或 `DeltaChannel` 声明。
  - `messages` 的 reducer 来自框架与 deepagents（熔断器借的是框架 `AgentState`，只读图借的是 deepagents 的 `AsyncSubAgentState`）。
  - `scheam.py` 实际上是 `ProcurementContext`（作 `context_schema`，只传 user_id/username）+ 一堆 API 的 Pydantic I/O 模型（`ChatRequest/ChatResponse/Message/...`）。
- **高光点**：`main_agent.py:394-401` 那段 context_schema 注释写得很准——尤其「与 State 严格隔离、模型无法幻觉篡改身份」这一条，说明它是**安全边界**而不只是 IDE 补全体验。
- **防混淆**：Delta Channel 那套机制是 **deepagents 自己声明的**，项目的应对动作是 `_state_reader` 只读图——两者不要混在同一层理解。

###  2. 链路缺陷：主模型故障没有降级链路

- **现状**：`config.py` 只定义 `MAIN_MODEL` 与 `SUMMARY_MODEL` 两级——没有备用模型，也没有 `with_fallbacks` / 重试 / 降级逻辑。主模型所在平台故障时，该请求直接失败，摘要模型救不了对话主链路。

###  3. 配置收敛：沙箱地址全项目单一来源

- **现状**：主链路（`env_utils.py:46`）与跨进程协调层（`global_sandbox_manager.py:44`）都读 env `SANDBOX_DOMAIN`，不存在第二处硬编码；换环境只需改 `.env` 一处。`.env.example` 的默认值 `http://localhost:8081` 是唯一兜底口径。

###  4. 存储后端：默认 MongoDBStore，memory 仅测试用

- **现状**：`config.py` 的 `STORE` 默认是 `MongoDBStore`（`src/agent/store/mongo_store.py`），数据落 MongoDB `agent_store` 集合，与 `MongoDBSaver` 的 checkpoint 同库同客户端，重启不丢。
- **开关陷阱**：`STORE_BACKEND=memory` 时回退 `InMemoryStore`——该后端重启即丢，只用于单测/临时调试，别配进生产。
- **能力边界**：`MongoDBStore` 的 namespace 前缀匹配靠 `ns_path`（`\x00` 拼接）正则实现；`asearch(query=...)` 的语义检索不支持，`score` 恒为 `None`。

### 5. 并发边界：记忆骨架对齐与写入串行化

- **骨架现状**：新用户/空文件时，`memory_update.py` 先落 `_DEFAULT_PREFERENCE_LINES`（`preferred_output` / `preferred_chart_type` / `preferred_currency` / `preferred_language` 四个键，`:32` 定义、`:366` 生效），再 merge `recent_*`，与 `AGENTS.md` 的默认格式对齐。
- **并发现状**：读-改-写全程持按 `user_id` 的回写锁（`:341`），单进程内同用户串行，后写覆盖先写不会发生；跨用户不互斥。多进程部署仍需 MongoDB 乐观锁（见 4b 的三个工程边界表）。

---

## 附：中间件栈速查表（`main_agent.py:350-375` 注册顺序）

| # | 中间件 | 钩子 | 一句话职责 | 副作用 |
|---|---|---|---|---|
| 1 | `SandboxHealthMiddleware` | `abefore_agent` | ping `echo ok`，失败重建沙箱 | 重建 backend + `proxy.replace_backend` + 重传 AGENTS.md |
| 2 | `ToolErrorMiddleware` | `wrap_tool_call` | 捕获工具异常 | 返回 `ToolMessage(status="error")`，短路不抛 |
| 3 | `ContextInjectionMiddleware` | `before_agent` / `abefore_agent` | 注入 user_id + 记忆路径提示 | 每轮追加一条 SystemMessage（会累积） |
| 4 | `SkillsSyncMiddleware` | `before_agent` / `abefore_agent` | 本地 `src/skills/` → 沙箱，一次批量 `md5sum` 比对增量 | 上传差异文件 + 变更时注入 SystemMessage；按 `user_id` 加进程内锁串行同用户并发 |
| 5 | `UserSkillsRestoreMiddleware` | `abefore_agent` | Store `("skills",)` → 沙箱 | 每轮全量重传 |
| 6 | `build_summarization_middleware` | 框架 | 上下文压缩 | 纯框架转发，无自研代码 |
| 7 | `MemoryUpdateMiddleware` | `aafter_agent` | LLM 抽实体写回 preferences.md | 写 Store；吞掉所有异常 |
| 8 | `SandboxCircuitBreakerMiddleware` | `before_model` + `can_jump_to=["end"]` | 尾部回溯连续沙箱错误 | 超限返回 `{"jump_to":"end"}` 短路 |
| 9 | `ModelCallLimitMiddleware` | 框架 | 模型调用上限 50 | — |
| 10 | `ToolCallLimitMiddleware` | 框架 | 工具调用上限 200 | — |

------

**三个致命的补充事实（必须警惕）：**

1.  **钩子不对称：异步完备，但同步会“静默降级”**

   8 个自研中间件中，有 **3 个**只实现了异步钩子，它们的同步钩子直接 `return None` 空转：

   - `sandbox_health.py:61`（`before_agent`）
   - `user_skills_restore.py:73`（`before_agent`）
   - `memory_update.py:267`（`after_agent`）

   > **风险**：如果有人强制用同步方式（如 `graph.invoke`）调用，这些核心功能将直接失效。
   > *注：另外 4 个（`tool_error`、`context_injection`、`skills_sync`、`sandbox_breaker`）的同步钩子都有真实实现；`tools_summarization` 则完全依赖底层框架转发。*

2. **隐式约定：跨文件的三处“一改就崩”**

   项目中有 **三处隐式字符串约定**，任何一处改名，另一处都不会报错，只会**静默失效**：

   - **路径约定**：`/memories/{uid}/preferences.md`
     - **现状：已有单一来源（`config.py` 里的常量被 `memory_update.py` 和 `context_injection.py` 共同引用）。**隐患仅剩一处**：`AGENTS.md` 里的散文描述是手抄的，如果改了 config 里的路径，必须手动去改 `AGENTS.md` 的提示词
   - **字段名约定**：`recent_suppliers` / `recent_queries`
     - *痛点*：这两个字段名依然是散落在 `memory_update.py` 中的**裸字符串**。
     - *何静默失效*：它不仅写在 Python 代码里（用于生成 Markdown 和正则删改旧区块），还写在 `AGENTS.md` 里指导大模型（“请去读取 recent_suppliers”）。如果代码里把字段名改成了 `latest_suppliers`，但 `AGENTS.md` 没改，大模型去读记忆时会找不到键，导致“记忆丢失”；如果正则匹配没改，旧数据就删不掉，导致“记忆无限堆积”。
   - **映射约定**：scope→子 Agent 映射
     - *现状*：由 `config.py:105-109` 的 `SCOPE_MAP` 定义，目前唯一消费方在 `assign_skill.py:50-55`。如果以后新增了消费方或修改了 Scope 名，两边没对齐就会导致技能分配异常。

3. **并发与性能：Skills 同步的 O(1) 优化与用户级锁**

   `SkillsSyncMiddleware` 在处理同步时，做了两个极致的工程权衡：

   - **拒绝实例缓存**：因为 Graph 每次请求新建，实例字段（如 `_last_hashes`）必然失效。改用“一次 `find -exec md5sum` 批量比对”（`skills_sync.py:130-132`），把 N 次远程往返压成 **O(1)**。
   - **用户级串行锁**：底层的 `chat.py` 中 `_THREAD_LOCKS` 只按 `thread` 串行，跨会话依然会并发。由于同用户的多个会话共享一个 per-user 沙箱，所以在 middleware 层必须自己加一把按 `user_id` 的进程内锁（`_SYNC_LOCKS`，`:36`），防止并发覆盖，用户级锁。



