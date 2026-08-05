"""RAG 离线评测命令行入口。

用法示例：
    python scripts/eval_rag.py --data rag/eval/sample_questions.jsonl \
        --persona-id <角色ID> --knowledge-space-id <知识空间ID> \
        [--workspace-id local-default] [--max-cases 20] [--web] [--no-probes] \
        [--fail-below recall=0.5 mrr=0.5 grounded=0.7 refusal=0.5]

数据格式（JSONL，每行一个 JSON 对象）：
    {"question": "...", "expected_chunk_ids": ["chunk-1", ...] | null,
     "reference_answer": "..." | null}

- expected_chunk_ids 可选：不填时评测自动用 LLM 判定候选池内的相关片段
  （免标注模式），无需人工准备标签；填了则按人工标注口径计算真实召回。
- reference_answer 当前未参与指标，保留字段供后续扩展。
- 数据集未带探针时自动附加内置无关问题探针测量拒答率；题集自带
  _probe 标记（自动生成的题集）时不会重复附加。可用 --no-probes 关闭。
- --fail-below 用于回归门禁（CI）：任一指标低于阈值则进程退出码为 1。
  可用键：recall / precision / mrr / hit1 / grounded / useful /
  refusal / answer / accepted / confidence。
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
    parser.add_argument("--workspace-id", default="local-default")
    parser.add_argument("--max-cases", type=int, default=None)
    parser.add_argument("--web", action="store_true", help="允许本地证据不足时联网兜底")
    parser.add_argument("--no-probes", action="store_true", help="不附加内置无关问题探针")
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
        enable_web_fallback=args.web,
        include_probes=not args.no_probes,
    )

    from rag.eval.runner import check_scope_isolation

    retrieval = summarize_retrieval([result.as_dict() for result in results])
    generation = summarize_generation([result.as_dict() for result in results])
    report = {
        "retrieval": retrieval,
        "generation": generation,
        "scope_isolation_ok": check_scope_isolation(
            args.workspace_id,
            args.knowledge_space_ids,
        ),
        "cases": [result.as_dict() for result in results],
    }
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, ensure_ascii=False, indent=2))

    gates = {"recall": retrieval["recall_at_k"], "precision": retrieval["precision_at_k"],
             "mrr": retrieval["mrr"], "hit1": retrieval["hit_at_1"],
             "grounded": generation.get("grounded_rate") or 0.0,
             "useful": generation.get("useful_rate") or 0.0,
             "refusal": generation.get("refusal_rate") or 0.0,
             "answer": generation.get("answer_rate") or 0.0,
             "accepted": generation.get("accepted_rate") or 0.0,
             "confidence": generation.get("mean_confidence") or 0.0}
    failed = []
    for item in args.fail_below:
        key, _, raw = item.partition("=")
        try:
            threshold = float(raw)
        except ValueError:
            print(f"无效阈值: {item}", file=sys.stderr)
            return 2
        if key not in gates:
            print(
                f"未知指标: {key}（可选: recall/precision/mrr/hit1/grounded/useful/refusal/answer/accepted/confidence）",
                file=sys.stderr,
            )
            return 2
        if gates[key] < threshold:
            failed.append(f"{key}={gates[key]:.3f} < {threshold:.3f}")
    if failed:
        print("未通过回归门禁:", "; ".join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
