ERP_OPENCLAW 本地备忘（安装/启动的权威说明在 README.md 与 venv.txt，本文件只记排障）

安装与运行
----------
依赖只有一条入口，别再单独记一串包名：

    pip install -r requirements.txt

本文件原先那两条裸安装命令已删除，原因是它们和锁文件直接冲突：

  * `pip install fastmcp httpx langchain-daytona dotenv ... pillow pyyaml ...`
    —— 无版本约束，会把 requirements.txt 锁好的包升到未验证的版本。
    其中 `dotenv` 是包名写错（正确的是 `python-dotenv`），环境里那个
    dotenv==0.9.9 空壳包就是这么进来的；`langchain-daytona` 全仓没有任何引用
    （只有 src/agent/env_utils.py 里两个从未被使用的 DAYTONA_* 变量），不需要装。
  * `pip install -U "langgraph-cli[inmem]"`
    —— 违反本仓库"conda 环境不用 pip install -U"的规则，会把 langgraph-cli /
    langgraph-api / langgraph-runtime-inmem 升到与 conda-meta 不一致的版本。
    langgraph-cli[inmem] 已经在 requirements.txt 里 pin 好了，不需要手动装。

启动卡住时的三步排障（Mac 终端）
--------------------------------
1. 杀掉残留的 MCP 进程（8000 端口被占会导致 MCP 起不来）：

       lsof -ti:8000 | xargs kill -9

   跑完没有任何输出就是杀干净了。

2. 清理坏沙箱状态——删掉 .langgraph_api/ 目录：

       rm -rf /Users/Python/project/project-learn/python-code/ERP_OPENCLAW/.langgraph_api

   副作用要先知道：这个目录除了 langgraph dev 的本地 checkpoint/store，还有
   global_sandbox.json（全局沙箱 id）。删掉它 = 下次启动新建一个全局沙箱，
   旧沙箱成孤儿，要等它创建时指定的 10 分钟 timeout 到期才被自动回收。
   如果只是 MCP 端口问题，优先做第 1 步，别急着删这里。

3. 重新启动总控脚本（依次拉起 MCP → 全局沙箱 → Agent Protocol → FastAPI 后端）：

       conda activate ERP_OPENCLAW
       python /Users/Python/project/project-learn/python-code/ERP_OPENCLAW/start_web.py

   成功标志：MCP server is ready → Global sandbox ready → Agent Protocol(127.0.0.1:2024)
   起来且日志里没有 GraphLoadError。

参考资料（飞书）
----------------
https://my.feishu.cn/docx/WQVndx4S0odEFUxNXGKcmZSYn0c  基于 Harness Engineering 架构的企业实战项目
https://ocnu47uh43x2.feishu.cn/docx/Mp4Gdgexzo0d6cx8b3acaWKCnwg  从 Agent 到手撕 OpenClaw 的企业实战总结
