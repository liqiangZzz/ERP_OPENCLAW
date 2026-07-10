# =============================================================================
# ★ 模块：SandboxBackendProxy — 稳定句柄，内部 backend 可热替换
# =============================================================================
"""
SandboxBackendProxy — 稳定句柄，内部 backend 可热替换。

显式代理 SandboxBackendProtocol 全部 18 个方法 + id property，
确保 Python MRO 不会找到协议层的 NotImplementedError 默认实现。

使用场景:
    当沙箱后端需要热替换（如重建、升级）时，持有 SandboxBackendProxy
    的调用方无需感知变化，proxy 透明地将请求转发到新的 backend。
"""
from deepagents.backends.protocol import SandboxBackendProtocol, ExecuteResponse, WriteResult, EditResult, \
    FileUploadResponse, FileDownloadResponse


# =============================================================================
# ★ 1. 核心类：SandboxBackendProxy
# =============================================================================
class SandboxBackendProxy(SandboxBackendProtocol):
    """
    代理所有 SandboxBackendProtocol 方法到内部 backend，支持热替换。

    作为稳定引用持有，即使内部 backend 被 replace_backend 替换，
    外部调用方仍然使用同一个 proxy 对象。
    """

    def __init__(self, backend: SandboxBackendProtocol) -> None:
        """
        初始化带来，绑定内部 backend

        Args：
            backend: 实际执行的沙箱后端实例。
        """
        self._backend = backend

    @property
    def id(self) -> str:
        """返回当前内部 backend 的沙箱id"""
        return self._backend.id

    def replace_backend(self, new_backend: SandboxBackendProtocol) -> None:
        """
        热替换内部 backend，外部 proxy 引用保持不变
        Args:
           new_backend: 新的沙箱后端实例。
        """
        self._backend = new_backend

    # ---- 同步方法：代理到内部 backend 的同步接口 ----

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """执行 Shell 命令（同步）。"""
        return self._backend.execute(command, timeout=timeout)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """读取文件内容（同步）。"""
        return self._backend.read(file_path, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        """写入文件内容（同步）。"""
        return self._backend.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """编辑文件中的文本（同步）。"""
        return self._backend.edit(file_path, old_string, new_string, replace_all)

    def ls_info(self, path: str) -> list:
        """列出目录信息（同步）。"""
        return self._backend.ls_info(path)

    def glob_info(self, pattern: str, path: str = "/") -> list:
        """按 glob 模式搜索文件（同步）。"""
        return self._backend.glob_info(pattern, path)

    def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list | str:
        """正则搜索文件内容（同步）。"""
        return self._backend.grep_raw(pattern, path, glob)

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传文件到沙箱（同步）。"""
        return self._backend.upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从沙箱下载文件（同步）。"""
        return self._backend.download_files(paths)

    # ---- 异步方法：代理到内部 backend 的异步接口 ----

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """执行 Shell 命令（异步）。"""
        return await self._backend.aexecute(command, timeout=timeout)

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
        """读取文件内容（异步）。"""
        return await self._backend.aread(file_path, offset, limit)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """写入文件内容（异步）。"""
        return await self._backend.awrite(file_path, content)

    async def aedit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """编辑文件中的文本（异步）。"""
        return await self._backend.aedit(file_path, old_string, new_string, replace_all)

    async def als_info(self, path: str) -> list:
        """列出目录信息（异步）。"""
        return await self._backend.als_info(path)

    async def aglob_info(self, pattern: str, path: str = "/") -> list:
        """按 glob 模式搜索文件（异步）。"""
        return await self._backend.aglob_info(pattern, path)

    async def agrep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list | str:
        """正则搜索文件内容（异步）。"""
        return await self._backend.agrep_raw(pattern, path, glob)

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """上传文件到沙箱（异步）。"""
        return await self._backend.aupload_files(files)

    async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """从沙箱下载文件（异步）。"""
        return await self._backend.adownload_files(paths)

    # ---- fallback：未来新增的方法通过 __getattr__ 动态转发 ----

    def __getattr__(self, name: str):
        """
        动态转发未显示定义的方法到内部 backend

        当 SandboxBackendProtocol 协议新增方法时，proxy 无需逐个添加，
        自动通过次方法转发，。但以下划线开头的方法（如私有属性）不转发。

        Args:
            name: 属性名/方法名
       Returns:
            内部 backend 对应属性的值。

        Raises:
            AttributeError: 属性名以下划线开头时抛出。
        """
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._backend, name)
