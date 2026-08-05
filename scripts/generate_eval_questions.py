"""按档位生成 RAG 评测问题（默认轻量 5 个问题：2 可检索 + 2 复杂 + 1 无关探针）。

用法：
    python scripts/generate_eval_questions.py --persona-id <角色ID> \
        --knowledge-space-id <知识空间ID> [--out data/eval/questions.jsonl] \
        [--tier fast|standard|thorough] [--total 5] [--max-chunks 12] \
        [--workspace-id local-default]

生成的问题集自带 expected_chunk_ids（问题来源块的真实 chunk_id），评测时是
真标注召回，且总数固定；配合评测运行器：
    python scripts/eval_rag.py --data <out> --persona-id <角色ID> \
        --knowledge-space-id <知识空间ID>
运行器检测到问题集内已含无关题探针后不会重复附加，总用例数即问题集规模。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rag.eval.question_generator import (
    DEFAULT_TIER,
    DEFAULT_TOTAL,
    TIERS,
    generate_questions_for_persona,
    load_chunks,
)
from rag.llm import get_llm


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a fixed-size RAG eval question set from a persona's knowledge space")
    parser.add_argument("--persona-id", required=True, help="persona id")
    parser.add_argument("--knowledge-space-id", action="append", required=True, dest="knowledge_space_ids")
    parser.add_argument("--workspace-id", default="local-default")
    parser.add_argument(
        "--tier",
        choices=tuple(TIERS),
        default=DEFAULT_TIER,
        help=f"档位（默认 {DEFAULT_TIER}）：fast=5 个问题 / standard=10 个问题 / thorough=15 个问题",
    )
    parser.add_argument("--total", type=int, default=None, help="自定义问题总数（覆盖档位）")
    parser.add_argument("--max-chunks", type=int, default=None, help="最多使用片段数（默认全部）")
    parser.add_argument("--out", type=Path, default=None, help="输出 JSONL 路径（默认 data/eval/questions_<persona>.jsonl）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.total < 3:
        print("--total 至少为 3（至少 1 道可检索题 + 2 道探针）", file=sys.stderr)
        return 2
    chunks = load_chunks(args.workspace_id, args.knowledge_space_ids, args.max_chunks)
    if not chunks:
        print("知识空间没有可用片段（可能未上传资料或片段过短）", file=sys.stderr)
        return 2
    output = generate_questions_for_persona(
        persona_id=args.persona_id,
        workspace_id=args.workspace_id,
        knowledge_space_ids=args.knowledge_space_ids,
        out_path=args.out,
        total=args.total,
        tier=args.tier,
    )
    rows = [line for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"已生成 {len(rows)} 条问题（覆盖 {len(chunks)} 个片段）到 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
