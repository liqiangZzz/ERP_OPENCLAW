"""
技能下载脚本：从 URL 下载 ZIP 包，解压到沙箱 /skills/main/，校验结构。

仅依赖 Python 3.11 标准库（urllib + zipfile），无需安装额外依赖。

用法:
    python download_skill.py <URL>

    URL 示例: https://wry-manatee-359.convex.site/api/v1/download?slug=web-scraper

输出:
    JSON 到 stdout，包含 success / skill_name / path / files / errors 字段。
    exit code 0 = 成功，非 0 = 失败。
"""

import json
import os
import re
import sys
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 沙箱内技能工作目录
MAIN_SKILLS_DIR = "/skills/main"


def extract_slug(url: str) -> str:
    """从 URL 的 slug 参数中提取技能名。失败时从路径末尾提取。"""
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    slug = params.get("slug", [None])[0]
    if slug:
        return slug
    # 回退：取路径最后一段
    path_parts = [p for p in parsed.path.split("/") if p]
    if path_parts:
        return path_parts[-1]
    raise ValueError(f"无法从 URL 中提取技能名: {url}")


def parse_frontmatter(text: str) -> dict:
    """解析 Markdown YAML frontmatter（无需 pyyaml，简易版）。"""
    if not text.startswith("---"):
        return {}
    # 找到第二个 ---
    end = text.find("---", 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    result = {}
    for line in fm_text.split("\n"):
        match = re.match(r'^(\w[\w_-]*)\s*:\s*(.+)', line)
        if match:
            key = match.group(1)
            value = match.group(2).strip().strip('"').strip("'")
            result[key] = value
    return result


def validate_skill(skill_dir: Path) -> list[str]:
    """校验解压后的技能目录。返回错误列表，空列表 = 通过。"""
    errors = []

    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        errors.append("缺少 SKILL.md 文件")
        return errors

    content = skill_md.read_text(encoding="utf-8")
    if not content.strip():
        errors.append("SKILL.md 内容为空")
        return errors

    fm = parse_frontmatter(content)
    if not fm:
        errors.append("SKILL.md 缺少 YAML frontmatter（--- ... ---）")
    else:
        if "name" not in fm:
            errors.append("SKILL.md frontmatter 缺少 name 字段")
        if "description" not in fm:
            errors.append("SKILL.md frontmatter 缺少 description 字段")

    return errors


def download_skill(url: str) -> dict:
    """主流程：下载 → 解压 → 校验 → 清理。返回结果字典。"""
    skill_name = extract_slug(url)

    # 确保目标目录存在
    os.makedirs(MAIN_SKILLS_DIR, exist_ok=True)

    zip_path = Path(MAIN_SKILLS_DIR) / f"{skill_name}.zip"
    skill_dir = Path(MAIN_SKILLS_DIR) / skill_name

    # ---- 步骤 1: 下载 ----
    try:
        urllib.request.urlretrieve(url, str(zip_path))
    except Exception as e:
        return {
            "success": False,
            "skill_name": skill_name,
            "errors": [f"下载失败: {e}"],
        }

    # ---- 步骤 2: 解压 ----
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(str(skill_dir))
    except zipfile.BadZipFile:
        zip_path.unlink(missing_ok=True)
        return {
            "success": False,
            "skill_name": skill_name,
            "errors": ["文件不是有效的 ZIP 压缩包"],
        }
    except Exception as e:
        zip_path.unlink(missing_ok=True)
        return {
            "success": False,
            "skill_name": skill_name,
            "errors": [f"解压失败: {e}"],
        }

    # ---- 步骤 3: 列出文件 ----
    all_files = sorted(
        p.relative_to(skill_dir).as_posix()
        for p in skill_dir.rglob("*")
        if p.is_file()
    )

    # ---- 步骤 4: 结构校验 ----
    errors = validate_skill(skill_dir)

    # ---- 步骤 5: 清理 zip ----
    zip_path.unlink(missing_ok=True)

    return {
        "success": len(errors) == 0,
        "skill_name": skill_name,
        "path": str(skill_dir),
        "files": all_files,
        "errors": errors,
    }


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "errors": ["缺少 URL 参数"]}, ensure_ascii=False))
        sys.exit(1)

    url = sys.argv[1]
    result = download_skill(url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
