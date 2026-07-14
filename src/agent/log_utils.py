# =============================================================================
# ★ 日志工具模块 —— 基于 loguru 封装，提供统一格式的控制台输出
# =============================================================================
"""
日志工具模块。

基于 loguru 封装的日志器，提供统一格式的控制台输出。
模块加载时自动创建日志目录并初始化全局 log 对象。
"""
import os
import sys

from loguru import logger

# =============================================================================
# ★ 1. 日志目录初始化 —— 模块加载时自动创建
# =============================================================================

# 获得当前项目的绝对路径
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
log_dir = os.path.join(root_dir, "logs")  # 存放项目日志目录的绝对路径

if not os.path.exists(log_dir):  # 如果日志目录不存在，则创建
    os.mkdir(log_dir)

# LOG_FILE = "translation.log"  # 存储日志的文件

# Trace < Debug < Info < Success < Warning < Error < Critical

# =============================================================================
# ★ 2. MyLogger 类 —— 基于 loguru 的日志器封装
# =============================================================================

class MyLogger:
    """基于 loguru 的日志器封装类。

    提供统一格式的控制台日志输出，包含时间、进程名、线程名、
    模块名、函数名、行号和日志等级等信息。

    用法:
        log = MyLogger().get_logger()
        log.info("这是一条日志")
    """
    def __init__(self):
        # log_file_path = os.path.join(log_dir, LOG_FILE)
        self.logger = logger  # 写日志的对象
        # 清空所有设置
        self.logger.remove()
        # 从环境变量读取日志级别，默认 INFO，生产环境可设为 WARNING
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        # 添加控制台输出的格式,sys.stdout为输出到屏幕;关于这些配置还需要自定义请移步官网查��相关参数说明
        self.logger.add(sys.stdout, level=log_level,
                        format="<green>{time:YYYYMMDD HH:mm:ss}</green> | "  # 颜色>时间
                               "{process.name} | "  # 进程名
                               "{thread.name} | "  # 线程名
                               "<cyan>{module}</cyan>.<cyan>{function}</cyan>"  # 模块名.方法名
                               ":<cyan>{line}</cyan> | "  # 行号
                               "<level>{level}</level>: "  # 等级
                               "<level>{message}</level>",  # 日志内容
                        )
        # 输出到文件的格式,注释下面的add',则关闭日志写入
        # self.logger.add(log_file_path, level='DEBUG', encoding='UTF-8',
        #                 format='{time:YYYYMMDD HH:mm:ss} - '  # 时间
        #                        "{process.name} | "  # 进程名
        #                        "{thread.name} | "  # 进程名
        #                        '{module}.{function}:{line} - {level} -{message}',  # 模块名.方法名:行号
        #                 rotation="10 MB",  # 日志文件生成的规则  rotation="1 week"  rotation="1 days"
        #                 retention=20  # 保留日志文件的规则
        #                 )

    def get_logger(self):
        """返回配置好的 loguru logger 实例。"""
        return self.logger


# =============================================================================
# ★ 3. 模块级全局日志实例 —— 其他模块可直接导入使用
# =============================================================================

# 模块级全局日志实例，其他模块可直接导入使用
log = MyLogger().get_logger()

if __name__ == '__main__':
    # log.debug("This is a debug message.")
    # log.info("This is an info message.")
    # log.warning('这是一个警告')
    # log.trace('xxxx')
    print('str.pdf'['str.pdf'.rindex('.'):])
    # @log.catch  # 整个函数自动加上try， catch。自动捕获异常，并且通过日志打印
    def test():
        try:
            print(3/0)
        except ZeroDivisionError as e:
            # log.error(e)
            log.exception(e)  # 以后常用
