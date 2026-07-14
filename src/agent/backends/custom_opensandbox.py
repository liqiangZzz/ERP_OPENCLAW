# =============================================================================
# ★ 模块：OpenSandbox 沙箱后端实现
# =============================================================================
"""OpenSandbox 沙箱后端实现，遵循 SandboxBackendProtocol 协议。

核心职责:
- 封装 OpenSandbox SDK 的命令执行、文件上传/下载
- 继承 BaseSandbox 处理文件读取、写入、编辑等操作
- 自动注入 PATH 环境变量，确保沙箱内非交互式 shell 能访问 pip/python

使用方式:
    from agent.backends.custom_opensandbox import OpenSandboxBackend
    from opensandbox import SandboxSync

    sandbox = SandboxSync.create(...)
    backend = OpenSandboxBackend(sandbox=sandbox, timeout=3600)
    result = backend.execute("pip install numpy")
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import cast

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from opensandbox import SandboxSync
from opensandbox.models import WriteEntry

# =============================================================================
# ★ 1. 类型定义
# =============================================================================
# 同步轮询间隔类型：固定间隔（float）或动态策略函数（Callable）
SyncPollingInterval = float | Callable[[float], float]
# 轮询策略函数签名：接收已用时间(秒)，返回下次轮询延迟(秒)
PollingStrategy = Callable[[float], float]

# =============================================================================
# ★ 2. 日志配置
# =============================================================================

# 配置日志
logger = logging.getLogger(__name__)
# logger.setLevel(logging.DEBUG)
logger.setLevel(logging.ERROR)

# 如果没有配置日志处理器，则添加一个
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)


# =============================================================================
# ★ 3. 核心类：OpenSandboxBackend
# =============================================================================
class OpenSandboxBackend(BaseSandbox):
    """
    基于 OpenSandbox 的沙箱后端。

    继承 BaseSandbox 的文件操作方法，仅实现 execute、download_files 和 upload_files。
    在执行命令时自动注入 SANDBOX_PATH 环境变量，覆盖非交互式 shell 的 PATH 限制。
    """

    def __init__(
            self,
            *,
            sandbox: SandboxSync,
            timeout: int = 60 * 60,
            sync_polling_interval: SyncPollingInterval = 0.1) -> None:
        """
        创建一个包装已有 OpenSandbox 沙箱后端实例
          Args：
            sandbox：要包装的现有 OpenSandbox 沙盒实例。
            timeout：调用 `execute()` 且未显式指定 `timeout` 时使用的默认命令超时时间（秒）。
                默认 3600 秒（1 小时）。
            sync_polling_interval：在同步执行路径上，轮询 OpenSandbox 命令完成状态的间隔时间（秒）；
                也可以是一个可调用对象，接收已执行的秒数并返回下一次轮询的延迟时间。
        """

        logger.info(f"正在初始化 OpenSandbox，沙盒 ID: {sandbox.id}")
        self._sandbox = sandbox

        # sandbox.kill() 手动关闭沙箱
        self.default_timeout = timeout

        # 处理轮训策略：若传入的事数字则包装为常量函数，否则直接使用
        if callable(sync_polling_interval):
            polling_strategy = cast("PollingStrategy", sync_polling_interval)
        else:
            def polling_strategy(_elapsed: float) -> float:
                return sync_polling_interval

        self._sync_polling_interval = polling_strategy
        logger.debug(f"OpenSandbox 初始化完成，默认超时时间={timeout}秒")

    @property
    def id(self) -> str:
        """返回 OpenSandbox 沙盒的 ID。"""
        sandbox_id = self._sandbox.id
        logger.debug(f"获取沙盒 ID: {sandbox_id}")
        return sandbox_id

    # =============================================================================
    # ★ 4. 核心方法实现：execute / upload / download
    # =============================================================================
    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """
        执行 Shell 命令（同步）。

        封装 OpenSandbox SDK 的 run 方法，处理超时和错误转换。
        自动注入 SANDBOX_PATH 环境变量，确保非交互式 shell 能找到 pip/python。

        Args:
            command: 要执行的 shell 命令。
            timeout: 超时时间（秒），默认使用 self.default_timeout。

        Returns:
            ExecuteResponse: 包含 exit_code、output 等字段。
        """
        timeout = timeout or self.default_timeout
        logger.debug(f"执行命令: {command[:100]}...")

        # 执行命令，返回 ExecuteResponse
        try:
            # OpenSandbox SDK 的 run 方法返回结果
            result = self._sandbox.run(command, timeout=timeout)

            return ExecuteResponse(
                exit_code=result.exit_code or 0,
                output=result.output or "",
                error=result.error,
            )
        except Exception as e:
            logger.error(f"命令执行失败: {e}")
            return ExecuteResponse(
                exit_code=-1,
                output="",
                error=str(e),
            )

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """
        执行 Shell 命令（异步）。

        通过 asyncio.to_thread 将同步调用转为异步，避免阻塞事件循环。

        Args:
            command: 要执行的 shell 命令。
            timeout: 超时时间（秒）。

        Returns:
            ExecuteResponse: 执行结果。
        """
        return await asyncio.to_thread(self.execute, command, timeout=timeout)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """
        上传文件到沙箱（同步）。

        将 (沙箱路径, 文件内容字节) 元组列表批量上传到沙箱。

        Args:
            files: [(sandbox_path, content), ...] 格式的列表。

        Returns:
            list[FileUploadResponse]: 每个文件的 upload 结果。
        """
        results = []
        for path, content in files:
            try:
                # OpenSandbox SDK 的 write 方法
                write_entry = WriteEntry(path=path, content=content)
                self._sandbox.write(write_entry)
                results.append(FileUploadResponse(
                    path=path,
                    success=True,
                    error=None,
                ))
            except Exception as e:
                logger.warning(f"文件上传失败 {path}: {e}")
                results.append(FileUploadResponse(
                    path=path,
                    success=False,
                    error=str(e),
                ))
        return results

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传文件到沙箱（异步）。"""
        return await asyncio.to_thread(self.upload_files, files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """
        从沙箱下载文件（同步）。

        批量下载沙箱中的文件到本地。

        Args:
            paths: 要下载的沙箱文件路径列表。

        Returns:
            list[FileDownloadResponse]: 每个文件的 download 结果。
        """
        results = []
        for path in paths:
            try:
                # OpenSandbox SDK 的 read 方法
                content = self._sandbox.read(path)
                results.append(FileDownloadResponse(
                    path=path,
                    content=content,
                    error=None,
                ))
            except Exception as e:
                logger.warning(f"文件下载失败 {path}: {e}")
                results.append(FileDownloadResponse(
                    path=path,
                    content=None,
                    error=str(e),
                ))
        return results

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从沙箱下载文件（异步）。"""
        return await asyncio.to_thread(self.download_files, paths)
