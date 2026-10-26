启动/安装看 README.md 与 venv.txt。本文件只记 Mac 排障。

前提：已 conda activate ERP_OPENCLAW，且当前目录是仓库根目录。下面三条都可以直接粘。

启动卡住时三步
--------------
1. 杀掉残留的 MCP 进程（8000 端口被占会导致 MCP 起不来）：

       lsof -ti:8000 | xargs kill -9

   运行完没有任何提示就是杀干净了。

2. 如果它一直去连之前创建了一半的坏沙箱，清掉状态目录：

       rm -rf .langgraph_api

   注意：里面有 global_sandbox.json（全局沙箱 id），清掉后下次会新建沙箱，旧沙箱成孤儿、
   要等创建时指定的 10 分钟 timeout 到期才回收。只是端口问题的话不用做这步。

3. 启动总控脚本（依次拉起 MCP → 全局沙箱 → Agent Protocol → FastAPI 后端）：

       python start_web.py

参考资料
--------
https://my.feishu.cn/docx/WQVndx4S0odEFUxNXGKcmZSYn0c  基于 Harness Engineering 架构的企业实战项目
https://ocnu47uh43x2.feishu.cn/docx/Mp4Gdgexzo0d6cx8b3acaWKCnwg  从 Agent 到手撕 OpenClaw 的企业实战总结
