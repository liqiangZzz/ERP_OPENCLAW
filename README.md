# ERP OpenClaw

基于 LangGraph、DeepAgents、FastAPI、MCP 和 Vue 3 的 ERP 采购智能助手。

## 运行环境

- Python 3.11+（`deepagents` 等依赖要求 `>=3.11`，3.10 装不上）
- Node.js 18+
- MongoDB 6+（由你本地自行启动）
- Java ERP API，默认 `http://localhost:8080/api`
- OpenSandbox，默认 `http://localhost:8081`

## 初始化

本项目用 conda 环境管理（环境名 `ERP_OPENCLAW`）。

```bash
conda activate ERP_OPENCLAW          # 或 conda create -n ERP_OPENCLAW python=3.11
python -m pip install -r requirements.txt    # 第 1 步：第三方依赖，全量精确锁定
python -m pip install -e '.[dev]' --no-deps  # 第 2 步：本项目本体 + 开发工具
cp .env.example .env
cd frontend && npm ci
```

第 2 步的 `--no-deps` 是必要的：它阻止 pip 按 `pyproject.toml` 的区间重新解析依赖，
从而覆盖第 1 步刚锁好的精确版本。`requirements.txt` 里不含 `-e .`，因为该行会按
「当前工作目录」解析 `.`，只有在项目根目录执行才成立；拆成两步可以避免这个坑。

`requirements.txt` 是全量精确锁定（`==`，含传递依赖），用于复现；
`pyproject.toml` 保留区间式声明（`>=x,<y`），表达升级意图。两者用途不同，改版本时按需选一个。

在 `.env` 中填写模型和 OpenSandbox 凭据。不要提交 `.env`。
本地开发默认 `PREWARM_SANDBOX=false`，启动 Python 服务时不会主动创建沙箱；
设置为 `true` 后才会在启动阶段预热一个沙箱。
`CLEANUP_SANDBOX_ON_SHUTDOWN=false` 会在服务重启时保留用户远程沙箱；
只有明确需要关机清理远程资源时才设为 `true`。

环境检查：

```bash
python scripts/check_environment.py
```

## 启动

各服务手动启动，便于直接查看日志和处理端口占用。启动顺序：

1. 启动 MongoDB Docker 容器。
2. 启动 Java ERP API。
3. 启动 OpenSandbox（需要沙箱功能时）。
4. 启动 ERP MCP。
5. 启动 FastAPI 后端。
6. 启动 Vue 前端。

后三个服务分别在独立终端运行：

```bash
# 终端 1：ERP MCP
make mcp

# 终端 2：FastAPI 后端
make backend

# 终端 3：Vue 前端
make frontend
```

访问地址：

- 前端：http://localhost:3000
- API 文档：http://localhost:8090/docs
- MCP：http://127.0.0.1:8000/mcp

## 常用命令

```bash
make install
make check
make test
make lint
```

外部图表 MCP 是可选能力。仅在 `.env` 配置 `ANALYSIS_MCP_URL` 时加载，不配置不影响 ERP 基础工具。
