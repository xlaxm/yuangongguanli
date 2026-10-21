"""构建 Day06 使用的本地 Chroma 向量库。"""

from knowledge_base import build_vector_store


def main() -> None:
    # 详细的文档解析和 Chroma 写入都封装在 knowledge_base.py。
    summary = build_vector_store()

    # 只打印可以核对的构建摘要，不把预期数量写死。
    print("向量库构建完成：")
    print(f"- 原始文档：{summary['document_count']}")
    print(f"- 文本分块：{summary['chunk_count']}")
    print(f"- 索引目录：{summary['vector_store_path']}")
    print(f"- Embedding：{summary['embedding_model']}")


if __name__ == "__main__":
    # 只有直接运行 python build_index.py 时才构建索引。
    main()
