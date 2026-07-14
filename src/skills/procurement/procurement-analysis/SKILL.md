---
name: procurement-analysis
description: >
  采购分析主技能包。串联完整分析流程：需求拆解 → 数据收集 → 执行分析 → 生成图表 → 输出报告。
  当需要进行供应商对比、价格分析、物料筛选、采购策略评估时加载此技能。
---

# 采购分析技能（操作手册）

## 适用场景
- 供应商比价分析（多供应商、同物料价格对比）
- 物料行情评估（价格趋势、供需分析）
- 供应商综合评分（按信用评级、价格、交期加权打分）
- 采购策略建议（分阶段采购、集中采购、备选供应商推荐）
- 生成含图表的采购分析报告

## 分析流程（5 步）

### 第 1 步：理解需求
- 从任务描述中提取：【任务目标】、【用户偏好】、【分析需求正文】
- 明确分析维度：供应商对比？价格趋势？物料筛选？综合评估？

### 第 2 步：收集数据

**2a. ERP 内部数据（必须）**
- `supplier_query(name="供应商名")` → 供应商列表
- `part_query(name="物料名")` 或 `part_search(name="物料名")` → 零部件
- `part_by_supplier(supplier_id=N)` → 按供应商查零部件
- `order_search_details(partName="xxx")` → 历史订单价格
- `inventory_warning()` → 库存预警

**2b. 外部数据（不可跳过）**
1. 激活 `/skills/procurement/supplier-price-urls/`，读取 `data/url_mapping.yaml`
2. 按零部件名 + 供应商名查找对应报价 URL
3. **找到 URL** → 使用 `web-scraper` 直接抓取：
   ```bash
   execute("python /skills/procurement/web-scraper/scrape_page.py --url '<报价URL>'")
   ```
   脚本将 HTML 转为 Markdown 保存到 `/analysis/temp/{filename}.md`，再 `read_file` 读取内容提取价格/型号/品牌/材质/单位。
4. **未找到 URL** → 使用 `web_search` 搜索市场价格和规格参数
5. 无论哪种路径，最终都要有外部参考数据用于对比

### 第 3 步：执行分析
1. 编写 Python 分析脚本，使用 `execute` 工具运行
2. 如需安装依赖：`pip install -i https://mirrors.aliyun.com/pypi/simple/ pandas matplotlib numpy`（已自动路由到 `/opt/skills-venv/bin/pip`）
3. 常见分析维度：
   - 价格对比：最低价、均价、最高价、价差百分比
   - 供应商评估：信用评级 + 价格 + 交期加权
   - 物料质量：规格参数对比
   - 综合排名：多维度加权评分
4. 分析结果保留在上下文（`execute` 工具输出），无需写入文件

### 第 4 步：生成图表
1. **首次调用图表前**：`read_file("/skills/procurement/chart_params.md")` 获取 26 种图表参数速查（紧凑格式，仅 data 格式 + 特有参数，通用参数已提取到顶部表格，不逐图重复）
2. 根据分析维度选择 2-4 个最有洞察力的 chart_type
4. 循环调用 `generate_visualization(chart_type="xxx", chart_config={...})`，通用参数（width/height/title/theme/style）直接使用，无需每图查阅
5. 参考文件只读一次，后续多次调用不额外消耗上下文
6. 参数报错时对照参考文件修正后重试

### 第 5 步：生成报告
1. 汇总分析结论和图表 URL
2. 写入 `/analysis/report_{timestamp}.md`

## 图表类型速查

| 分析目的 | chart_type | 说明 |
|----------|------------|------|
| 供应商价格横向对比 | bar | 各供应商对同一物料报价 |
| 物料采购量/金额对比 | column | 按物料/供应商的柱状对比 |
| 价格/订单量趋势 | line | 时间序列趋势 |
| 采购金额占比 | pie | 按分类或供应商的饼图 |
| 供应商多维度评分 | radar | 价格/质量/交期/服务多轴 |
| 价格分布 | histogram | 价格区间分布直方图 |
| 采购金额层级 | treemap | 分类→物料层级占比 |
| 供应商-物料关系 | network_graph | 供应关系拓扑图 |
| 交货周期分布 | boxplot | 各供应商交货周期箱线图 |
| 成本构成增减 | waterfall | 采购成本逐项瀑布图 |
| 预算完成率 | liquid | 水波图，单一百分比指标 |
| 采购流程转化 | funnel | 各环节转化漏斗 |

> 完整 26 种图表及参数 schema 见 `/skills/procurement/chart_params.md`

## 报告模板

```markdown
# 采购分析报告

## 1. 分析概述
（目的、数据范围、方法说明）

## 2. 数据概览
（查询到的供应商数量、物料数量、数据时间范围）

## 3. 对比分析
（核心对比结果，附图表引用）

## 4. 结论与建议
（3-5 条可操作的采购建议）
```

## 返回主 Agent 格式

```
【报告路径】/analysis/report_xxx.md
【摘要】（300-500 字核心发现）
【结论】1. … 2. … 3. …
【建议】- 具体可执行的采购建议
```

**不要**返回原始工具输出或 JSON 数据。

## 文件管理说明

- DeepAgents 已内置自动 offload（>20k tokens）和 Summarization（85% 上下文阈值）
- 你只需显式写入：`/analysis/report_*.md`
- 其余工具返回由框架自动管理

## 分析资源

### 脚本模板
- `scripts/price_compare.py` — 价格对比分析
- `scripts/supplier_scoring.py` — 供应商综合评分

## 关键原则
- **技能发现**：每次任务开始先 `ls /skills/procurement/` 扫描可用技能，不依赖静态列表
- **数据真实**：所有结论基于真实数据，不编造
- **精简输出**：报告不超过 500 字摘要 + 3-5 条结论
- **成本意识**：先获取必要数据，避免无限循环查询

## 环境依赖
- Python 3.11+
- pandas, matplotlib, numpy（如未安装：`pip install -i https://mirrors.aliyun.com/pypi/simple/ pandas matplotlib numpy`）
