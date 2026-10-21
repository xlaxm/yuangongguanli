#!/bin/sh
set -eu

# 只读根文件系统下，把镜像内索引复制到挂载于 /tmp 的临时可写目录。
vector_store_source="${VECTOR_STORE_SOURCE:-/opt/vector-store}"
vector_store_dir="${VECTOR_STORE_DIR:-/tmp/vector_store}"

mkdir -p "${vector_store_dir}"
if [ ! -f "${vector_store_dir}/index_manifest.json" ]; then
    cp -R "${vector_store_source}/." "${vector_store_dir}/"
fi

# 检查点必须落在持久化 Volume；目录不可写时尽早失败。
checkpoint_db="${CHECKPOINT_DB:-/app/runtime/checkpoints.sqlite3}"
mkdir -p "$(dirname "${checkpoint_db}")"

exec "$@"
