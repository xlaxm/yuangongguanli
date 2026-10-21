"""密码验证与 JWT 签发、解析。"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
from dotenv import load_dotenv
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import User

PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env", override=False)

password_hash = PasswordHash.recommended()
# 用户名不存在时也跑一次验证，缩小"用户不存在"和"密码错误"的响应时间差
DUMMY_HASH = password_hash.hash("day06-dummy-password")


# 验证明文密码是否匹配数据库摘要
def verify_password(plain_password: str, stored_hash: str) -> bool:
    """验证明文密码是否匹配数据库摘要"""

    return password_hash.verify(plain_password, stored_hash)


# 验证帐号状态和密码， 失败时统一返回 None，不告诉调用者具体错在哪。
def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """验证帐号状态和密码， 失败时统一返回 None，不告诉调用者具体错在哪。"""
    user = db.scalar(select(User).where(User.username == username.strip()))
    if user is None or user.password_hash is None:
        verify_password(password, DUMMY_HASH)  # 缩小时间差
        return None

    password_matches = verify_password(password, user.password_hash)
    if (
        not password_matches
        or not user.login_enabled
        or user.account_status != "active"
        or user.role not in {"employee", "hr"}
    ):
        return None
    return user


# 读取并检查 JWT 配置
def _jwt_settings() -> tuple[str, str, int]:
    """读取并检查 JWT 配置"""
    secret = (os.getenv("JWT_SECRET_KEY") or "").strip()
    if not secret or secret.startswith("<") or len(secret) < 32:
        raise RuntimeError("JWT_SECRET_KEY 必须设置为至少 32 字节的随机值")
    algorithm = (os.getenv("JWT_ALGORITHM") or "HS256").strip()
    if algorithm != "HS256":
        raise RuntimeError("本课只演示 HS256")
    try:
        expire_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES") or "30")
    except ValueError as exc:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES 必须是整数") from exc
    if expire_minutes <= 0:
        raise RuntimeError("ACCESS_TOKEN_EXPIRE_MINUTES 必须大于 0")
    return secret, algorithm, expire_minutes


# 把稳定用户编号写入 sub，并签发短期 Access Token
def create_access_token(user_id: str) -> str:
    """把稳定用户编号写入 sub，并签发短期 Access Token"""
    secret, algorithm, expire_minutes = _jwt_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


# 验证签名和过期时间，并返回 Token 中的用户编号
def decode_access_token(token: str) -> str:
    """验证签名和过期时间，并返回 Token 中的用户编号"""
    secret, algorithm, _ = _jwt_settings()
    payload = jwt.decode(
        token,
        secret,
        algorithms=[algorithm],  # 固定算法，防止算法混淆攻击
        options={"require": ["sub", "exp"]},
    )
    user_id = payload["sub"]
    if not isinstance(user_id, str) or not user_id:
        raise jwt.InvalidTokenError("JWT sub 必须是非空字符串")
    return user_id
