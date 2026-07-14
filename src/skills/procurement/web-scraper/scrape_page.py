#!/usr/bin/env python3
"""
网页抓取工具 - Web Scraper
从沙箱内直接发起 HTTP 请求，解析 HTML 并转换为 Markdown 保存。

用法:
    python scrape_page.py --url "http://39.100.100.28/spark-plug-bosch.html"
    python scrape_page.py --url "http://39.100.100.28/spark-plug-bosch.html" --output "/analysis/temp/result.md"
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = "/analysis/temp"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 2
RETRY_DELAY = 2  # 秒

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

# 需要完全移除的标签
REMOVE_TAGS = ["script", "style", "noscript", "iframe", "svg", "nav", "footer", "header"]

# ---------------------------------------------------------------------------
# HTML → Markdown 转换（手动实现，无外部依赖）
# ---------------------------------------------------------------------------

def _extract_main_content(soup: BeautifulSoup) -> Tag:
    """尝试定位页面主体内容区域。"""
    # 按优先级查找主体容器
    selectors = [
        "article", "main", '[role="main"]',
        ".content", ".article", ".post", ".page-content", ".main-content",
        "#content", "#article", "#main",
    ]
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            return el
    # 回退到 body
    body = soup.body
    return body if body else soup


def _clean_soup(soup: BeautifulSoup) -> None:
    """移除无关标签和属性。"""
    for tag_name in REMOVE_TAGS:
        for t in soup.find_all(tag_name):
            t.decompose()
    # 移除常见非内容元素
    for cls in ["sidebar", "comment", "advertisement", "popup", "modal", "menu"]:
        for t in soup.find_all(class_=lambda c: c and cls in c if c else False):
            t.decompose()
    # 清理所有标签的属性，只保留 href / src / alt
    for t in soup.find_all(True):
        keep = {}
        if t.get("href"):
            keep["href"] = t["href"]
        if t.get("src"):
            keep["src"] = t["src"]
        if t.get("alt"):
            keep["alt"] = t["alt"]
        t.attrs = keep


def _html_to_markdown(element: Tag, base_url: str = "") -> str:
    """递归将 BeautifulSoup 元素树转换为 Markdown 文本。"""
    lines: list[str] = []
    _walk(element, lines, base_url)
    # 清理多余空行
    result = "\n".join(lines)
    while "\n\n\n" in result:
        result = result.replace("\n\n\n", "\n\n")
    return result.strip() + "\n"


def _resolve_url(href: str, base_url: str) -> str:
    """将相对 URL 转为绝对 URL。"""
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if base_url:
        from urllib.parse import urljoin
        return urljoin(base_url, href)
    return href


def _walk(el, lines: list[str], base_url: str) -> None:
    """递归遍历 DOM 树，追加 Markdown 行。"""
    if not isinstance(el, Tag):
        text = str(el).strip()
        if text:
            lines.append(text)
        return

    tag = el.name.lower() if el.name else ""

    # 块级元素 —— 前置空行
    block_tags = {
        "p", "div", "section", "article", "header", "footer", "main",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "ul", "ol", "li", "blockquote", "pre", "table", "tr", "hr", "br",
        "dl", "dt", "dd", "figure", "figcaption", "details", "summary",
    }
    if tag in block_tags and lines and lines[-1] != "":
        lines.append("")

    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        level = int(tag[1])
        text = el.get_text(" ", strip=True)
        lines.append(f"{'#' * level} {text}")
        lines.append("")

    elif tag == "p":
        text = _inline_content(el, base_url)
        if text:
            lines.append(text)
        lines.append("")

    elif tag == "br":
        lines.append("")

    elif tag == "hr":
        lines.append("---")
        lines.append("")

    elif tag == "blockquote":
        for child in el.children:
            sub: list[str] = []
            _walk(child, sub, base_url)
            for s in sub:
                if s:
                    lines.append(f"> {s}")
                else:
                    lines.append(">")
        lines.append("")

    elif tag == "pre":
        code_el = el.find("code")
        lang = ""
        if code_el:
            cls = code_el.get("class", [])
            if cls:
                for c in cls:
                    if c.startswith("language-") or c.startswith("lang-"):
                        lang = c.split("-", 1)[1]
                        break
        text = el.get_text().rstrip()
        lines.append(f"```{lang}")
        lines.append(text)
        lines.append("```")
        lines.append("")

    elif tag in ("ul", "ol"):
        for li in el.find_all("li", recursive=False):
            prefix = "- " if tag == "ul" else "1. "
            text = _inline_content(li, base_url)
            lines.append(f"{prefix}{text}")
        lines.append("")

    elif tag == "li":
        # 被 ul/ol 遍历时处理，这里处理孤立 li
        text = _inline_content(el, base_url)
        if text:
            lines.append(f"- {text}")

    elif tag == "table":
        _table_to_md(el, lines, base_url)
        lines.append("")

    elif tag == "img":
        src = _resolve_url(el.get("src", ""), base_url)
        alt = el.get("alt", "")
        lines.append(f"![{alt}]({src})")

    elif tag == "a":
        href = _resolve_url(el.get("href", ""), base_url)
        text = el.get_text(" ", strip=True)
        if text and href and not href.startswith("javascript:"):
            lines.append(f"[{text}]({href})")
        elif text:
            lines.append(text)

    elif tag in ("strong", "b"):
        lines.append(f"**{el.get_text(' ', strip=True)}**")

    elif tag in ("em", "i"):
        lines.append(f"*{el.get_text(' ', strip=True)}*")

    elif tag == "code":
        parent_tag = el.parent.name.lower() if el.parent and el.parent.name else ""
        if parent_tag != "pre":
            lines.append(f"`{el.get_text(strip=True)}`")

    elif tag in ("span", "label", "small", "sub", "sup", "abbr", "cite"):
        text = el.get_text(" ", strip=True)
        if text:
            lines.append(text)

    else:
        # 递归处理子节点
        for child in el.children:
            _walk(child, lines, base_url)

    if tag in block_tags and tag not in ("br", "hr") and lines and lines[-1] != "":
        lines.append("")


def _inline_content(el: Tag, base_url: str) -> str:
    """提取内联内容，保留链接/加粗等格式。"""
    parts: list[str] = []
    for child in el.children:
        if isinstance(child, Tag):
            t = child.name.lower() if child.name else ""
            if t == "a":
                href = _resolve_url(child.get("href", ""), base_url)
                txt = child.get_text(" ", strip=True)
                if txt and href and not href.startswith("javascript:"):
                    parts.append(f"[{txt}]({href})")
                elif txt:
                    parts.append(txt)
            elif t in ("strong", "b"):
                parts.append(f"**{child.get_text(' ', strip=True)}**")
            elif t in ("em", "i"):
                parts.append(f"*{child.get_text(' ', strip=True)}*")
            elif t == "code":
                parts.append(f"`{child.get_text(strip=True)}`")
            elif t == "img":
                src = _resolve_url(child.get("src", ""), base_url)
                alt = child.get("alt", "")
                parts.append(f"![{alt}]({src})")
            elif t in ("br",):
                parts.append("\n")
            else:
                parts.append(child.get_text(" ", strip=True))
        else:
            txt = str(child).strip()
            if txt:
                parts.append(txt)
    return "".join(parts)


def _table_to_md(table: Tag, lines: list[str], base_url: str) -> None:
    """将 HTML table 转为 Markdown 表格。"""
    rows = table.find_all("tr")
    if not rows:
        return
    md_rows: list[list[str]] = []
    for tr in rows:
        cells = tr.find_all(["th", "td"])
        md_rows.append([cell.get_text(" ", strip=True) for cell in cells])
    if not md_rows:
        return
    max_cols = max(len(r) for r in md_rows)
    # 补齐列数
    for r in md_rows:
        while len(r) < max_cols:
            r.append("")
    # 表头
    lines.append("| " + " | ".join(md_rows[0]) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")
    # 数据行
    for row in md_rows[1:]:
        lines.append("| " + " | ".join(row) + " |")


# ---------------------------------------------------------------------------
# 主逻辑
# ---------------------------------------------------------------------------

def _make_output_path(url: str, output: str | None) -> str:
    if output:
        return output
    parsed = urlparse(url)
    domain = parsed.netloc.replace(":", "_")
    path = parsed.path.strip("/")
    if not path or path.endswith("/"):
        path = "index"
    # 清理文件名
    safe_name = path.replace("/", "_").replace("\\", "_")
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    return f"{DEFAULT_OUTPUT_DIR}/{domain}_{safe_name}"


def _try_install_markdownify() -> bool:
    """尝试 pip install markdownify（PATH 已注入 venv，pip 直接可用）。"""
    import subprocess
    try:
        subprocess.run(
            ["pip", "install", "-i", "https://mirrors.aliyun.com/pypi/simple/", "markdownify", "-q"],
            capture_output=True, timeout=30,
        )
        return True
    except Exception:
        return False


def fetch_and_convert(url: str, output_path: str) -> str:
    """抓取网页并转换为 Markdown 保存。

    Returns:
        结果摘要字符串。
    """
    # 1. 发起 HTTP 请求（带重试）
    session = requests.Session()
    session.headers.update(HEADERS)

    last_error = ""
    for attempt in range(MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            break
        except requests.RequestException as e:
            last_error = str(e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
    else:
        return f"错误：请求失败（{MAX_RETRIES + 1} 次重试后）: {last_error}"

    # 2. 检测编码
    resp.encoding = resp.apparent_encoding or resp.encoding or "utf-8"
    html = resp.text

    # 3. 解析 HTML
    soup = BeautifulSoup(html, "lxml" if _has_lxml() else "html.parser")

    # 4. 提取标题
    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)

    # 5. 提取主体内容并清理
    main = _extract_main_content(soup)
    _clean_soup(main)

    # 6. 尝试 markdownify
    md_content = _try_markdownify(main, url)
    if md_content is None:
        # 回退到手动转换
        md_content = _html_to_markdown(main, url)

    # 7. 组装最终 Markdown
    final_md_parts: list[str] = []
    if title:
        final_md_parts.append(f"# {title}")
        final_md_parts.append("")
    final_md_parts.append(f"> 来源: {url}")
    final_md_parts.append(f"> 抓取时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    final_md_parts.append("")
    final_md_parts.append(md_content)

    final_md = "\n".join(final_md_parts)

    # 8. 保存文件
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(final_md, encoding="utf-8")

    size_kb = len(final_md.encode("utf-8")) / 1024
    return (
        f"✅ 网页抓取成功\n"
        f"URL: {url}\n"
        f"标题: {title or '(无标题)'}\n"
        f"保存路径: {output_path}\n"
        f"文件大小: {size_kb:.1f} KB"
    )


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except ImportError:
        return False


def _try_markdownify(main: Tag, base_url: str) -> str | None:
    """尝试使用 markdownify 库转换，失败返回 None。"""
    try:
        from markdownify import markdownify as md_convert
    except ImportError:
        if _try_install_markdownify():
            try:
                from markdownify import markdownify as md_convert
            except ImportError:
                return None
        else:
            return None

    try:
        html_str = str(main)
        result = md_convert(
            html_str,
            heading_style="ATX",
            bullets="-",
            strip=["script", "style", "noscript"],
        )
        if result and result.strip():
            return result.strip()
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="网页抓取工具 - 抓取网页并保存为 Markdown")
    parser.add_argument("--url", required=True, help="目标网页 URL")
    parser.add_argument("--output", default=None, help="输出 Markdown 文件路径")
    args = parser.parse_args()

    output_path = _make_output_path(args.url, args.output)
    try:
        result = fetch_and_convert(args.url, output_path)
        print(result)
    except Exception:
        print(f"❌ 抓取失败:\n{traceback.format_exc()}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
