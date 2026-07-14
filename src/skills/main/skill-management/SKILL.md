---
name: skill-management
description: 用于管理用户技能的完整生命周期：从 URL 下载技能 ZIP 包并解压、根据需求创建新技能、沙箱内测试验证、持久化到 StoreBackend、分配给指定子 Agent。当用户要下载、创建、安装、分配技能时激活。
---

# 技能管理

你负责管理用户技能的完整生命周期。所有操作在沙箱内执行（安全隔离），通过 StoreBackend 持久化（跨会话保留）。

## 技能架构

```
/skills/{scope}/     ← 沙箱内技能根目录（scope = Agent 对应的技能域）
  ├── {预置技能}/     ← SkillsSyncMiddleware 从本地同步（只读）
  └── {持久化技能}/   ← UserSkillsRestoreMiddleware 从 StoreBackend 恢复
```

### Scope 映射表

| 子 Agent | scope | 技能路径 |
|----------|-------|----------|
| `procurement-analyst` | `procurement` | `/skills/procurement/` |
| `procurement-order` | `order` | `/skills/order/` |

> 所有技能在 scope 下平级存放，不区分预置/持久化。
> 子 Agent 通过渐进式披露自动发现所属 scope 下的所有技能，无需手动激活。

## 工作流

### 阶段 1 — 下载并解压（沙箱内，`/skills/main/`）

用户提供 ZIP 下载 URL（如 `https://xxx.convex.site/api/v1/download?slug=web-scraper`）。

一条命令完成下载 + 解压 + 结构校验：
```
execute("python /skills/main/skill-management/scripts/download_skill.py '{url}'")
```

脚本自动完成：
- 从 URL 的 `slug` 参数提取技能名
- `urllib` 下载 ZIP 到 `/skills/main/{name}.zip`
- `zipfile` 解压到 `/skills/main/{name}/`
- 校验：`SKILL.md` 存在性 + frontmatter（name、description）
- 清理 zip 包
- 输出 JSON：`{success, skill_name, path, files[], errors[]}`

> `success=false` → 根据 errors 信息修复，必要时重新下载。
> `success=true` → 进入阶段 2。

### 阶段 2 — 创建模式（用户描述需求，无 URL）

当用户直接描述需求而不是提供 URL 时：
1. 根据用户需求生成 SKILL.md，包含 frontmatter（name、description）和步骤说明
2. `write_file("/skills/main/{name}/SKILL.md", content)`
3. 如有附属 .py 脚本，一并写入同目录

### 阶段 3 — 功能测试（沙箱内）

在 `/skills/main/{name}/` 下进行：
```
① 阅读 SKILL.md 中的使用说明，确认入口和参数
② 如有 .py 脚本依赖第三方库，使用 pip 安装（阿里云镜像加速）：
   execute("pip install -i https://mirrors.aliyun.com/pypi/simple/ <package_name>")
   pip/python 已通过 PATH 注入自动路由到 /opt/skills-venv/bin/，无需绝对路径。
   常见依赖（numpy/pandas/matplotlib/requests/beautifulsoup4）已在沙箱初始化时预装到 venv
③ execute("python -m py_compile /skills/main/{name}/*.py") 检查语法
④ execute("cd /skills/main/{name} && python script.py --help") 确认可执行
⑤ 纯指引型技能（无脚本）: 跳过执行测试，仅验证 SKILL.md 步骤完整性
⑥ 失败 → 修复 → 重测，直到全部通过
```

### 阶段 4 — 分配 + 持久化

测试全部通过后：
```
① 调用 assign_skill(skill_name="{name}", subagent_name="{子Agent名}")
   → 工具自动将 /skills/main/{name}/ 复制到 /skills/{scope}/{name}/
   → 子 Agent 下一轮对话即可通过渐进式披露发现新技能

② 持久化到 StoreBackend（跨会话保留）：
   read_file("/skills/main/{name}/SKILL.md") → 获取内容
   write_file("/persisted-skills/{scope}/{name}/SKILL.md", 内容)
   附属文件同理：write_file("/persisted-skills/{scope}/{name}/xxx.py", ...)

③ 清理: execute("rm -rf /skills/main/{name}")
```

> scope 由用户指定的目标子 Agent 决定（查 Scope 映射表）。如用户未指定，先提醒再执行。
> 持久化后，下次会话 UserSkillsRestoreMiddleware 自动恢复到沙箱 `/skills/{scope}/{name}/`。

### 阶段 5 — 未指定子 Agent 时的提醒

```
用户未指定目标 → 提醒: "技能 '{name}' 已通过测试！请指定分配给哪个子 Agent？
             可用: procurement-analyst（采购分析）, procurement-order（采购订单）"
           → 等待用户回复后 → 执行阶段 4
```

## 可用工具

- `execute("python /skills/main/skill-management/scripts/download_skill.py '{url}'")`: 下载 ZIP + 解压 + 结构校验，一步完成。输出 JSON。
- `assign_skill(skill_name, subagent_name)`: 将 /skills/main/{name}/ 复制到 /skills/{scope}/{name}/。自动校验子 Agent → scope 映射，纯沙箱文件操作。
- `execute`: 执行 Shell 命令（Python 脚本测试、文件清理等）
- `read_file` / `write_file`: 文件读写（沙箱 + StoreBackend `/persisted-skills/`）
- `ls`: 查看目录结构
