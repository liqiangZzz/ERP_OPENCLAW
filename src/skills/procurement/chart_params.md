# 可视化图表参数参考

`generate_visualization(chart_type, chart_config)` 的 26 种图表参数速查。
**以下每种图表只列出 data 格式和特有参数**，通用参数见下方。

---

## 通用可选参数（所有图表都支持，以下不再逐图重复）

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `width` | number | 600 | 图表宽度 |
| `height` | number | 400 | 图表高度 |
| `title` | string | — | 图表标题 |
| `theme` | string | 'default' | 可选: 'academy'、'dark' |
| `style.backgroundColor` | string | — | 背景色，如 '#fff' |
| `style.palette` | array | — | 颜色数组 |
| `style.texture` | string | 'default' | 'rough' = 手绘风格 |
| `axisXTitle` | string | — | X轴标题（有轴图表） |
| `axisYTitle` | string | — | Y轴标题（有轴图表） |

---

## category-value 模式

### bar — 供应商价格横向对比
- **data**: `[{category, value, group?}]`
- **特有**: `group`(bool), `stack`(bool) — 分组/堆叠，互斥

### column — 物料采购量/金额纵向对比
- **data**: `[{category, value, group?}]`
- **特有**: `group`(bool), `stack`(bool) — 同 bar

### pie — 采购金额占比（饼图）
- **data**: `[{category, value}]`
- **特有**: `innerRadius`(0~1) — 设为 0.6 左右变环形图

### funnel — 采购流程转化漏斗
- **data**: `[{category, value}]`

### treemap — 采购金额层级矩形树图
- **data**: `{name, value, children?: [{name, value, children?: [...]}]}` — 最大深度 3 层

### word_cloud — 物料/供应商高频词云
- **data**: `[{text, value}]`

---

## time-value 模式

### line — 价格/订单量时间趋势
- **data**: `[{time, value, group?}]`
- **特有**: `style.startAtZero`(bool), `style.lineWidth`(number) — 线宽如 4

### area — 库存量等随时间变化面积图
- **data**: `[{time, value, group?}]`
- **特有**: `stack`(bool) — 堆叠面积; `style.lineWidth`(number)

---

## 分布/统计模式

### boxplot — 供应商交货周期分布箱线图
- **data**: `[{category, value, group?}]`
- **特有**: `style.startAtZero`(bool)

### violin — 数据分布密度小提琴图
- **data**: `[{category, value, group?}]`
- **特有**: `style.startAtZero`(bool)

### histogram — 价格/交货周期数值分布
- **data**: `[number, number, ...]` — 纯数值数组
- **特有**: `binNumber`(number) — 分组区间数

### scatter — 价格-质量二维相关性
- **data**: `[{x, y, group?}]`

---

## 多维度/流向/集合

### radar — 供应商多维度综合评分雷达图
- **data**: `[{name, value, group?}]`
- **特有**: `style.lineWidth`(number)

### sankey — 物料从供应商到仓库流转桑基图
- **data**: `[{source, target, value}]`
- **特有**: `nodeAlign`('left'|'right'|'justify'|'center')

### venn — 供应商与物料交叉韦恩图
- **data**: `[{label?, sets, value}]` — sets 如 `['A']` 或 `['A','B']` 表示交集

### waterfall — 采购成本逐项增减瀑布图
- **data**: `[{category, value?, isIntermediateTotal?, isTotal?}]`
- **特有**: `style.palette.positiveColor`(默认'#FF4D4F'), `style.palette.negativeColor`(默认'#2EBB59'), `style.palette.totalColor`(默认'#1783FF')

### dual_axes — 价格+数量双轴复合（柱+线）
- **categories**: `[string, ...]` — X轴类别
- **series**: `[{type: 'column'|'line', data: [number,...], axisYTitle?}]`
- **特有**: `style.startAtZero`(bool)
- **注意**: line 的 data 建议值 < 1（比率）

---

## 层级树模式

### organization — 组织架构层级图
- **data**: `{name, description?, children: [...]}` — 最大深度 3
- **特有**: `orient`('horizontal'|'vertical') — 层级>3 建议 horizontal

### mind_map — 采购策略/产品架构思维导图
- **data**: `{name, children: [{name, children: [...]}]}` — 最大深度 3

### fishbone_diagram — 质量问题根因鱼骨图
- **data**: `{name, children: [{name, children: [...]}]}` — 最大深度 3

---

## 节点-边图模式

### flow_diagram — 采购审批/业务流程流程图
- **data**: `{nodes: [{name}], edges: [{source, target, name?}]}`

### network_graph — 供应商-物料关系网络拓扑
- **data**: `{nodes: [{name}], edges: [{source, target, name?}]}`

---

## 特殊图表

### liquid — 采购完成率/预算执行率水波图
- **特有**: `percent`(0~1) **必填**; `shape`('circle'|'rect'|'pin'|'triangle'); `style.color`(string)

---

## 地图（width/height 默认 1600×1000）

### district_map — 中国行政区划数据分布
- **特有**: `title` **必填**(≤16字); `data.name` **必填** — 中国行政区名(省/市/区/县); `data.subdistricts` — 下级区域数组; `data.dataType`('number'|'enum'); `data.colors` — 颜色数组; `data.showAllSubdistricts`(bool); `data.dataLabel`/`data.dataValue`/`data.dataValueUnit` — 数据标注

### pin_map — 供应商/仓库地理位置标记
- **特有**: `title` **必填**(≤16字); `data` **必填** — POI 中文名数组如 `["西安钟楼", "西安大雁塔"]`; `markerPopup` — 图片弹窗 `{type:"image", width, height, borderRadius}`

### path_map — 物流配送路线
- **特有**: `title` **必填**(≤16字); `data` **必填** — 路线数组 `[{data: ["起点","途经点","终点"]}]`
