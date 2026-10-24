 pip install fastmcp httpx langchain-daytona deepagents dotenv langchain-openai zai-sdk langchain-mcp-adapters
 pillow pyyaml opensandbox-code-interpreter fastapi uvicorn langgraph-checkpoint-mongodb


pip install fastapi uvicorn langgraph-checkpoint-mongodb
pip install -U "langgraph-cli[inmem]"

启动项目（Mac 终端里跑）：
1. 杀掉老进程
在你的 Mac 终端里跑这一句（把之前残留在后台的 MCP 进程全部杀掉）：
lsof -ti:8000 | xargs kill -9
(运行完没有任何提示就是杀干净了)

2. 清理错误状态
为了避免它去连之前创建了一半的坏沙箱，把状态文件删一下：
rm -rf /Users/Python/project/project-learn/python-code/ERP_OPENCLAW/.langgraph_api


3. 重新启动总控脚本
/opt/miniconda3/envs/ERP_OPENCLAW/bin/python /Users/Python/project/project-learn/python-code/ERP_OPENCLAW/start_web.py


https://my.feishu.cn/docx/WQVndx4S0odEFUxNXGKcmZSYn0c  基于Harness Engineering架构的企业实战项目


https://ocnu47uh43x2.feishu.cn/docx/Mp4Gdgexzo0d6cx8b3acaWKCnwg  从Agent到手撕OpenClaw的企业实战总结

