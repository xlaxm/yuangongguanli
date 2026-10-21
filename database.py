import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env", override=False)

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    raise RuntimeError("请在 .env 中配置 DATABASE_URL")


# Engine 管理数据库方言和连接池；复用连接前先检查连接是否可用。
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

# SessionLocal 是创建 Session 的工厂，不是一个全局 Session。
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)
