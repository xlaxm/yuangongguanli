import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from sqlalchemy import select

from auth import authenticate_user, create_access_token
from database import SessionLocal
from dependencies import CurrentUser, get_current_user
from knowledge_base import INDEX_MANIFEST_PATH
from models import AgentThread, User
from multi_agent import (
    MultiAgentApplication,
    UserSession,
    message_text,
    outer_trace,
)
from schemas import (
    AgentReply,
    ChatRequest,
    ConfirmRequest,
    CurrentUserResponse,
    HistoryMessage,
    LoginRequest,
    ThreadHistoryResponse,
    ThreadResponse,
    TokenResponse,
)

PROJECT_DIR = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_DIR / "static"
STREAM_TIMEOUT_SECONDS = 90
load_dotenv(PROJECT_DIR / ".env", override=False)


# 获取 sqlite 保存路径
def checkpoint_path() -> Path:
    """把 CHECKPOINT_DB 解析为不依赖启动目录的绝对路径。"""

    configured = (os.getenv("CHECKPOINT_DB") or "runtime/checkpoints.sqlite3").strip()
    path = Path(configured)
    if not path.is_absolute():
        path = PROJECT_DIR / path

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# 根据 thread_id 判断当前回话是否有需要用户确认的操作
async def has_pending_confirmation(session: UserSession) -> bool:
    """从持久化 Checkpointer 查询当前 Thread 是否仍有 Interrupt。"""

    snapshot = await session.supervisor.aget_state(
        {"configurable": {"thread_id": session.thread_id}}
    )
    return bool(snapshot.interrupts)


def format_agent_result(
    request: Request,
    session_id: str,
    result: dict[str, Any],
) -> AgentReply:
    # 没有该键时使用空元组，表示本轮没有被 interrupt 暂停。
    interrupts = result.get("__interrupt__", ())

    if interrupts:
        # value 才是业务代码通过 interrupt 传出的待确认信息。
        value = interrupts[0].value
        # API 统一返回字典；如果只传了字符串，就包装为 message 字段。
        confirmation = value if isinstance(value, dict) else {"message": str(value)}
        return AgentReply(
            answer=None,
            needs_confirmation=True,
            confirmation=confirmation,
            trace=outer_trace(result),
        )

    # 只保留“不包含 Tool 调用”的 AIMessage，它才是面向用户的回答。
    answers = [
        message_text(message)
        for message in result.get("messages", [])
        if isinstance(message, AIMessage) and not message.tool_calls
    ]

    # 一轮运行可能产生多条 AIMessage，最后一条才是最终回答。
    answer = answers[-1] if answers else "Agent 没有返回可展示的回答"

    return AgentReply(
        answer=answer,
        needs_confirmation=False,
        confirmation=None,
        trace=outer_trace(result),
    )


# 使用前端传入的id去数据库中查找对应的回话id和用户信息，返回 UserSession 对象
def restore_user_session(
    request: Request,
    thread_id: str,
) -> UserSession:
    """从 MySQL 恢复服务端用户上下文，再构造运行时会话。"""

    statement = (
        select(AgentThread, User)
        .join(User, AgentThread.user_id == User.user_id)
        .where(AgentThread.thread_id == thread_id)
    )
    with SessionLocal() as db:
        row = db.execute(statement).one_or_none()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail="会话不存在，请重新登录",
            )

        agent_thread, user = row
        if (
            not user.login_enabled
            or user.account_status != "active"
            or user.username is None
            or user.role not in {"employee", "hr"}
        ):
            raise HTTPException(
                status_code=401,
                detail="该教学账号当前不可登录",
            )

        restored_thread_id = agent_thread.thread_id

    return request.app.state.agent.build_runtime_session(
        restored_thread_id,
        user,
    )


# 工具函数，返回 SSE 事件字符串
def sse_event(
    event: str,
    data: dict[str, Any],
    # 星号之后的参数只能通过参数名传入，避免混淆事件 ID 和重连时间。
    *,
    event_id: str | None = None,  # 可选的事件编号
    retry_ms: int | None = None,  # 可选的重连等待时间，单位为毫秒
) -> str:
    """把事件名称和字典编码成一个完整的 SSE 文本块。"""

    # 每个元素对应 SSE 文本中的一行。
    lines: list[str] = []
    if event_id is not None:
        lines.append(f"id: {event_id}")

    # event 是项目约定的事件名称，例如 text_delta 或 done。
    lines.append(f"event: {event}")

    if retry_ms is not None:
        lines.append(f"retry: {retry_ms}")

    # ensure_ascii=False 让中文直接显示，不转换成 \uXXXX。
    payload = json.dumps(data, ensure_ascii=False)
    lines.append(f"data: {payload}")
    # 最后的两个换行表示当前 SSE 事件结束。
    return "\n".join(lines) + "\n\n"


# 调用 agent 中的流式输出
async def stream_agent_events(
    # request 可读取应用共享状态，也可以检查客户端是否已经断开。
    request: Request,
    # session 保存当前登录用户的 Context、thread_id 和 Supervisor。
    session: UserSession,
    # payload 是 FastAPI 已校验的请求体，包含 session_id 和 message。
    payload: ChatRequest,
) -> AsyncIterator[str]:
    """把 Agent 内部事件转换成浏览器可以消费的 SSE 事件。"""

    # 建立流后立即告诉页面：服务端已开始处理。
    yield sse_event("status", {"message": "Agent 已开始处理"})

    try:
        # timeout 限制整个 Agent 流的最长运行时间，超时后进入 except TimeoutError。
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            # app.state.agent 是 lifespan() 启动时创建的共享 Agent 应用。
            async for event in request.app.state.agent.stream_v2_chat(
                session,
                payload.message,
            ):
                # is_disconnected() 为 True 表示浏览器已关闭或取消当前连接。
                if await request.is_disconnected():
                    return

                # 把第七章的 {event, data} 转换成 SSE 文本。
                yield sse_event(event["event"], event["data"])

        # 判断是否有待确认操作
        waiting = await has_pending_confirmation(session)
        yield sse_event(
            "done",
            {"status": ("waiting_confirmation" if waiting else "completed")},
        )

    except TimeoutError:
        # 响应头已经发送，此时通过 SSE error 报告超时。
        yield sse_event(
            "error",
            {"message": "Agent 处理超时，请稍后重试"},
        )
    except asyncio.CancelledError:
        # CancelledError 表示协程被取消；继续抛出，让 ASGI Server 完成清理。
        raise
    except Exception as exc:
        print("Exception:", exc)
        # 只返回可公开的错误，不暴露模型地址、密钥或堆栈。
        yield sse_event(
            "error",
            {"message": "Agent 调用失败，请稍后重试"},
        )
    finally:
        # discard() 在元素不存在时也不会报错，适合在 finally 中安全清理。
        request.app.state.active_streams.discard(session.thread_id)


# 从请求头获取token的工具
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/token")


def get_owned_runtime_session(
    request: Request,
    thread_id: str,
    current_user: Annotated[
        CurrentUser,
        Depends(get_current_user),
    ],
) -> UserSession:
    """验证 Thread 归属，并构造本次请求使用的运行时会话。"""

    # 同时匹配 thread_id 和当前 user_id，防止水平越权访问。
    statement = select(AgentThread).where(
        AgentThread.thread_id == thread_id,
        AgentThread.user_id == current_user.user_id,
    )
    with SessionLocal() as db:
        agent_thread = db.scalar(statement)
        if agent_thread is None:
            # 不区分“不存在”和“不属于我”，避免泄露资源是否存在。
            raise HTTPException(
                status_code=404,
                detail="对话不存在",
            )
        restored_thread_id = agent_thread.thread_id

    return request.app.state.agent.build_runtime_session(restored_thread_id, current_user)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 在接收请求前检查索引，尽早暴露“知识库尚未构建”的准备错误。
    if not INDEX_MANIFEST_PATH.exists():
        raise RuntimeError("尚未构建向量库，请先运行：python build_index.py")

    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path())) as checkpointer:
        # 整个应用只创建一套多 Agent，接口中通过此对象调用 agent
        app.state.agent = MultiAgentApplication(checkpointer=checkpointer)

        # active_streams 保存正在运行 SSE 的 session_id，防止同一会话并发执行。
        app.state.active_streams = set()

        # yield 之前是启动阶段；执行到这里后，FastAPI 才开始处理请求。
        yield

        # yield 之后是关闭阶段，清理当前进程中的临时状态。

        app.state.active_streams.clear()


app = FastAPI(
    title="多 Agent 新员工助手 API",
    description="Week16 Day02：使用普通 HTTP 调用多 Agent 助手",
    version="1.0.0",
    # 生命周期
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount(
    "/static",
    # 该对象负责响应 /static 下的 CSS、JavaScript 等文件。
    StaticFiles(directory=STATIC_DIR),
    name="static",
)

# 配置处理跨域的中间件
app.add_middleware(
    CORSMiddleware,
    # 只允许课堂中可能使用的两个 Live Server 来源跨域访问。
    allow_origins=[
        "http://127.0.0.1:10086",
        "http://localhost:10086",
    ],
    # 本课没有通过 Cookie 等方式携带登录凭据。
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


# Docker 和编排系统使用的轻量存活检查。
@app.get("/health", include_in_schema=False)
def health() -> dict[str, str]:
    return {"status": "ok"}


# 配置首页
@app.get("/", include_in_schema=False)
def index_page() -> FileResponse:
    # 访问根路径时直接返回配套页面入口。
    return FileResponse(STATIC_DIR / "index.html")


# 用户登录
@app.post(
    "/api/auth/token",
    response_model=TokenResponse,
    tags=["Authentication"],
)
def login_for_access_token(
    form_data: LoginRequest,
) -> TokenResponse:
    """验证用户名和密码，只签发 Token，不创建 Thread。"""

    with SessionLocal() as db:
        # 去数据库中查找对应的用户，并且校验传入的密码和数据库密码是否一致
        user = authenticate_user(db, form_data.username, form_data.password)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Session 关闭前取出主键；Token 只保存这个稳定用户编号。
        user_id = user.user_id

    # 根据用户的 user_id 生成 token 返回给前端
    return TokenResponse(access_token=create_access_token(user_id))


# 解析 token 从数据库中查询当前登录的用户
CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


# 查询用户信息接口，解析前端传入的 token，去数据库中查找对应的用户
@app.get(
    "/api/auth/me",
    response_model=CurrentUserResponse,
    tags=["Authentication"],
)
def read_current_user(current_user: CurrentUserDep) -> CurrentUserResponse:
    """返回数据库中最新且允许展示的当前用户信息。"""

    return CurrentUserResponse(
        user_id=current_user.user_id,
        username=current_user.username,
        display_name=current_user.display_name,
        role=current_user.role,
        department_id=current_user.department_id,
    )


# 查询当前登录用户的所有会话列表
@app.get(
    "/api/threads",
    response_model=list[ThreadResponse],
    tags=["Threads"],
)
def list_threads(
    current_user: CurrentUserDep,
) -> list[ThreadResponse]:
    # user_id 只能来自已经验证的 CurrentUser，不能由浏览器提交。
    statement = (
        select(AgentThread)
        .where(AgentThread.user_id == current_user.user_id)
        # 最新创建的会话显示在列表最前面。
        .order_by(AgentThread.created_at.desc())
    )
    with SessionLocal() as db:
        records = list(db.scalars(statement).all())

        for row in records:
            print(row.thread_id, row.user_id, row.created_at)

        return [
            ThreadResponse(
                thread_id=record.thread_id,
                created_at=record.created_at,
            )
            for record in records
        ]


# 创建新会话，创建 thread_id 添加到数据库中
@app.post(
    "/api/threads",
    response_model=ThreadResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Threads"],
)
def create_thread(
    current_user: CurrentUserDep,
) -> ThreadResponse:
    # begin() 正常结束时自动提交，出现异常时自动回滚。
    with SessionLocal.begin() as db:
        record = AgentThread(
            thread_id=str(uuid4()),
            user_id=current_user.user_id,
        )
        db.add(record)
        # flush 执行 INSERT 但不提交，使 ORM 默认生成的 created_at 可读取。
        db.flush()
        response = ThreadResponse(
            thread_id=record.thread_id,
            created_at=record.created_at,
        )
    return response


# 查询具体会话的聊天记录
@app.get(
    "/api/threads/{thread_id}/messages",
    response_model=ThreadHistoryResponse,
    tags=["Threads"],
)
async def list_thread_messages(
    session: Annotated[UserSession, Depends(get_owned_runtime_session)], request: Request
) -> ThreadHistoryResponse:

    # RuntimeSessionDep 已经完成 JWT 验证和 Thread 归属校验。
    messages, confirmation = await request.app.state.agent.get_thread_history(session)

    return ThreadHistoryResponse(
        messages=[HistoryMessage(**item) for item in messages],
        needs_confirmation=confirmation is not None,
        confirmation=confirmation,
    )


# 用户确认接口
@app.post("/api/{thread_id}/confirm", response_model=AgentReply, tags=["对话"])
async def confirm_action(
    payload: ConfirmRequest,
    request: Request,
    session: Annotated[UserSession, Depends(get_owned_runtime_session)],
) -> AgentReply:
    if not session:
        raise HTTPException(status_code=401, detail="此用户不存在，请重新登录")

    # 如果当前用户没有待确认操作提示用户
    confirmation = await has_pending_confirmation(session)
    if not confirmation:
        raise HTTPException(
            status_code=409,
            detail="当前会话没有等待确认的操作",
        )

    try:
        result = await request.app.state.agent.resume(
            session,
            payload.approved,
        )
        return format_agent_result(request=request, session_id=session.thread_id, result=result)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Agent 调用失败，请稍后重试",
        ) from exc


# 聊天接口
@app.post(
    "/api/v2/chat/{thread_id}/stream",
    # 告诉 OpenAPI 和 FastAPI：这个路由返回流式响应，而不是普通 JSON。
    response_class=StreamingResponse,
    # tags 只用于把接口归类到 /docs 的 SSE 分组。
    tags=["SSE"],
)
async def stream_chat(
    # payload 由请求体 JSON 校验并转换为 ChatRequest。
    payload: ChatRequest,
    # request 由 FastAPI 注入，用于访问 app.state。
    request: Request,
    session: Annotated[UserSession, Depends(get_owned_runtime_session)],
) -> StreamingResponse:
    """校验请求后建立 SSE 响应，持续发送 Agent 事件。"""

    if not session:
        raise HTTPException(status_code=401, detail="此用户不存在，请重新登录")

    # 同一会话等待确认时，不能继续提交新的聊天任务。
    if await has_pending_confirmation(session):
        raise HTTPException(
            status_code=409,
            detail="请先处理当前待确认操作",
        )

    # active_streams 中存在当前编号，说明上一条 SSE 请求仍在运行。
    if session.thread_id in request.app.state.active_streams:
        raise HTTPException(
            status_code=409,
            detail="当前会话正在处理上一条消息",
        )

    # 在返回 StreamingResponse 前先占用运行标记，防止并发请求同时进入。
    request.app.state.active_streams.add(session.thread_id)

    return StreamingResponse(
        # 第一个参数是持续产生 SSE 字符串的异步生成器。
        stream_agent_events(request, session, payload),
        # 浏览器根据该响应类型识别 SSE 事件流。
        media_type="text/event-stream",
        # 要求客户端和中间缓存不要复用旧的事件流响应。
        headers={"Cache-Control": "no-cache"},
    )
