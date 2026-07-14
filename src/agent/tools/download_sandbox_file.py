"""
沙箱文件下载工具

将沙箱中的指定文件下载到本地 EXAMPLE_DIR/download/ 目录。
"""
from pathlib import Path

from langchain_core.tools import tool


# =============================================================================
# ★ 1. 工具工厂 —— 创建 download_sandbox_file 工具
# =============================================================================
def create_download_tool(sandbox_backend, download_dir: Path):
    """
    创建 download_sandbox_file 工具工厂函数。

    Args:
        sandbox_backend: OpenSandboxBackend 实例，用于从沙箱读取文件。
        download_dir: 本地下载目标目录（如 EXAMPLE_DIR/download/）。

    Returns:
        download_sandbox_file 工具函数。
    """

    # 确保本地目录存在
    download_dir.mkdir(parents=True, exist_ok=True)

    # =============================================================================
    # ★ 2. 工具定义 —— download_sandbox_file
    # =============================================================================
    @tool
    def download_sandbox_file(sandbox_path: str, local_filename: str = "") -> str:
        """
        将沙箱中的指定文件下载到本地 download/ 目录。

        适用场景：
        - 下载生成的分析报告（/analysis/report_*.md）
        - 下载可视化图表（/data/chart_*.png）
        - 下载分析结果数据（/data/analysis_result.json）
        - 下载沙箱中的任意文件到本地

        Args:
            sandbox_path: 沙箱中的绝对文件路径（如 "/analysis/report_20260513.md"）
            local_filename: 本地保存的文件名（可选，为空则使用沙箱中的原始文件名）

        Returns:
            下载确认信息，含本地文件路径。
        """
        # 1. 校验路径
        if not sandbox_path or not sandbox_path.startswith("/"):
            return f"错误：sandbox_path 必须是绝对路径（以 / 开头），收到: {sandbox_path}"

        # 2. 从沙箱下载文件
        try:
            results = sandbox_backend.download_files([sandbox_path])
        except Exception as e:
            return f"错误：从沙箱读取文件失败: {e}"

        if not results:
            return f"错误：下载返回空结果"

        dl = results[0]
        if dl.error:
            return f"错误：文件不存在或无法读取（{dl.error}），路径: {sandbox_path}"

        content = dl.content
        if content is None:
            return f"错误：文件内容为空，路径: {sandbox_path}"

        # 3. 确定本地文件名
        if not local_filename:
            local_filename = Path(sandbox_path).name
        # 安全检查：防止路径穿越
        local_filename = Path(local_filename).name

        local_path = download_dir / local_filename

        # 4. 写入本地文件
        try:
            if isinstance(content, str):
                content_bytes = content.encode("utf-8")
            elif isinstance(content, bytes):
                content_bytes = content
            else:
                content_bytes = str(content).encode("utf-8")

            local_path.write_bytes(content_bytes)
        except Exception as e:
            return f"错误：写入本地文件失败: {e}"

        # 5. 返回结果
        file_size = len(content_bytes)
        size_str = (
            f"{file_size} B" if file_size < 1024
            else f"{file_size / 1024:.1f} KB" if file_size < 1024 * 1024
            else f"{file_size / (1024 * 1024):.1f} MB"
        )

        return (
            f"✅ 文件已下载到本地\n"
            f"沙箱路径: {sandbox_path}\n"
            f"本地路径: {local_path}\n"
            f"文件大小: {size_str}"
        )

    download_sandbox_file.name = "download_sandbox_file"
    return download_sandbox_file
