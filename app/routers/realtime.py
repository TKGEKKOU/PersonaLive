import asyncio

from anyio import to_thread
from fastapi import APIRouter, HTTPException, Path, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.routers.agents import context_for, response_for
from realtime.protocol import (
    CancelEvent,
    ConfirmationEvent,
    PingEvent,
    TextSubmitEvent,
    parse_client_event,
    server_event,
)
from realtime.session import RealtimeSession


router = APIRouter(tags=["realtime"])


@router.websocket("/ws/personas/{persona_id}/conversations/{conversation_id}")
async def persona_realtime(
    websocket: WebSocket,
    persona_id: str,
    conversation_id: str = Path(min_length=1, max_length=255),
) -> None:
    await websocket.accept()
    realtime = RealtimeSession()
    db = websocket.app.state.session_factory()

    try:
        try:
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

        async def send_if_current(
            turn_id: str,
            event_type: str,
            **payload,
        ) -> None:
            if realtime.is_current(turn_id):
                await websocket.send_json(
                    server_event(event_type, turn_id=turn_id, **payload)
                )

        async def run_query(turn_id: str, question: str) -> None:
            try:
                await send_if_current(turn_id, "turn.started")
                await send_if_current(turn_id, "agent.status", status="thinking")
                result = await to_thread.run_sync(
                    websocket.app.state.agent_service.query,
                    question,
                    context,
                    abandon_on_cancel=True,
                )
                if not realtime.is_current(turn_id):
                    return
                response = response_for(result).model_dump()
                if response["status"] == "pending_confirmation":
                    await send_if_current(
                        turn_id,
                        "confirmation.required",
                        **response,
                    )
                    return
                if response["answer"]:
                    await send_if_current(
                        turn_id,
                        "text.delta",
                        text=response["answer"],
                    )
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
                await realtime.finish(turn_id)

        async def run_resume(
            turn_id: str,
            event: ConfirmationEvent,
        ) -> None:
            try:
                await send_if_current(turn_id, "turn.started")
                result = await to_thread.run_sync(
                    websocket.app.state.agent_service.resume,
                    context,
                    event.specialist,
                    event.approved,
                    abandon_on_cancel=True,
                )
                if not realtime.is_current(turn_id):
                    return
                response = response_for(result).model_dump()
                if response["answer"]:
                    await send_if_current(
                        turn_id,
                        "text.delta",
                        text=response["answer"],
                    )
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
                await realtime.finish(turn_id)

        while True:
            try:
                event = parse_client_event(await websocket.receive_json())
            except (ValidationError, ValueError) as exc:
                await websocket.send_json(
                    server_event(
                        "error",
                        code="invalid_event",
                        message=str(exc),
                    )
                )
                continue

            if isinstance(event, PingEvent):
                await websocket.send_json(server_event("session.pong"))
            elif isinstance(event, CancelEvent):
                cancelled = await realtime.cancel()
                if cancelled:
                    await websocket.send_json(
                        server_event("turn.cancelled", turn_id=cancelled)
                    )
            elif isinstance(event, TextSubmitEvent):
                await realtime.start(
                    lambda turn_id: run_query(turn_id, event.question)
                )
            elif isinstance(event, ConfirmationEvent):
                await realtime.start(
                    lambda turn_id: run_resume(turn_id, event)
                )
    except WebSocketDisconnect:
        pass
    finally:
        await realtime.close()
        db.close()
