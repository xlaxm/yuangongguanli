import os

# 导入应用模块前提供安全的占位配置，测试不连接真实 MySQL。
os.environ.setdefault(
    "DATABASE_URL",
    "mysql+pymysql://test:test@127.0.0.1:3306/test?charset=utf8mb4",
)
