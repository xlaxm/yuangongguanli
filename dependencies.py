"""FastAPI 的当前用户与 Thread 归属依赖。"""

from dataclasses import dataclass
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select

from auth import decode_access_token
from database import SessionLocal
from models import User

# 这里只负责从 Authorization 请求头提取 Bearer Token。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/token")


@dataclass(frozen=True)
class CurrentUser:
    """从 Token 和数据库恢复出的请求级可信身份。"""

    user_id: str
    username: str
    display_name: str
    role: str
    department_id: str


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无法验证登录凭证",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> CurrentUser:
    """验证 JWT，并从数据库读取用户的最新状态和权限属性。"""

    print(token)

    # 第一步：验证请求头中的 Token 是否可信且未过期。
    try:
        user_id = decode_access_token(token)
    except jwt.InvalidTokenError as exc:
        raise _credentials_exception() from exc

    # 第二步：每次请求重新读取账号状态，避免继续信任已禁用账号。
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.user_id == user_id))
        if (
            user is None
            or not user.login_enabled
            or user.account_status != "active"
            or user.username is None
            or user.role not in {"employee", "hr"}
        ):
            raise _credentials_exception()

        # 只有服务端验证后的字段才能进入后续 Agent 运行上下文。
        return CurrentUser(
            user_id=user.user_id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            department_id=user.department_id,
        )
