# Day06 Multi-Agent

一个用于学习多 Agent、RAG 和容器化部署的 FastAPI 项目。项目通过 LangGraph
编排知识问答、员工服务和 HR 业务工具，并使用 Chroma 与本地 CPU Embedding
完成知识检索。

> 当前定位是学习与单机部署基线，不是完整的企业生产架构。
> 当前只支持单 worker、单实例运行。

## 功能概览

- FastAPI Web API 与静态前端
- LangGraph Supervisor + 多个专业 Agent
- 基于 Chroma 的公司知识检索
- 文档解析、OCR、权限过滤
- JWT 登录和 Thread 归属校验
- MySQL 业务数据访问
- LangGraph SQLite 会话检查点
- CLI 对话入口
- 多阶段 Docker 镜像
- Ruff、mypy、pytest 和 Secret 扫描门禁

## 系统架构

```text
Browser / CLI
      |
      v
   FastAPI
      |
      +---- JWT Authentication
      |
      +---- LangGraph Multi-Agent
      |         |
      |         +---- Business Tools ----> External MySQL
      |         |
      |         +---- Knowledge Tools ---> Chroma
      |                                    |
      |                                    +--> Local CPU Embedding
      |
      +---- SQLite Checkpoint (/app/runtime)
```

| 组件 | 当前实现 |
| --- | --- |
| Web 服务 | FastAPI + Uvicorn |
| Agent 编排 | LangGraph + LangChain |
| 业务数据库 | 外部 MySQL + SQLAlchemy + PyMySQL |
| 向量检索 | 本地 Chroma |
| Embedding | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| 会话检查点 | 本地 SQLite |
| CPU 推理 | PyTorch CPU |
| 前端 | 原生 HTML、CSS、JavaScript |

## 项目结构

```text
.
├── main.py                  # FastAPI 应用、路由、健康检查
├── multi_agent.py           # LangGraph 多 Agent 编排
├── business_tools.py        # 业务工具与权限控制
├── knowledge_base.py        # 知识解析、Chroma 和检索
├── build_index.py           # 构建 Chroma 索引
├── database.py              # SQLAlchemy Engine 与 Session
├── models.py                # MySQL ORM 模型
├── auth.py                  # JWT 与密码验证
├── dependencies.py          # FastAPI 认证依赖
├── cli_agent.py             # 命令行对话入口
├── static/                  # Web 前端
├── tests/                   # 自动测试
├── scripts/                 # 本地质量门禁
├── Dockerfile               # 多阶段容器构建
├── docker-entrypoint.sh     # 容器启动准备
├── pyproject.toml           # 项目、工具和质量门禁配置
├── requirements.txt         # Docker 运行时依赖
└── uv.lock                  # uv 锁定依赖
```

以下内容属于本地或受控数据，不应上传到公开 GitHub：

- `knowledge/`
- `data/`
- `runtime/`
- `.env`
- `.env.docker`
- `vector_store/`

## 前置条件

- Python 3.13
- uv 0.12.24 或兼容版本
- 可访问的外部 MySQL
- Docker Desktop（仅容器运行需要）

检查环境：

```powershell
python --version
uv --version
docker version
```

首次安装开发依赖：

```powershell
uv sync --locked --all-groups
```

## 环境变量

新环境可以复制占位模板：

```powershell
Copy-Item .env.example .env
```

如果 `.env` 已存在，不要覆盖它。

主要变量：

| 变量 | 说明 |
| --- | --- |
| `MODEL_PROVIDER` | LangChain 模型提供方，例如 `openai` |
| `MODEL_NAME` | 支持 Tool Calling 的聊天模型 |
| `API_KEY` | 模型服务密钥 |
| `BASE_URL` | OpenAI 兼容接口地址 |
| `EMBEDDING_MODEL` | 本地 Hugging Face Embedding |
| `JWT_SECRET_KEY` | 至少 32 字节的随机 JWT 密钥 |
| `JWT_ALGORITHM` | 当前只支持 `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access Token 有效期 |
| `DATABASE_URL` | SQLAlchemy MySQL 连接 URL |
| `CHECKPOINT_DB` | LangGraph SQLite 检查点路径 |

不要提交或截图以下文件：

```text
.env
.env.docker
```

## 业务数据库

项目使用外部 MySQL，不会自动建表，也没有内置 Alembic 迁移。
首次运行前必须：

1. 创建 MySQL 数据库。
2. 按照 `models.py` 创建业务表。
3. 导入必要的部门、用户、设备和申请数据。
4. 配置最小权限应用账号。

涉及的表包括：

- `departments`
- `users`
- `devices`
- `device_requests`
- `agent_threads`

`data/` 中的本地 JSON 不进入 Git，也不是自动初始化脚本。

## 知识库准备

`knowledge/` 包含公司内部资料，被 `.gitignore` 排除。请只通过公司受控渠道获取。

目录应至少包含：

```text
knowledge/
├── day05_knowledge_manifest.json
├── hr/
└── public/
```

构建 Chroma 索引：

```powershell
uv run python build_index.py
```

预期生成：

```text
vector_store/index_manifest.json
vector_store/chroma.sqlite3
```

构建和查询必须使用相同的 Embedding 模型。切换模型后需要重新构建索引。

## 本地启动

启动 Web 服务：

```powershell
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

访问：

- 首页：<http://127.0.0.1:8000/>
- OpenAPI：<http://127.0.0.1:8000/docs>
- 健康检查：<http://127.0.0.1:8000/health>

启动 CLI：

```powershell
uv run python cli_agent.py
```

## 主要接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/health` | 轻量存活检查 |
| `POST` | `/api/auth/token` | 用户登录并签发 JWT |
| `GET` | `/api/auth/me` | 获取当前用户 |
| `GET` | `/api/threads` | 获取当前用户会话 |
| `POST` | `/api/threads` | 创建会话 |
| `GET` | `/api/threads/{thread_id}/messages` | 获取会话历史 |
| `POST` | `/api/{thread_id}/confirm` | 确认待执行操作 |
| `POST` | `/api/v2/chat/{thread_id}/stream` | SSE 流式对话 |

`/health` 只验证 Web 进程可响应，不查询 MySQL，也不调用模型 API。

## Docker 构建

### 默认联网构建

默认目标为 `runtime-download`，构建时会下载 Embedding 模型：

```powershell
docker build -t day06-multi-agent:local .
```

### 使用本机模型缓存离线构建

如果本机已有 Hugging Face 模型缓存，可复用缓存并构建 `runtime-offline`：

```powershell
docker build --target runtime-offline `
  --build-context "hf-cache=$env:USERPROFILE\.cache\huggingface\hub" `
  -t day06-multi-agent:local .
```

该镜像包含 Python 依赖、Embedding 和 Chroma 索引，因此镜像较大。

## Docker 安全运行

Docker 运行时需要单独创建 `.env.docker`：

```powershell
Copy-Item .env .env.docker
```

然后在编辑器中把 `DATABASE_URL` 的主机改为：

```text
host.docker.internal
```

不要把这个文件提交到 Git。

创建检查点 Volume：

```powershell
docker volume create day06-runtime
```

启动容器：

```powershell
docker run -d `
  --name day06-agent `
  --env-file .env.docker `
  --add-host host.docker.internal:host-gateway `
  --read-only `
  --tmpfs "/tmp:rw,noexec,nosuid,nodev,size=512m" `
  --cap-drop ALL `
  --security-opt no-new-privileges:true `
  --mount "type=volume,source=day06-runtime,target=/app/runtime" `
  --publish 127.0.0.1:18000:8000 `
  --stop-timeout 35 `
  day06-multi-agent:local
```

检查运行状态：

```powershell
docker ps
docker exec day06-agent id
Invoke-RestMethod http://127.0.0.1:18000/health
docker inspect --format '{{.State.Health.Status}}' day06-agent
```

预期用户：

```text
uid=10001(appuser) gid=10001(appuser)
```

停止容器：

```powershell
docker stop day06-agent
```

`/app/runtime` 使用具名 Volume，容器停止或重建后检查点仍可保留。

## 质量门禁

本地一次执行全部检查：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\quality_gate.ps1
```

门禁包括：

- Ruff lint
- Ruff format check
- mypy
- pytest 与覆盖率
- `detect-secrets-hook --baseline .secrets.baseline`

也可以分别运行：

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy main.py
uv run pytest
```

Secret 扫描只检查 Git 可提交文件，不扫描 `.env`、内部知识、业务数据或运行时数据库。

## 当前限制

- 只使用一个 Uvicorn worker。
- 不支持多应用副本共享进程内状态。
- LangGraph 检查点使用本地 SQLite。
- 业务数据库为 MySQL，暂无 Alembic。
- 暂无 `compose.yaml`。
- 暂未迁移到 PostgreSQL/pgvector。
- 自动测试目前只有健康接口冒烟测试。
- 公开 GitHub 仓库不包含内部知识库。
- 包含内部知识的镜像只能推送到公司私有镜像仓库。

## 常见问题

### 启动提示缺少 `DATABASE_URL`

确认 `.env` 存在，并且 `database.py` 可以读取到 MySQL 连接 URL。

### Docker 容器无法连接 Windows MySQL

使用 `.env.docker`，数据库主机填写 `host.docker.internal`，并添加：

```text
--add-host host.docker.internal:host-gateway
```

同时确认 MySQL 允许来自 Docker 网络的连接。

### 启动提示尚未构建向量库

确认本机拥有受控的内部知识文件，然后执行：

```powershell
uv run python build_index.py
```

### 容器在只读根文件系统中启动失败

确认同时配置：

- `/tmp` tmpfs
- `/app/runtime` 可写 named volume

### 修改知识文档后检索结果没有变化

重新运行 `build_index.py`。只修改知识文件不会自动更新已有 Chroma 索引。
