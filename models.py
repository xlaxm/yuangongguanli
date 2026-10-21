"""Day05 的业务表和 Agent 会话索引表。"""

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型共同继承的声明式基类。"""


def utc_now() -> datetime:
    """生成适合写入 MySQL DATETIME 的 UTC 无时区时间。"""

    return datetime.now(UTC).replace(tzinfo=None)


class Department(Base):
    __tablename__ = "departments"

    department_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    responsibilities: Mapped[list[str]] = mapped_column(JSON)
    office_location: Mapped[str] = mapped_column(String(200))
    public_contact: Mapped[str] = mapped_column(String(200))


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('employee', 'hr')", name="ck_users_role"),
        CheckConstraint(
            "account_status IN ('active', 'disabled')",
            name="ck_users_status",
        ),
    )
    user_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    # 公共员工目录记录可能不是登录账号，因此账号字段允许为空。
    username: Mapped[str | None] = mapped_column(String(50), unique=True, index=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,  # 公开员工目录的人不登录，允许为空
    )
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    login_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    account_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    department_id: Mapped[str] = mapped_column(ForeignKey("departments.department_id"))
    job_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expertise: Mapped[list[str]] = mapped_column(JSON)
    office_location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    office_contact: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint(
            "max_quantity_per_request >= 0",
            name="ck_devices_max_quantity",
        ),
    )

    device_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    category: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text)
    eligible_roles: Mapped[list[str]] = mapped_column(JSON)
    eligible_department_ids: Mapped[list[str]] = mapped_column(JSON)
    max_quantity_per_request: Mapped[int] = mapped_column(Integer)
    request_requirements: Mapped[str] = mapped_column(Text)
    is_requestable: Mapped[bool] = mapped_column(Boolean, default=True)


class DeviceRequest(Base):
    __tablename__ = "device_requests"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_device_requests_quantity"),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name="ck_device_requests_status",
        ),
    )

    request_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    applicant_user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"))
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"))
    quantity: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    reviewer_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.user_id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class AgentThread(Base):
    __tablename__ = "agent_threads"

    # session_id 与 LangGraph thread_id 使用同一个值。
    thread_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
