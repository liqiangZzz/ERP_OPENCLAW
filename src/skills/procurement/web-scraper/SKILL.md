---
name: web-scraper
description: "网页抓取工具 | 从沙箱内直接发起 HTTP 请求获取网页内容，解析 HTML 并转换为干净的 Markdown 文件保存到 /analysis/temp/。支持内网 URL（如 http://39.100.100.28/...）。触发词：抓取网页、网页转markdown、爬取内容、保存网页、scrape webpage、fetch and save"
author: Boss
metadata:
  openclaw:
    emoji: 🕷️
    tags: [web, scraper, markdown, html-parsing, direct-http]
---

# 网页抓取工具 (Web Scraper)

从沙箱内直接发起 HTTP 请求，解析 HTML 并转换为 Markdown 保存。不依赖任何外部代理服务，可直接访问内网 URL。

## 适用场景

- 抓取内网页面（如 `http://39.100.100.28/...`），外部代理服务无法访问
- 需要将网页内容保存为 Markdown 文件供后续分析
- 批量抓取产品/供应商页面

## 使用方法

```bash
# 基本用法（自动生成输出文件名）
python /skills/procurement/web-scraper/scrape_page.py \
  --url "http://39.100.100.28/spark-plug-bosch.html"

# 指定输出路径
python /skills/procurement/web-scraper/scrape_page.py \
  --url "http://39.100.100.28/spark-plug-bosch.html" \
  --output "/analysis/temp/bosch-spark-plug.md"
```

## 参数

| 参数 | 必填 | 说明 |
|------|------|------|
| `--url` | 是 | 目标网页 URL |
| `--output` | 否 | 输出 Markdown 文件路径，默认 `/analysis/temp/{domain}_{path}.md` |

## 工作原理

1. **直接 HTTP 请求** — 使用 `requests` 库从沙箱内发起 GET 请求，模拟浏览器 User-Agent
2. **HTML 解析** — 使用 BeautifulSoup4 移除 script/style/nav/footer 等干扰元素，提取主体内容
3. **Markdown 转换** — 按优先级尝试 markdownify → html2text → 手动转换，保留标题/链接/列表/表格结构
4. **保存文件** — 写入指定路径，自动创建父目录

## 示例

```
用户: 帮我抓取 http://39.100.100.28/spark-plug-bosch.html 的内容并保存
助手: (执行 python scrape_page.py --url "...") → 保存到 /analysis/temp/spark-plug-bosch.md
```

---

*直接 HTTP，不依赖外部代理 🕷️*
