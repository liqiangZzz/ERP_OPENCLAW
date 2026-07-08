




# =============================================================================
# ★ 1. MongoDB 配置 —— 用于存储 Agent 的短期记忆（checkpoint）
# =============================================================================

# MongoDB 连接 URI，格式: mongodb://用户名:密码@主机地址:端口/?authSource=认证数据库
MONGODB_URI = "mongodb://root:root@3localhost:27017/?authSource=admin"
# MongoDB 数据库名称
MONGODB_DB_NAME = "langchain_db"
# MongoDB 集合名称，用于存储 checkpoint 数据
MONGODB_CHECKPOINT_COLLECTION = "checkpoints"






# =============================================================================
# ★ 3. 服务配置
# ============================================================

# API 服务标题
API_TITLE = "DeepAgent Chat API"
# API 版本
API_VERSION = "1.0.0"
# API 描述
API_DESCRIPTION = "基于 DeepAgent 的 AI 对话系统 API"
