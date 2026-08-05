"""RAG 评测任务端点：后台线程运行离线评测，前端轮询进度与结果。

同一时间只允许一个评测任务（app.state.eval_job）；结果只保存在内存中，
供 /api/eval/status 与 /api/eval/results 消费，应用重启即清空。
"""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.routers.settings import require_local
from persona.service import resolve_knowledge_scope
from rag.eval.question_generator import DEFAULT_TIER

router = APIRouter(prefix="/api/eval", tags=["eval"])

class EvalRunPayload(BaseModel):
    persona_id: str
    max_cases: int | None = Field(default=None, ge=1, le=100)
    web_fallback: bool = False
    tier: str = DEFAULT_TIER


def _job(request: Request) -> dict[str, Any]:
    job = getattr(request.app.state, "eval_job", None)
    if job is None:
        job = {}
        request.app.state.eval_job = job
    return job


def _execute(payload: EvalRunPayload, session_factory, job: dict[str, Any]) -> None:
    """后台线程：派生角色作用域 -> 跑离线评测 -> 写回 job 状态。"""

    from rag.eval.metrics import summarize_generation, summarize_retrieval
    from rag.eval.runner import check_scope_isolation, load_dataset, run_eval

    try:
        session = session_factory()
        try:
            scope = resolve_knowledge_scope(session, payload.persona_id)
        finally:
            session.close()

        def progress(done: int, count: int) -> None:
            job["state"] = "running"
            job["progress"] = done
            job["total"] = count

        def status(text: str) -> None:
            job["status_text"] = text

        def step_callback(index: int, question: str, step: str) -> None:
            job["current_question"] = f"第 {index}/{len(cases)} 题"
            job["current_step"] = step
            job["current_question_text"] = question[:28]

        # 按角色知识空间生成档位题集（含无关探针），无需人工准备。
        from rag.eval.question_generator import generate_questions_for_persona

        job["phase"] = "generating"
        job["status_text"] = "准备生成问题…"
        dataset_path = generate_questions_for_persona(
            persona_id=payload.persona_id,
            workspace_id=scope.workspace_id,
            knowledge_space_ids=list(scope.knowledge_space_ids),
            tier=payload.tier,
            status=status,
        )
        dataset = load_dataset(dataset_path)
        cases = dataset[: payload.max_cases] if payload.max_cases else dataset

        job["phase"] = "running"
        job["status_text"] = "开始评测…"
        job["current_question"] = ""
        job["current_step"] = ""
        results = run_eval(
            cases,
            persona_id=payload.persona_id,
            workspace_id=scope.workspace_id,
            knowledge_space_ids=list(scope.knowledge_space_ids),
            progress=progress,
            enable_web_fallback=payload.web_fallback,
            include_probes=False,  # 生成的题集已含固定无关探针
            step_callback=step_callback,
        )
        case_dicts = [result.as_dict() for result in results]
        job["state"] = "done"
        job["phase"] = "done"
        job["status_text"] = "评测完成"
        job["current_question"] = ""
        job["current_step"] = ""
        job["progress"] = len(results)
        job["total"] = len(results)
        job["metrics"] = {
            **summarize_retrieval(case_dicts),
            **summarize_generation(case_dicts),
            "scope_isolation_ok": check_scope_isolation(
                scope.workspace_id,
                list(scope.knowledge_space_ids),
            ),
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
            "phase": "pending",
            "status_text": "等待开始",
            "current_question": "",
            "current_step": "",
            "current_question_text": "",
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
        "phase": job.get("phase", "idle"),
        "status_text": job.get("status_text", ""),
        "current_question": job.get("current_question", ""),
        "current_step": job.get("current_step", ""),
        "current_question_text": job.get("current_question_text", ""),
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


@router.post("/analyze")
def analyze_eval(request: Request) -> dict:
    """对已完成的评测结果做 AI 分析：异常、性能、功能与建议。"""

    require_local(request)
    job = _job(request)
    if job.get("state") != "done":
        raise HTTPException(status_code=409, detail="评测未完成，暂不能分析")
    from rag.eval.analyzer import analyze_results

    analysis = analyze_results(job.get("metrics", {}), job.get("cases", []))
    job["analysis"] = analysis
    return {"analysis": analysis}
