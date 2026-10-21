# 基础镜像可通过 ARG 换成可信的镜像加速器地址，默认保持 Docker Hub 官方镜像。
ARG PYTHON_IMAGE=python:3.13.7-slim-bookworm
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security
ARG HF_ENDPOINT=https://huggingface.co

# ============================================================
# Python base：builder 和 runtime 共享的系统依赖。
# ============================================================
FROM ${PYTHON_IMAGE} AS python-base

ARG DEBIAN_MIRROR
ARG DEBIAN_SECURITY_MIRROR

# 允许通过 ARG 切换 Debian 软件源；默认仍使用官方源。
RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_MIRROR}|g" \
        -e "s|http://deb.debian.org/debian|${DEBIAN_MIRROR}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# Builder common：安装依赖并准备索引构建所需的源码。
# ============================================================
FROM python-base AS builder-common

ARG EMBEDDING_MODEL
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG TORCH_VERSION=2.14.1
ARG HF_ENDPOINT

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    HF_HOME=/opt/huggingface \
    HF_ENDPOINT=${HF_ENDPOINT} \
    HF_HUB_OFFLINE=0 \
    TRANSFORMERS_OFFLINE=0 \
    EMBEDDING_MODEL=${EMBEDDING_MODEL}

WORKDIR /app

# 先安装依赖文件，源码变化时仍可复用 pip 和 wheel 构建缓存。
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install \
        --index-url "${TORCH_INDEX_URL}" \
        --extra-index-url "${PIP_INDEX_URL}" \
        "torch==${TORCH_VERSION}" \
    && /opt/venv/bin/pip install -r requirements.txt

# 索引构建只需要知识库代码和原始知识文件。
COPY knowledge_base.py build_index.py ./
COPY knowledge ./knowledge

# ============================================================
# 默认构建目标：从 HF_ENDPOINT 下载模型后生成索引。
# ============================================================
FROM builder-common AS builder-download

RUN HF_HUB_DISABLE_XET=1 /opt/venv/bin/python build_index.py

# ============================================================
# 离线构建目标：通过 --build-context hf-cache=<本地 hub 缓存> 复用模型。
# ============================================================
FROM builder-common AS builder-offline

COPY --from=hf-cache / /opt/huggingface/hub
RUN HF_HUB_OFFLINE=1 /opt/venv/bin/python build_index.py

# ============================================================
# Runtime common：仅保留运行依赖和应用代码。
# ============================================================
FROM python-base AS runtime-common

ARG APP_UID=10001
ARG APP_GID=10001
ARG EMBEDDING_MODEL

# 使用固定 UID/GID 的非 root 用户运行应用。
RUN groupadd --gid "${APP_GID}" appuser \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
        --home-dir /app --no-create-home --shell /usr/sbin/nologin appuser

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    HOME=/tmp \
    TMPDIR=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    MPLCONFIGDIR=/tmp/matplotlib \
    HF_HOME=/opt/huggingface \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    EMBEDDING_MODEL=${EMBEDDING_MODEL} \
    VECTOR_STORE_DIR=/tmp/vector_store \
    VECTOR_STORE_SOURCE=/opt/vector-store \
    CHECKPOINT_DB=/app/runtime/checkpoints.sqlite3 \
    CHROMA_TELEMETRY_ENABLED=False \
    ANONYMIZED_TELEMETRY=False

WORKDIR /app

# Python 虚拟环境来自共享 builder，runtime 不安装编译工具。
COPY --from=builder-common /opt/venv /opt/venv

# Docker 构建上下文由 .dockerignore 过滤，不会包含 .env 或本地数据。
COPY --chown=appuser:appuser . /app

# 检查点目录必须由持久化 Volume 提供；这里只准备容器内的初始权限。
RUN mkdir -p /app/runtime \
    && chown -R appuser:appuser /app/runtime \
    && chmod 0755 /app/docker-entrypoint.sh

# ============================================================
# 离线 runtime：适用于已预先准备 Hugging Face 缓存的本机构建。
# ============================================================
FROM runtime-common AS runtime-offline

COPY --from=builder-offline --chown=root:root /app/vector_store /opt/vector-store
COPY --from=builder-offline --chown=root:root /opt/huggingface /opt/huggingface

VOLUME ["/app/runtime"]
USER appuser
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "30"]

# ============================================================
# 默认 runtime：无额外构建上下文时联网下载模型，适合 GitHub 用户。
# ============================================================
FROM runtime-common AS runtime-download

COPY --from=builder-download --chown=root:root /app/vector_store /opt/vector-store
COPY --from=builder-download --chown=root:root /opt/huggingface /opt/huggingface

VOLUME ["/app/runtime"]
USER appuser
EXPOSE 8000
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-graceful-shutdown", "30"]
