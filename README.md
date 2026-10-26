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
conda create -n ERP_OPENCLAW python=3.11.15 -y   # 同名环境已存在时 create 会直接报错，不覆盖
conda activate ERP_OPENCLAW
python -m pip install -r requirements.txt        # 唯一一条：第三方依赖 + dev 工具 + 本项目
cp .env.example .env
cd frontend && npm ci
```

`requirements.txt` 是完整锁文件，末尾已包含 `-e .`（项目自身 editable）和 dev 工具的
精确 pin，所以不需要再执行 `pip install -e '.[dev]'`。尤其**不要**给它加 `--no-deps`：
mypy 依赖 `mypy_extensions`/`pathspec`，`--no-deps` 会装出一个 import 即坏的 mypy。
`requirements.txt` 负责 `==` 精确复现，`pyproject.toml` 表达升级区间，两者用途不同。

在 `.env` 中填写模型和 OpenSandbox 凭据，不要提交 `.env`。
沙箱行为默认值：`PREWARM_SANDBOX=false`（启动不预热沙箱）、
`CLEANUP_SANDBOX_ON_SHUTDOWN=false`（重启保留用户远程沙箱）。

环境检查：

```bash
python scripts/check_environment.py
```

## 启动

先启动 MongoDB、Java ERP API、OpenSandbox（需要沙箱功能时）。

一键（推荐，会依次拉起 MCP → 全局沙箱 → Agent Protocol → FastAPI 后端）：

```bash
python start_web.py
```

或分终端调试：

```bash
make mcp        # 终端 1：ERP MCP
make backend    # 终端 2：FastAPI 后端
make frontend   # 终端 3：Vue 前端
```

- 前端：http://localhost:3000
- API 文档：http://localhost:8090/docs
- MCP：http://127.0.0.1:8000/mcp
- Agent Protocol（仅 `start_web.py` 会拉起）：http://127.0.0.1:2024

启动卡住或报坏的清理步骤见 `README.txt`；环境前置条件与已知坑见 `venv.txt`。

## 常用命令

```bash
make install
make check
make test
make lint
```

外部图表 MCP 为可选能力：仅在 `.env` 配置 `ANALYSIS_MCP_URL` 时加载，
不配置不影响 ERP 基础工具。
