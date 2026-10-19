"""
MCP Server 业务工具集
======================================================================

一句话：用 register_xxx_tools(mcp) + 闭包来注册工具，是为了用最小的
语法成本同时拿到三个收益 —— 依赖注入（mcp 由外部传入，不依赖全局
单例、可单测）、签名干净（http_client 藏进闭包，不进 LLM 可见的参数）、
扩展无痛（新增业务域 = 加一个文件 + server_main 一行，不动现有代码）。

本包为纯 docstring 包，不含任何可执行代码，仅作为 4 个工具模块的
包级说明。加载时无副作用，所有实际注册由 server_main.py 完成。

----------------------------------------------------------------------
一、注册模式与设计动机：register_xxx_tools(mcp)
----------------------------------------------------------------------

每个工具模块只暴露一个 register_xxx_tools(mcp: FastMCP) 函数，
由 server_main.py 在启动时统一调用：

    register_inventory_tools(mcp)   # server_main.py:30
    register_order_tools(mcp)       # server_main.py:31
    register_parts_tools(mcp)       # server_main.py:32
    register_supplier_tools(mcp)    # server_main.py:33

函数内部用闭包定义工具，示例如下：

    def register_order_tools(mcp: FastMCP):
        @mcp.tool(name=f"{GROUP_NAME}_create")
        async def create_order(order_id: str, items: list):
            ctx = mcp.get_context()
            client = ctx.request_context.lifespan_context["http_client"]
            ...

为什么要这么写（三个设计动机）：

  1. 依赖注入 —— mcp 实例由外部传入，模块不创建也不 import 全局实例。
     既无循环依赖，也便于单测传入假 mcp 收集工具后直接断言。

  2. 签名干净 —— http_client 等运行时依赖藏在闭包里，不进工具的参数
     签名，LLM 只看到业务参数（order_id、items 等），不会乱传基础设施。

  3. 定义即注册 + 名称解耦 —— @mcp.tool 在函数定义时完成挂载，无需另
     维护一张注册表，不存在“定义了忘注册”的脱节；内层函数名
     （create_order）与对外工具名（order_create）分开，靠
     name=f"{GROUP_NAME}_xxx" 拼出统一前缀。

合计效果：新增一个业务域 = 新增一个文件 + server_main 加一行注册，
不改动任何现有代码。

----------------------------------------------------------------------
二、工具清单（4 个模块 · 8 个工具）
----------------------------------------------------------------------

inventory_tools   —— 库存

  inventory_warning        查询库存预警（低于安全库存的物料）

order_tools       —— 采购订单

  order_create             创建采购订单（订单号/时间不传则自动生成）
  order_update             按订单 ID 更新（传明细则整体替换）
  order_search_details     按零件名/日期范围搜订单明细（含零件+供应商）

parts_tools       —— 零部件

  part_query               分页查询（支持名称/分类/供应商筛选）
  part_search              按名称直接搜索
  part_by_supplier         查询供应商有采购记录的零部件

suppliers_tools   —— 供应商

  supplier_query           按名称模糊搜索

----------------------------------------------------------------------
三、共同约定
----------------------------------------------------------------------

  数据来源
      从 ctx.request_context.lifespan_context 取共享 http_client，
      转发后端 Java ERP API。http_client 在 Server 生命周期内只创建
      一次、所有工具复用，不按请求重建。

  返回值校验
      统一校验业务码 code == 200。

  错误处理
      业务码异常或调用异常时，直接返回错误信息文本、不抛出异常。
      因为返回值会进 LLM 上下文，抛异常只会变成 traceback 噪音，
      返回可读错误才能让 Agent 自我纠正后重试。

----------------------------------------------------------------------
四、边界说明
----------------------------------------------------------------------

本包只管“工具怎么定义、怎么挂载”，不参与上下文控制。LLM 上下文的
开销由消费端 src/agent/ 决定：

  - 同构工具的合并     → agent/tools/chart_generator.py
  - 按子 Agent 分配    → agent/subagents/loader.py
"""
