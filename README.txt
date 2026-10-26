启动/安装看 README.md 与 venv.txt。本文件只记 Mac 排障。

启动卡住时三步
--------------
1. 杀残留 MCP 进程（8000 端口被占会导致 MCP 起不来）：
       lsof -ti:8000 | xargs kill -9
   没有输出就是杀干净了。

2. 如果它一直去连之前创建一半的坏沙箱，删掉状态目录 .langgraph_api/。
   注意：里面有 global_sandbox.json（全局沙箱 id），删了下次会新建沙箱，
   旧沙箱成孤儿、要等创建时指定的 10 分钟 timeout 到期才回收。端口问题别动这里。

3. 重新启动总控脚本（依次拉起 MCP → 全局沙箱 → Agent Protocol → 后端）：
       conda activate ERP_OPENCLAW
       python start_web.py

参考资料
--------
https://my.feishu.cn/docx/WQVndx4S0odEFUxNXGKcmZSYn0c  基于 Harness Engineering 架构的企业实战项目
https://ocnu47uh43x2.feishu.cn/docx/Mp4Gdgexzo0d6cx8b3acaWKCnwg  从 Agent 到手撕 OpenClaw 的企业实战总结
