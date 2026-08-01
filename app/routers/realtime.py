import asyncio

from fastapi import APIRouter, HTTPException, Path, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.chat_store import try_persist_text_message
from app.routers.agents import context_for, response_for
from realtime.protocol import (
    CancelEvent,
    ConfirmationEvent,
    PingEvent,
    TextSubmitEvent,
    parse_client_event,
    server_event,
)
from realtime.session import RealtimeSession, TurnInProgressError


router = APIRouter(tags=["realtime"])


def chunk_text(text: str, max_chars: int = 80) -> list[str]:
    """Split visible answers into short sentence-aware chunks for incremental UI/TTS."""
    remaining = text.strip()
    chunks: list[str] = []
    punctuation = "。！？!?；;\n"
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        window = remaining[: max_chars + 1]
        boundary = max((window.rfind(mark) for mark in punctuation), default=-1)
        if boundary >= 0:
            cut = boundary + 1
        else:
            cut = max_chars
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def voice_limited_answer(answer: str, persona_profile: dict) -> str:
    """Keep TTS-enabled replies short enough that speech can catch up with text."""
    tts = persona_profile.get("tts") or {}
    if not tts.get("enabled"):
        return answer
    limit = 50
    if len(answer) <= limit:
        return answer
    shortened = answer[:limit]
    boundary = max((shortened.rfind(mark) for mark in "。！？!?；;\n"), default=-1)
    return shortened[: boundary + 1 if boundary >= 0 else limit].rstrip() + "…"


@router.websocket("/ws/personas/{persona_id}/conversations/{conversation_id}")
async def persona_realtime(
    websocket: WebSocket,
    persona_id: str,
    conversation_id: str = Path(min_length=1, max_length=255),
) -> None:
    await websocket.accept()
    realtime = RealtimeSession()
    send_lock = asyncio.Lock()

    try:
        try:
            with websocket.app.state.session_factory() as db:
                context = context_for(websocket, db, persona_id, conversation_id)
        except HTTPException as exc:
            code = "persona_not_found" if exc.status_code == 404 else "invalid_context"
            await websocket.send_json(
                server_event("error", code=code, message=str(exc.detail))
            )
            await websocket.close(code=1008)
            return

        await websocket.send_json(
            server_event("session.ready", conversation_id=conversation_id)
        )

        async def send(event_type: str, *, turn_id: str | None = None, **payload) -> None:
            async with send_lock:
                await websocket.send_json(
                    server_event(event_type, turn_id=turn_id, **payload)
                )

        async def send_if_current(
            turn_id: str,
            event_type: str,
            **payload,
        ) -> None:
            async with send_lock:
                if realtime.is_current(turn_id):
                    await websocket.send_json(
                        server_event(event_type, turn_id=turn_id, **payload)
                    )

        async def run_query(turn_id: str, question: str) -> None:
            try:
                await send_if_current(turn_id, "turn.started")
                await send_if_current(turn_id, "agent.status", status="thinking")
                try_persist_text_message(
                    websocket.app.state.session_factory,
                    workspace_id=context.workspace_id,
                    persona_id=persona_id,
                    conversation_id=conversation_id,
                    role="user",
                    content=question,
                )
                result = await websocket.app.state.realtime_executions.run(
                    f"{persona_id}:{conversation_id}",
                    lambda: websocket.app.state.agent_service.query(question, context),
                )
                if not realtime.is_current(turn_id):
                    return
                response = response_for(result).model_dump()
                response["answer"] = voice_limited_answer(response["answer"], context.persona_profile)
                if response["status"] == "completed":
                    try_persist_text_message(
                        websocket.app.state.session_factory,
                        workspace_id=context.workspace_id,
                        persona_id=persona_id,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=response["answer"],
                    )
                if response["status"] == "pending_confirmation":
                    await send_if_current(
                        turn_id,
                        "confirmation.required",
                        **response,
                    )
                    return
                for chunk in chunk_text(response["answer"]):
                    await send_if_current(turn_id, "text.delta", text=chunk)
                    await asyncio.sleep(0)
                await send_if_current(turn_id, "text.final", **response)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await send_if_current(
                    turn_id,
                    "error",
                    code="agent_error",
                    message=str(exc),
                )
            finally:
                was_cancelled = not realtime.is_current(turn_id)
                await realtime.finish(turn_id)
                if was_cancelled:
                    try:
                        await send("session.ready", conversation_id=conversation_id)
                    except RuntimeError:
                        pass

        async def run_resume(
            turn_id: str,
            event: ConfirmationEvent,
        ) -> None:
            try:
                await send_if_current(turn_id, "turn.started")
                result = await websocket.app.state.realtime_executions.run(
                    f"{persona_id}:{conversation_id}",
                    lambda: websocket.app.state.agent_service.resume(
                        context,
                        event.specialist,
                        event.approved,
                    ),
                )
                if not realtime.is_current(turn_id):
                    return
                response = response_for(result).model_dump()
                response["answer"] = voice_limited_answer(response["answer"], context.persona_profile)
                if response["status"] == "completed":
                    try_persist_text_message(
                        websocket.app.state.session_factory,
                        workspace_id=context.workspace_id,
                        persona_id=persona_id,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=response["answer"],
                    )
                for chunk in chunk_text(response["answer"]):
                    await send_if_current(turn_id, "text.delta", text=chunk)
                    await asyncio.sleep(0)
                await send_if_current(turn_id, "text.final", **response)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await send_if_current(
                    turn_id,
                    "error",
                    code="agent_error",
                    message=str(exc),
                )
            finally:
                was_cancelled = not realtime.is_current(turn_id)
                await realtime.finish(turn_id)
                if was_cancelled:
                    try:
                        await send("session.ready", conversation_id=conversation_id)
                    except RuntimeError:
                        pass

        while True:
            try:
                event = parse_client_event(await websocket.receive_json())
            except (ValidationError, ValueError) as exc:
                await send(
                    "error",
                    code="invalid_event",
                    message=str(exc),
                )
                continue

            if isinstance(event, PingEvent):
                await send("session.pong")
            elif isinstance(event, CancelEvent):
                cancelled = await realtime.cancel()
                if cancelled:
                    await send("turn.cancelled", turn_id=cancelled)
            elif isinstance(event, TextSubmitEvent):
                try:
                    await realtime.start(
                        lambda turn_id: run_query(turn_id, event.question)
                    )
                except TurnInProgressError as exc:
                    await send("error", code="turn_in_progress", message=str(exc))
            elif isinstance(event, ConfirmationEvent):
                try:
                    await realtime.start(
                        lambda turn_id: run_resume(turn_id, event)
                    )
                except TurnInProgressError as exc:
                    await send("error", code="turn_in_progress", message=str(exc))
    except WebSocketDisconnect:
        pass
    finally:
        await realtime.close()
