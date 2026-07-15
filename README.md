# ERP OpenClaw

基于 LangGraph、DeepAgents、FastAPI、MCP 和 Vue 3 的 ERP 采购智能助手。

## 运行环境

- Python 3.10+（推荐 3.11）
- Node.js 18+
- MongoDB 6+（由你本地自行启动）
- Java ERP API，默认 `http://localhost:8080/api`
- OpenSandbox，默认 `http://localhost:8081`

## 初始化

```bash
python3.11 -m venv .venv
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
cd frontend && npm ci
```

在 `.env` 中填写模型和 OpenSandbox 凭据。不要提交 `.env`。
本地开发默认 `PREWARM_SANDBOX=false`，启动 Python 服务时不会主动创建沙箱；
设置为 `true` 后才会在启动阶段预热一个沙箱。
`CLEANUP_SANDBOX_ON_SHUTDOWN=false` 会在服务重启时保留用户远程沙箱；
只有明确需要关机清理远程资源时才设为 `true`。

环境检查：

```bash
.venv/bin/python scripts/check_environment.py
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
