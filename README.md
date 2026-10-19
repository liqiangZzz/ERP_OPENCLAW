# ERP OpenClaw

基于 LangGraph、DeepAgents、FastAPI、MCP 和 Vue 3 的 ERP 采购智能助手。

## 运行环境

- Python 3.11+（deepagents 等依赖要求 `>=3.11`）
- Node.js 18+
- MongoDB 6+
- Java ERP API，默认 `http://localhost:8080/api`
- OpenSandbox，默认 `http://localhost:8081`

## 初始化

使用 conda 环境管理（环境名 `ERP_OPENCLAW`）：

```bash
conda activate ERP_OPENCLAW          # 或 conda create -n ERP_OPENCLAW python=3.11
python -m pip install -r requirements.txt    # 第 1 步：第三方依赖，全量精确锁定
python -m pip install -e '.[dev]' --no-deps  # 第 2 步：本项目本体 + 开发工具
cp .env.example .env
cd frontend && npm ci
```

第 2 步的 `--no-deps` 防止 pip 按 `pyproject.toml` 的区间重新解析、覆盖第 1 步
锁定的版本。`requirements.txt` 负责 `==` 精确复现，`pyproject.toml` 表达升级
区间，两者用途不同。

在 `.env` 中填写模型和 OpenSandbox 凭据，不要提交 `.env`。
沙箱行为默认值：`PREWARM_SANDBOX=false`（启动不预热沙箱）、
`CLEANUP_SANDBOX_ON_SHUTDOWN=false`（重启保留用户远程沙箱）。

环境检查：

```bash
python scripts/check_environment.py
```

## 启动

先启动 MongoDB、Java ERP API、OpenSandbox（需要沙箱功能时），
再在三个终端分别运行：

```bash
make mcp        # 终端 1：ERP MCP
make backend    # 终端 2：FastAPI 后端
make frontend   # 终端 3：Vue 前端
```

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

外部图表 MCP 为可选能力：仅在 `.env` 配置 `ANALYSIS_MCP_URL` 时加载，
不配置不影响 ERP 基础工具。
