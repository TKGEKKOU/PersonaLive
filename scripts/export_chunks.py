"""导出知识空间的所有文本片段（chunk_id + 预览），供人工标注 expected_chunk_ids。

用法：
    python scripts/export_chunks.py --workspace-id local \
        --knowledge-space-id <知识空间ID> --out chunks.csv
"""

from __future__ import annotations

import argparse
import csv

from ingestion.milvus_store import MilvusRagStore
from rag.contracts import RagQueryContext
from rag.retriever import build_scope_expression


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出知识空间片段用于标注")
    parser.add_argument("--workspace-id", default="local")
    parser.add_argument("--knowledge-space-id", action="append", required=True)
    parser.add_argument("--out", type=argparse.FileType("w", encoding="utf-8-sig"), required=True)
    args = parser.parse_args(argv)

    store = MilvusRagStore()
    store.connect()
    expr = build_scope_expression(
        RagQueryContext(
            persona_id="export",
            workspace_id=args.workspace_id,
            knowledge_space_ids=tuple(args.knowledge_space_id),
        )
    )
    rows = store.client().query(
        collection_name=store.settings.collection_name,
        filter=expr,
        output_fields=["chunk_id", "doc_id", "filename", "title", "text"],
        limit=16384,
    )
    writer = csv.DictWriter(
        args.out,
        fieldnames=["chunk_id", "doc_id", "filename", "title", "text"],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key) or "" for key in writer.fieldnames})
    print(f"已导出 {len(rows)} 个片段到 {args.out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
