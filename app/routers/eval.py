"""RAG 评测任务端点：后台线程运行离线评测，前端轮询进度与结果。

同一时间只允许一个评测任务（app.state.eval_job）；结果只保存在内存中，
供 /api/eval/status 与 /api/eval/results 消费，应用重启即清空。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.routers.settings import require_local
from persona.service import resolve_knowledge_scope

router = APIRouter(prefix="/api/eval", tags=["eval"])

DATASET_PATH = Path("rag/eval/sample_questions.jsonl")


class EvalRunPayload(BaseModel):
    persona_id: str
    max_cases: int | None = Field(default=None, ge=1, le=100)


def _job(request: Request) -> dict[str, Any]:
    job = getattr(request.app.state, "eval_job", None)
    if job is None:
        job = {}
        request.app.state.eval_job = job
    return job


def _execute(payload: EvalRunPayload, session_factory, job: dict[str, Any]) -> None:
    """后台线程：派生角色作用域 -> 跑离线评测 -> 写回 job 状态。"""

    from rag.eval.metrics import summarize_generation, summarize_retrieval
    from rag.eval.runner import load_dataset, run_eval

    try:
        session = session_factory()
        try:
            scope = resolve_knowledge_scope(session, payload.persona_id)
        finally:
            session.close()

        dataset = load_dataset(DATASET_PATH)
        cases = dataset[: payload.max_cases] if payload.max_cases else dataset
        total = len(cases)

        def progress(done: int, count: int) -> None:
            job["state"] = "running"
            job["progress"] = done
            job["total"] = count

        results = run_eval(
            cases,
            persona_id=payload.persona_id,
            workspace_id=scope.workspace_id,
            knowledge_space_ids=list(scope.knowledge_space_ids),
            progress=progress,
        )
        case_dicts = [result.as_dict() for result in results]
        job["state"] = "done"
        job["progress"] = len(results)
        job["total"] = total
        job["metrics"] = {
            **summarize_retrieval(case_dicts),
            **summarize_generation(case_dicts),
        }
        job["cases"] = case_dicts
    except Exception as exc:  # noqa: BLE001 - 后台任务错误只上报给轮询端
        job["state"] = "error"
        job["error"] = str(exc)


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
def start_eval(payload: EvalRunPayload, request: Request) -> dict:
    require_local(request)
    job = _job(request)
    if job.get("state") == "running":
        raise HTTPException(status_code=409, detail="已有评测任务在运行")
    job.clear()
    job.update(
        {
            "state": "pending",
            "progress": 0,
            "total": 0,
            "metrics": {},
            "cases": [],
            "error": "",
        }
    )
    threading.Thread(
        target=_execute,
        args=(payload, request.app.state.session_factory, job),
        daemon=True,
    ).start()
    return {"state": "pending"}


@router.get("/status")
def eval_status(request: Request) -> dict:
    require_local(request)
    job = _job(request)
    return {
        "state": job.get("state", "idle"),
        "progress": job.get("progress", 0),
        "total": job.get("total", 0),
        "error": job.get("error", ""),
    }


@router.get("/results")
def eval_results(request: Request) -> dict:
    require_local(request)
    job = _job(request)
    return {
        "state": job.get("state", "idle"),
        "metrics": job.get("metrics", {}),
        "cases": job.get("cases", []),
    }
