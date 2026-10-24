# =============================================================================
# ★ 模块：SandboxBackendProxy — 稳定句柄，内部 backend 可热替换
# =============================================================================
"""
SandboxBackendProxy — 稳定句柄，内部 backend 可热替换。

显式代理 deepagents 0.7 BackendProtocol / SandboxBackendProtocol 的全部方法
（execute / read / write / edit / ls / glob / grep / delete / upload_files /
download_files 及各自的 a* 异步变体）+ id property。

为什么必须逐个显式定义，而不是只靠 __getattr__：
    协议基类把 ls / glob / grep / delete 等实现为「raise NotImplementedError」
    的具体方法（不是 abstractmethod）。属性查找会先在 MRO 上命中这些基类默认实现，
    __getattr__ 根本不会被触发 —— 漏写一个方法，运行期就得到一个空的
    NotImplementedError（str(e) == ""），表现为前端「一直没输出」。
    这正是 deepagents 0.6 → 0.7 迁移中 ls_info/glob_info/grep_raw 改名为
    ls/glob/grep（返回值改为 LsResult/GlobResult/GrepResult、新增 delete）后
    踩到的坑。

使用场景:
    当沙箱后端需要热替换（如重建、升级）时，持有 SandboxBackendProxy
    的调用方无需感知变化，proxy 透明地将请求转发到新的 backend。
"""
from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)


# =============================================================================
# ★ 1. 核心类：SandboxBackendProxy
# =============================================================================
class SandboxBackendProxy(SandboxBackendProtocol):
    """
    代理所有 BackendProtocol 方法到内部 backend，支持热替换。

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

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """读取文件内容（同步）。"""
        return self._backend.read(file_path, offset, limit)

    def write(self, file_path: str, content: str) -> WriteResult:
        """写入文件内容（同步）。"""
        return self._backend.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
        """编辑文件中的文本（同步）。"""
        return self._backend.edit(file_path, old_string, new_string, replace_all)

    def ls(self, path: str) -> LsResult:
        """列出目录条目（同步）。deepagents 0.7 的 SkillsMiddleware 靠它扫描技能目录。"""
        return self._backend.ls(path)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        """按 glob 模式查找文件（同步）。"""
        return self._backend.glob(pattern, path)

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """在文件内容中搜索字面量文本（同步）。"""
        return self._backend.grep(pattern, path, glob, max_count=max_count)

    def delete(self, file_path: str) -> DeleteResult:
        """删除路径（同步）。"""
        return self._backend.delete(file_path)

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

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """读取文件内容（异步）。"""
        return await self._backend.aread(file_path, offset, limit)

    async def awrite(self, file_path: str, content: str) -> WriteResult:
        """写入文件内容（异步）。"""
        return await self._backend.awrite(file_path, content)

    async def aedit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """编辑文件中的文本（异步）。"""
        return await self._backend.aedit(file_path, old_string, new_string, replace_all)

    async def als(self, path: str) -> LsResult:
        """列出目录条目（异步）。"""
        return await self._backend.als(path)

    async def aglob(self, pattern: str, path: str | None = None) -> GlobResult:
        """按 glob 模式查找文件（异步）。"""
        return await self._backend.aglob(pattern, path)

    async def agrep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
        *,
        max_count: int | None = None,
    ) -> GrepResult:
        """在文件内容中搜索字面量文本（异步）。"""
        return await self._backend.agrep(pattern, path, glob, max_count=max_count)

    async def adelete(self, file_path: str) -> DeleteResult:
        """删除路径（异步）。"""
        return await self._backend.adelete(file_path)

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

        注意：本方法只对「基类里没有同名实现」的成员生效。基类已给出
        NotImplementedError 默认实现的方法（ls/glob/grep/delete 等）必须在上面
        显式写出，否则永远走不到这里。

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
