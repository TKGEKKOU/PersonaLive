"""RAG 离线评测命令行入口。

用法示例：
    python scripts/eval_rag.py --data rag/eval/sample_questions.jsonl \
        --persona-id <角色ID> --knowledge-space-id <知识空间ID> \
        [--workspace-id local] [--max-cases 20] \
        [--fail-below recall=0.5 mrr=0.5 grounded=0.7]

数据格式（JSONL，每行一个 JSON 对象）：
    {"question": "...", "expected_chunk_ids": ["chunk-1", ...] | null,
     "reference_answer": "..." | null}

- expected_chunk_ids 需从真实 Milvus 数据中取 chunk_id（可用管理端检索或直接
  查询 Milvus）；缺失时跳过检索类指标，只统计生成质量与耗时。
- --fail-below 用于回归门禁（CI）：任一指标低于阈值则进程退出码为 1。
  可用键：recall / precision / mrr / hit1 / grounded / useful。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rag.eval.metrics import summarize_generation, summarize_retrieval
from rag.eval.runner import load_dataset, run_eval


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG offline evaluation")
    parser.add_argument("--data", type=Path, required=True, help="JSONL dataset path")
    parser.add_argument("--persona-id", required=True, help="persona id to evaluate")
    parser.add_argument("--knowledge-space-id", action="append", required=True, dest="knowledge_space_ids")
    parser.add_argument("--workspace-id", default="local")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--fail-below", nargs="*", default=[], metavar="KEY=VALUE",
                        help="regression gates, e.g. recall=0.5 grounded=0.7")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.data.is_file():
        print(f"数据集不存在: {args.data}", file=sys.stderr)
        return 2

    dataset = load_dataset(args.data)
    if not dataset:
        print("数据集为空", file=sys.stderr)
        return 2

    print(f"评测 {len(dataset[: args.max_cases] or dataset)} 条问题（persona={args.persona_id}）…")
    results = run_eval(
        dataset,
        persona_id=args.persona_id,
        workspace_id=args.workspace_id,
        knowledge_space_ids=args.knowledge_space_ids,
        max_cases=args.max_cases,
    )

    retrieval = summarize_retrieval([result.as_dict() for result in results])
    generation = summarize_generation([result.as_dict() for result in results])
    report = {
        "retrieval": retrieval,
        "generation": generation,
        "cases": [result.as_dict() for result in results],
    }
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))

    gates = {"recall": retrieval["recall_at_k"], "precision": retrieval["precision_at_k"],
             "mrr": retrieval["mrr"], "hit1": retrieval["hit_at_1"],
             "grounded": generation.get("grounded_rate") or 0.0,
             "useful": generation.get("useful_rate") or 0.0}
    failed = []
    for item in args.fail_below:
        key, _, raw = item.partition("=")
        try:
            threshold = float(raw)
        except ValueError:
            print(f"无效阈值: {item}", file=sys.stderr)
            return 2
        if key not in gates:
            print(f"未知指标: {key}（可选: recall/precision/mrr/hit1/grounded/useful）", file=sys.stderr)
            return 2
        if gates[key] < threshold:
            failed.append(f"{key}={gates[key]:.3f} < {threshold:.3f}")
    if failed:
        print("未通过回归门禁:", "; ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
