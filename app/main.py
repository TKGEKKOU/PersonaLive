from contextlib import asynccontextmanager
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver

from agents.checkpoint import create_mysql_checkpointer
from agents.service import PersonaAgentService
from app.database import Base, build_engine, build_session_factory, upgrade_persona_schema
from app.routers.agents import router as agents_router
from app.routers.asr import router as asr_router
from app.routers.documents import router as documents_router
from app.routers.persona_drafts import router as persona_drafts_router
from app.routers.messages import router as messages_router
from app.routers.personas import router as personas_router
from app.routers.rag import router as rag_router
from app.routers.realtime import router as realtime_router
from app.routers.settings import router as settings_router
from app.routers.tts import router as tts_router
from app.routers.voice import router as voice_router
from settings import Settings
from ingestion.status import get_system_status
from persona.delete_service import PersonaDeletionService
from realtime.execution import ConversationExecutionRegistry
from voice.asr import build_asr_provider
from voice.asr.install import ASRResourceManager
from voice.tts.install import TTSResourceManager
from voice.tts.local_worker import LocalTTS

STATIC_DIR = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]) / "static"


def create_app(initialize_database: bool = True) -> FastAPI:
    settings = Settings.load()
    checkpoint_resource = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        resource = getattr(app.state, "checkpoint_resource", None)
        if resource is not None:
            resource.close()

    app = FastAPI(title="PersonaLive", lifespan=lifespan)
    engine = build_engine(settings)
    app.state.session_factory = build_session_factory(engine)
    app.state.persona_delete_service = PersonaDeletionService(settings)
    app.state.realtime_executions = ConversationExecutionRegistry()
    app.state.asr_provider_factory = build_asr_provider
    app.state.asr_resources = ASRResourceManager(settings.project_root)
    app.state.tts_resources = TTSResourceManager(settings.project_root)
    app.state.tts_factory = lambda: LocalTTS(
        app.state.tts_resources.runtime_path,
        app.state.tts_resources.model_dir,
        use_gpu=app.state.tts_resources.config()["use_gpu"],
    )
    if initialize_database:
        Base.metadata.create_all(engine)
        upgrade_persona_schema(engine)
        checkpoint_resource = create_mysql_checkpointer(settings)
        app.state.checkpoint_resource = checkpoint_resource
        app.state.agent_service = PersonaAgentService(checkpoint_resource.saver)
    else:
        app.state.agent_service = PersonaAgentService(MemorySaver())
    app.include_router(agents_router)
    app.include_router(asr_router)
    app.include_router(messages_router)
    app.include_router(personas_router)
    app.include_router(documents_router)
    app.include_router(persona_drafts_router)
    app.include_router(rag_router)
    app.include_router(realtime_router)
    app.include_router(settings_router)
    app.include_router(tts_router)
    app.include_router(voice_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "workspace_id": settings.workspace_id}

    @app.get("/api/status")
    def status() -> dict[str, str]:
        return get_system_status()

    @app.get("/", include_in_schema=False)
    def web_workbench() -> RedirectResponse:
        return RedirectResponse(url="/static/index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
