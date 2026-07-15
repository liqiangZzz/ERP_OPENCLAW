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
        self._default_timeout = timeout

        # 处理轮训策略：若传入的是数字则包装为常量函数，否则直接使用
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

    # 沙箱中非交互式 shell 不会加载 /etc/profile，需要手动注入环境变量
    # 包含 skills-venv、Python、Go、Node.js、Java 等运行时路径
    SANDBOX_PATH = (
        "/opt/skills-venv/bin:"
        "/opt/python/versions/cpython-3.11.14-linux-x86_64-gnu/bin:"
        "/opt/go/1.25.5/bin:"
        "/opt/node/v22.2.0/bin:"
        "/usr/lib/jvm/java-21-openjdk-amd64/bin:"
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    )

    # =============================================================================
    # ★ 4. 核心方法实现：execute / upload / download
    # =============================================================================
    def execute(
            self,
            command: str,
            *,
            timeout: int | None = None,
    ) -> ExecuteResponse:
        """在沙盒内部执行一条 Shell 命令。

        执行前会自动注入 PATH 环境变量，确保命令能找到正确的可执行文件。

        Args：
            command：要执行的 Shell 命令字符串。
            timeout：等待命令完成的最大时间（秒）。
                如果为 None，则使用后端默认的超时时间。

        Returns:
            ExecuteResponse 对象，包含标准输出、错误输出、退出码等信息。
        """
        effective_timeout = timeout if timeout is not None else self._default_timeout
        # 非交互式 shell 不会 source /etc/profile，需要注入 PATH 确保 pip/python 可用
        wrapped = f'export PATH="{self.SANDBOX_PATH}:$PATH" && {command}'
        logger.debug(f"准备执行命令：{command[:100]}...（超时时间={effective_timeout}秒）")
        return self._execute_command(wrapped, timeout=effective_timeout)

    def _execute_command(
            self,
            command: str,
            *,
            timeout: int,
    ) -> ExecuteResponse:
        """使用 OpenSandbox 的 API 执行命令。

        处理命令执行的完整流程：调用 OpenSandbox SDK、解析输出、处理异常。
        超时情况下返回 exit_code=124 的标准退出码。

        Args:
            command: 要执行的命令（已包含 PATH 注入）。
            timeout: 超时时间（秒）。

        Returns:
            ExecuteResponse 对象。
        """
        try:
            logger.debug(f"通过 OpenSandbox API 执行命令：{command}")
            result = self._sandbox.commands.run(command)
            logger.debug(f"命令执行完成，退出码：{result.exit_code}")

            # 提取标准输出与标准错误
            stdout = ""
            stderr = ""

            if result.logs.stdout:
                stdout = "\n".join([log.text for log in result.logs.stdout])
                logger.debug(f"命令标准输出长度：{len(stdout)} 字符")

            if result.logs.stderr:
                stderr = "\n".join([log.text for log in result.logs.stderr])
                logger.debug(f"命令标准错误长度：{len(stderr)} 字符")

            # 合并输出：stdout 为主体，stderr 附加在 <stderr> 标签中
            output = stdout
            if stderr and stderr.strip():
                output += f"\n<stderr>{stderr.strip()}</stderr>"

            logger.info(f"命令执行成功，退出码：{result.exit_code or 0}")
            return ExecuteResponse(
                output=output,
                exit_code=result.exit_code or 0,
                truncated=False,
            )

        except Exception as e:
            error_msg = str(e)
            logger.error(f"执行命令时发生错误：{error_msg}", exc_info=True)

            if "timeout" in error_msg.lower():
                logger.warning(f"命令在 {timeout} 秒后超时")
                return ExecuteResponse(
                    output=f"命令在 {timeout} 秒后超时",
                    exit_code=124,  # Linux 标准超时退出码
                    truncated=False,
                )

            return ExecuteResponse(
                output=f"执行命令时出错：{error_msg}",
                exit_code=1,
                truncated=False,
            )

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从沙箱下载指定文件。

        逐个读取文件内容，若文件不存在或路径不合法则记录错误而不中断。

        Args:
            paths: 沙箱中的绝对文件路径列表。

        Returns:
            与 paths 顺序对应的响应列表，包含文件内容或错误信息。
        """
        responses: list[FileDownloadResponse] = []

        for path in paths:
            if not path.startswith("/"):
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="invalid_path")
                )
                continue
            try:
                content = self._sandbox.files.read_file(path)
                # 统一转为 bytes
                content_bytes = content.encode("utf-8") if isinstance(content, str) else content
                responses.append(
                    FileDownloadResponse(path=path, content=content_bytes, error=None)
                )
            except Exception:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found")
                )

        return responses

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传文件到沙箱。

        批量构建写入条目后一次性提交。若提交失败，将所有已准备但未写入的条目标记为错误。

        Args:
            files: 每个元素为 (绝对路径, 文件内容字节) 的元组列表。

        Returns:
            与 files 顺序对应的响应列表，包含操作错误信息（成功时为 None）。
        """
        responses: list[FileUploadResponse] = []
        upload_entries: list[WriteEntry] = []

        for path, content in files:
            if not path.startswith("/"):
                responses.append(FileUploadResponse(path=path, error="invalid_path"))
                continue
            try:
                # 将 bytes 转换为字符串用于写入
                if isinstance(content, bytes):
                    try:
                        content_str = content.decode("utf-8")
                    except UnicodeDecodeError:
                        # UTF-8 解码失败时降级使用 latin-1（兼容任意字节序列）
                        content_str = content.decode("latin-1")
                else:
                    content_str = str(content)
                upload_entries.append(WriteEntry(path=path, data=content_str, mode=0o644))
                responses.append(FileUploadResponse(path=path, error=None))
            except Exception as e:
                responses.append(FileUploadResponse(path=path, error=str(e)))

        if upload_entries:
            try:
                self._sandbox.files.write_files(upload_entries)
            except Exception as e:
                # 若写操作失败，将所有成功准备但未真正上传的条目标记为错误
                for resp in responses:
                    if resp.error is None:
                        resp.error = f"upload_failed: {e}"

        return responses
