"""可信身份、业务数据访问和专业 Agent 使用的 Tool。"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from langchain.tools import ToolRuntime, tool
from langgraph.types import interrupt
from sqlalchemy import select

from database import SessionLocal
from knowledge_base import search_knowledge
from models import Department, Device, DeviceRequest, User, utc_now

PROJECT_DIR = Path(__file__).resolve().parent
# 业务 Tool 统一通过这些路径访问项目自己的数据副本。
DATA_DIR = PROJECT_DIR / "data"
USERS_PATH = DATA_DIR / "day05_users.json"
DEPARTMENTS_PATH = DATA_DIR / "day05_departments.json"
DEVICES_PATH = DATA_DIR / "day05_devices.json"
REQUESTS_PATH = DATA_DIR / "day05_device_requests.json"
_WRITE_LOCK = threading.RLock()  # 防止同一进程的两次写入相互覆盖。

# 权限名由程序定义，不从用户聊天内容推断。
ROLE_PERMISSIONS = {
    "employee": {
        "read_public_knowledge",
        "read_public_directory",
        "read_device_catalog",
        "create_device_request",
        "read_own_device_requests",
    },
    "hr": {
        "read_public_knowledge",
        "read_hr_knowledge",
        "read_public_directory",
        "read_device_catalog",
        "read_all_device_requests",
        "review_device_requests",
    },
}


@dataclass(frozen=True)
class RunContext:
    """由教学登录创建、由应用注入的可信运行上下文。"""

    user_id: str
    username: str
    display_name: str
    role: str
    department_id: str


def _result(
    status: str,
    *,
    data: Any = None,
    code: str | None = None,
    message: str | None = None,
) -> str:
    """让所有业务 Tool 使用一致的 JSON 返回结构。"""

    return json.dumps(
        {
            "status": status,
            "data": data,
            "error": None if code is None else {"code": code, "message": message},
        },
        ensure_ascii=False,
    )


def _has_permission(context: RunContext, permission: str) -> bool:
    """检查当前可信角色是否拥有指定权限。"""

    return permission in ROLE_PERMISSIONS.get(context.role, set())


def _permission_denied(permission: str) -> str:
    """生成统一的无权限 Tool 返回结果。"""

    return _result(
        "error",
        code="permission_denied",
        message=f"当前登录角色没有权限：{permission}",
    )


def login_as(username: str) -> User:
    """查询已登记且可用的教学账号；不接受聊天中的身份声明。"""

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username.strip()))
        print("login_as 查询用户", user)
    if user is None:
        raise ValueError("未找到可登录的教学账号")
    if (
        not user.login_enabled
        or user.account_status != "active"
        or user.role not in {"employee", "hr"}
    ):
        raise PermissionError("该教学账号当前不可登录")
    return user


def _confirmation_approved(response: Any) -> bool:
    """将 interrupt() 恢复时收到的值统一解析为布尔值。"""

    if isinstance(response, bool):
        return response
    if isinstance(response, dict):
        return bool(response.get("approved"))
    return False


def _device_data(device: Device) -> dict[str, Any]:
    """把 Device ORM 对象转换成可序列化字典。"""

    return {
        "device_id": device.device_id,
        "name": device.name,
        "category": device.category,
        "description": device.description,
        "eligible_roles": device.eligible_roles,
        "eligible_department_ids": device.eligible_department_ids,
        "max_quantity_per_request": device.max_quantity_per_request,
        "request_requirements": device.request_requirements,
        "is_requestable": device.is_requestable,
    }


def _request_data(
    record: DeviceRequest,
    device_name: str | None = None,
) -> dict[str, Any]:
    """把申请转换成字典，并把 UTC 时间标记为 Z。"""

    data = {
        "request_id": record.request_id,
        "applicant_user_id": record.applicant_user_id,
        "device_id": record.device_id,
        "quantity": record.quantity,
        "reason": record.reason,
        "status": record.status,
        "requested_at": record.requested_at.isoformat() + "Z",
        "reviewer_user_id": record.reviewer_user_id,
        "reviewed_at": (record.reviewed_at.isoformat() + "Z" if record.reviewed_at else None),
        "review_comment": record.review_comment,
    }
    if device_name is not None:
        data["device_name"] = device_name
    return data


# ============= 待改造 ==========
def _find_device(
    device_name: str,
) -> tuple[dict[str, Any] | None, str | None]:
    """先精确匹配设备，再尝试部分匹配并处理歧义。"""

    keyword = device_name.strip().lower()

    with SessionLocal() as db:
        devices = list(db.scalars(select(Device)).all())

        exact = [item for item in devices if keyword in {item.device_id.lower(), item.name.lower()}]
        if len(exact) == 1:
            return _device_data(exact[0]), None

        partial = [
            item
            for item in devices
            if keyword in item.device_id.lower() or keyword in item.name.lower()
        ]
        if len(partial) == 1:
            return _device_data(partial[0]), None
        if not partial:
            return None, "没有找到匹配的设备"

        names = "、".join(item.name for item in partial)
        return None, f"匹配到多个设备，请明确选择：{names}"


@tool
def search_company_knowledge(
    query: str,
    runtime: ToolRuntime[RunContext],
) -> str:
    """查询公司介绍、公共制度或 HR 制度；根据登录角色过滤知识库。"""

    # Runtime Context 由应用在调用 Agent 时注入，模型不能修改它。
    context = runtime.context
    if not _has_permission(context, "read_public_knowledge"):
        return _permission_denied("read_public_knowledge")
    try:
        print("给向量搜索工具传的问题：=============", query)
        documents = search_knowledge(query=query, role=context.role, k=4)
    except Exception as exc:
        return _result("error", code="knowledge_search_failed", message=str(exc))

    # 把来源一起返回，便于 Agent 生成可追溯的回答。
    evidence = [
        {
            "chunk_id": document.metadata["chunk_id"],
            "title": document.metadata["title"],
            "source": document.metadata["source"],
            "content": document.page_content,
        }
        for document in documents
    ]
    return _result("ok", data={"query": query, "evidence": evidence})


@tool
def find_department(
    topic: str,
    runtime: ToolRuntime[RunContext],
) -> str:
    """按部门名称或办事主题查询公开部门信息。"""

    if not _has_permission(runtime.context, "read_public_directory"):
        return _permission_denied("read_public_directory")
    keyword = topic.strip().lower()
    with SessionLocal() as db:
        matches = []
        for department in db.scalars(select(Department)).all():
            searchable = " ".join([department.name, *department.responsibilities]).lower()
            if keyword in searchable or any(
                responsibility.lower() in keyword for responsibility in department.responsibilities
            ):
                matches.append(
                    {
                        "department_id": department.department_id,
                        "name": department.name,
                        "responsibilities": department.responsibilities,
                        "office_location": department.office_location,
                        "public_contact": department.public_contact,
                    }
                )

    return _result("ok", data=matches)


@tool
def list_department(
    runtime: ToolRuntime[RunContext],
) -> str:
    """查询公司的部门信息，返回所有部门列表"""

    if not _has_permission(runtime.context, "read_public_directory"):
        return _permission_denied("read_public_directory")
    with SessionLocal() as db:
        departments = db.scalars(select(Department)).all()

    data = [
        {
            "department_id": item.department_id,
            "name": item.name,
            "responsibilities": item.responsibilities,
            "office_location": item.office_location,
            "public_contact": item.public_contact,
        }
        for item in departments
    ]
    return _result("ok", data=data)


@tool
def find_public_employee(
    keyword: str,
    runtime: ToolRuntime[RunContext],
) -> str:
    """按姓名、岗位或专长查询公开员工目录，不返回非公开人员。"""

    if not _has_permission(runtime.context, "read_public_directory"):
        return _permission_denied("read_public_directory")
    query = keyword.strip().lower()
    statement = (
        select(User, Department.name)
        .join(Department, User.department_id == Department.department_id)
        .where(User.is_public.is_(True))
    )

    with SessionLocal() as db:
        matches = []
        for user, department_name in db.execute(statement).all():
            searchable = " ".join(
                [
                    user.display_name,
                    user.job_title or "",
                    department_name,
                    *(user.expertise or []),
                ]
            ).lower()
            if query in searchable:
                matches.append(
                    {
                        "user_id": user.user_id,
                        "display_name": user.display_name,
                        "department": department_name,
                        "job_title": user.job_title,
                        "expertise": user.expertise,
                        "office_location": user.office_location,
                        "office_contact": user.office_contact,
                    }
                )

    return _result("ok", data=matches)


@tool
def list_requestable_devices(
    runtime: ToolRuntime[RunContext],
    keyword: str = "",
) -> str:
    """查询当前登录用户可以申请或查看的设备目录，可按名称和类别筛选。"""

    context = runtime.context
    if not _has_permission(context, "read_device_catalog"):
        return _permission_denied("read_device_catalog")
    query = keyword.strip().lower()
    with SessionLocal() as db:
        devices = db.scalars(select(Device).where(Device.is_requestable.is_(True))).all()

        matches = []
        for device in devices:
            if context.role not in device.eligible_roles:
                continue
            if (
                device.eligible_department_ids
                and context.department_id not in device.eligible_department_ids
            ):
                continue

            searchable = f"{device.device_id} {device.name} {device.category}".lower()
            if not query or query in searchable:
                matches.append(_device_data(device))

    return _result("ok", data=matches)


@tool
def create_device_request(
    device_name: str,
    quantity: int,
    reason: str,
    runtime: ToolRuntime[RunContext],
) -> str:
    """为当前员工创建设备申请；确认后才开启写事务。"""

    context = runtime.context
    if not _has_permission(context, "create_device_request"):
        return _permission_denied("create_device_request")

    device, error = _find_device(device_name)
    if error:
        return _result("error", code="invalid_device", message=error)
    assert device is not None

    if not device["is_requestable"]:
        return _result(
            "error",
            code="device_not_requestable",
            message="该设备不开放申请",
        )
    if context.role not in device["eligible_roles"]:
        return _result(
            "error",
            code="role_not_eligible",
            message="当前角色不符合申请条件",
        )

    allowed_departments = device["eligible_department_ids"]
    if allowed_departments and context.department_id not in allowed_departments:
        return _result(
            "error",
            code="department_not_eligible",
            message="当前部门不符合申请条件",
        )

    if quantity < 1 or quantity > device["max_quantity_per_request"]:
        return _result(
            "error",
            code="invalid_quantity",
            message=(f"申请数量必须在 1 到 {device['max_quantity_per_request']} 之间"),
        )

    normalized_reason = reason.strip()
    if len(normalized_reason) < 8:
        return _result(
            "error",
            code="reason_too_short",
            message="请补充更具体的工作用途",
        )

    # 此时查询 Session 已关闭，等待用户时不会占用数据库连接。
    response = interrupt(
        {
            "action": "create_device_request",
            "message": "是否确认提交这条设备申请？",
            "applicant": context.display_name,
            "device_id": device["device_id"],
            "device_name": device["name"],
            "quantity": quantity,
            "reason": normalized_reason,
        }
    )
    if not _confirmation_approved(response):
        return _result(
            "cancelled",
            data={"message": "用户已取消设备申请"},
        )

    # UUID 避免并发请求通过“最大编号加一”生成重复主键。
    request_id = f"REQ-{uuid4().hex[:12].upper()}"
    with SessionLocal.begin() as db:
        record = DeviceRequest(
            request_id=request_id,
            applicant_user_id=context.user_id,
            device_id=device["device_id"],
            quantity=quantity,
            reason=normalized_reason,
            status="pending",
            requested_at=utc_now(),
        )
        db.add(record)
        # flush 会执行 INSERT 和约束检查，但仍然可以回滚。
        db.flush()
        data = _request_data(record, device["name"])

    return _result("ok", data=data)


@tool
def query_device_requests(
    runtime: ToolRuntime[RunContext],
    status: str = "all",
) -> str:
    """员工只能查询本人申请；HR 可以查询全部申请或按状态筛选。"""
    context = runtime.context
    normalized_status = status.strip().lower()
    if normalized_status not in {
        "",
        "all",
        "pending",
        "approved",
        "rejected",
    }:
        return _result(
            "error",
            code="invalid_status",
            message="不支持的申请状态",
        )

    statement = select(DeviceRequest, Device.name).join(
        Device,
        DeviceRequest.device_id == Device.device_id,
    )

    if context.role == "employee":
        if not _has_permission(context, "read_own_device_requests"):
            return _permission_denied("read_own_device_requests")
        statement = statement.where(DeviceRequest.applicant_user_id == context.user_id)
    elif context.role == "hr":
        if not _has_permission(context, "read_all_device_requests"):
            return _permission_denied("read_all_device_requests")
    else:
        return _permission_denied("read_device_requests")

    if normalized_status not in {"", "all"}:
        statement = statement.where(DeviceRequest.status == normalized_status)

    with SessionLocal() as db:
        data = [
            _request_data(record, device_name)
            for record, device_name in db.execute(statement).all()
        ]

    return _result("ok", data=data)


@tool
def approve_device_request(
    request_id: str,
    decision: Literal["approved", "rejected"],
    comment: str,
    runtime: ToolRuntime[RunContext],
) -> str:
    """由当前 HR 审批申请；恢复后锁定记录并重新检查状态。"""

    context = runtime.context
    if not _has_permission(context, "review_device_requests"):
        return _permission_denied("review_device_requests")

    normalized_comment = comment.strip()
    if not normalized_comment:
        return _result(
            "error",
            code="comment_required",
            message="审批意见不能为空",
        )

    # 暂停前先确认申请存在且仍待审批。
    with SessionLocal() as db:
        current = db.get(DeviceRequest, request_id)
        if current is None:
            return _result(
                "error",
                code="request_not_found",
                message="没有找到该申请",
            )
        if current.status != "pending":
            return _result(
                "error",
                code="invalid_request_state",
                message="只能审批 pending 申请",
            )

    response = interrupt(
        {
            "action": "review_device_request",
            "message": "是否确认提交这次审批？",
            "request_id": request_id,
            "decision": decision,
            "comment": normalized_comment,
            "reviewer": context.display_name,
        }
    )
    if not _confirmation_approved(response):
        return _result(
            "cancelled",
            data={"message": "用户已取消审批"},
        )

    with SessionLocal.begin() as db:
        # 锁定当前申请，防止两个审批请求同时修改同一行。
        current = db.scalar(
            select(DeviceRequest).where(DeviceRequest.request_id == request_id).with_for_update()
        )
        if current is None:
            return _result(
                "error",
                code="request_not_found",
                message="没有找到该申请",
            )
        # Interrupt 可能暂停很久，恢复后必须重新检查状态。
        if current.status != "pending":
            return _result(
                "error",
                code="invalid_request_state",
                message="申请已被其他请求处理",
            )

        current.status = decision
        current.reviewer_user_id = context.user_id
        current.reviewed_at = utc_now()
        current.review_comment = normalized_comment
        db.flush()
        data = _request_data(current)

    return _result("ok", data=data)
