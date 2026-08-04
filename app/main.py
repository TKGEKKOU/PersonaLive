from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from starlette.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver

from agents.checkpoint import create_mysql_checkpointer
from agents.context_factory import build_agent_runner
from agents.service import PersonaAgentService
from app.database import Base, build_engine, build_session_factory, upgrade_persona_schema
from app.routers.agents import router as agents_router
from app.routers.asr import router as asr_router
from app.routers.documents import router as documents_router
from app.routers.embedding import router as embedding_router
from app.routers.eval import router as eval_router
from app.routers.integrations import router as integrations_router
from app.routers.live2d import router as live2d_router
from app.routers.mcp import router as mcp_router
from app.routers.persona_drafts import router as persona_drafts_router
from app.routers.messages import router as messages_router
from app.routers.personas import router as personas_router
from app.routers.plugins import router as plugins_router
from app.routers.rag import router as rag_router
from app.routers.realtime import router as realtime_router
from app.routers.settings import router as settings_router
from app.routers.skills import router as skills_router
from app.routers.system import router as system_router
from app.routers.tts import router as tts_router
from app.routers.voice import router as voice_router
from app.routers.voice_stream import router as voice_stream_router
from settings import Settings
from extensions.events import EVENT_MESSAGE, EventBus
from extensions.manager import PluginManager
from ingestion.status import get_system_status
from ingestion.local_embedding.resources import LocalEmbeddingResourceManager
from ingestion.embeddings import warm_managed_embedding
from integrations.config import onebot_runtime_config
from integrations.mcp.client import MCPManager
from integrations.onebot11.router import ImMessageRouter
from integrations.onebot11.ws_server import OneBotConnectionManager, router as onebot_ws_router
from persona.delete_service import PersonaDeletionService
from realtime.execution import ConversationExecutionRegistry
from voice.asr import build_asr_provider
from voice.asr.install import ASRResourceManager
from voice.asr.stream_client import WorkerStreamClient
from voice.tts.install import TTSResourceManager
from voice.tts.local_worker import LocalTTS
from voice.vad import build_vad

STATIC_DIR = (Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]) / "static"


class NoCacheStaticFiles(StaticFiles):
    """静态资源允许缓存但必须重新验证，避免 WebView2/浏览器启发式缓存导致改了不生效。"""

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store"
        return response


def create_app(initialize_database: bool = True) -> FastAPI:
    settings = Settings.load()
    checkpoint_resource = None

    async def warm_asr_worker() -> None:
        """Preload the local ASR worker in the background so the first voice
        utterance is not delayed by a cold model load. ASR must already be
        installed; failures are ignored (the start command retries)."""

        try:
            if not app.state.asr_resources.status().get("ready"):
                return
            provider = app.state.asr_provider_factory(Settings.load())
            manager = getattr(provider, "manager", None)
            if manager is not None:
                await manager.ensure_ready()
        except Exception:
            pass

    async def warm_tts_worker() -> None:
        """Preload the local TTS service in the background so the first reply
        voice is not delayed by model loading. TTS must already be installed
        and enabled; failures are ignored (synthesis retries on demand)."""

        try:
            if not app.state.tts_resources.status().get("ready"):
                return
            await asyncio.to_thread(app.state.tts_worker.warm_up)
        except Exception:
            pass

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # MCP 服务器启动时连接并注册工具：连接失败仅记录错误，不阻塞启动；
        # 工具注册发生在 workflow 懒构建之前，因此新技能即可引用 MCP 工具。
        app.state.mcp_manager = MCPManager(
            settings.project_root / "data" / "mcp_servers.json",
            allow_arbitrary_stdio=settings.mcp_allow_arbitrary_stdio,
        )
        await app.state.mcp_manager.connect_all(register=True)
        app.state.embedding_warmup_task = asyncio.create_task(
            asyncio.to_thread(warm_managed_embedding, settings)
        )
        if initialize_database:
            app.state.asr_warmup_task = asyncio.create_task(warm_asr_worker())
            app.state.tts_warmup_task = asyncio.create_task(warm_tts_worker())
        yield
        app.state.embedding_warmup_task.cancel()
        warmup = getattr(app.state, "asr_warmup_task", None)
        if warmup is not None:
            warmup.cancel()
        tts_warmup = getattr(app.state, "tts_warmup_task", None)
        if tts_warmup is not None:
            tts_warmup.cancel()
        app.state.plugin_manager.unload_all()
        app.state.tts_worker.stop_service()
        resource = getattr(app.state, "checkpoint_resource", None)
        if resource is not None:
            resource.close()

    app = FastAPI(title="PersonaLive", lifespan=lifespan)
    # 允许 file:// 启动页等本地来源通过 HTTP 轮询（服务仅绑定 127.0.0.1）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    engine = build_engine(settings)
    app.state.session_factory = build_session_factory(engine)
    app.state.persona_delete_service = PersonaDeletionService(settings)
    app.state.realtime_executions = ConversationExecutionRegistry()
    app.state.asr_provider_factory = build_asr_provider
    app.state.asr_resources = ASRResourceManager(settings.project_root)
    app.state.vad_factory = build_vad
    app.state.asr_stream_client_factory = WorkerStreamClient
    app.state.embedding_resources = LocalEmbeddingResourceManager(settings.project_root)
    app.state.tts_resources = TTSResourceManager(settings.project_root)
    app.state.tts_worker = LocalTTS(
        app.state.tts_resources.runtime_path,
        app.state.tts_resources.model_dir,
        use_gpu=app.state.tts_resources.config()["use_gpu"],
    )
    app.state.tts_factory = lambda: app.state.tts_worker
    if initialize_database:
        Base.metadata.create_all(engine)
        upgrade_persona_schema(engine)
        # 生产环境使用 MySQL 持久化 LangGraph 检查点：会话状态（对话历史、中断点、Worker 结果）
        # 全部落库，服务重启后可按 thread_id 恢复；langgraph-checkpoint-mysql 实现了
        # BaseCheckpointSaver 接口，对上层 PersonaAgentService 透明。
        checkpoint_resource = create_mysql_checkpointer(settings)
        app.state.checkpoint_resource = checkpoint_resource
        app.state.agent_service = PersonaAgentService(checkpoint_resource.saver)
    else:
        # 无数据库（测试/演示）时退化为内存检查点，行为一致但重启即失。
        app.state.agent_service = PersonaAgentService(MemorySaver())
    # PersonaAgentService 是人设多 Agent（supervisor + 四类 Worker）的应用层入口：
    # 对外只暴露 query / resume，内部由 LangGraph 图执行，thread_id = persona_id:conversation_id。
    app.state.event_bus = EventBus()
    app.state.onebot = OneBotConnectionManager(
        lambda: onebot_runtime_config(settings.project_root)
    )
    # IM 消息路由：OneBot（QQ）等外部渠道消息经 EventBus 广播到这里，统一转成
    # PersonaAgentService 的一轮对话；消息与 Agent 解耦，渠道扩展不触碰 Agent 逻辑。
    app.state.im_router = ImMessageRouter(
        app.state.agent_service,
        app.state.session_factory,
        settings.project_root / "data" / "im_bindings.json",
        settings.project_root / "data" / "integrations.json",
    )
    app.state.event_bus.subscribe(EVENT_MESSAGE, app.state.im_router.handle)
    # 插件管理器注入 agent_runner，使插件能安全地触发同一套 Agent 流程；
    # 插件自身只感知 EventBus 与受限 runner，不直接持有数据库或图对象。
    app.state.plugin_manager = PluginManager(
        settings.project_root / "plugins",
        settings.project_root / "data",
        app.state.event_bus,
        agent_runner=build_agent_runner(app.state.session_factory, app.state.agent_service),
    )
    app.state.plugin_manager.load_all()
    app.include_router(agents_router)
    app.include_router(asr_router)
    app.include_router(onebot_ws_router)
    app.include_router(integrations_router)
    app.include_router(live2d_router)
    app.include_router(messages_router)
    app.include_router(mcp_router)
    app.include_router(personas_router)
    app.include_router(documents_router)
    app.include_router(embedding_router)
    app.include_router(eval_router)
    app.include_router(persona_drafts_router)
    app.include_router(plugins_router)
    app.include_router(skills_router)
    app.include_router(rag_router)
    app.include_router(realtime_router)
    app.include_router(settings_router)
    app.include_router(system_router)
    app.include_router(tts_router)
    app.include_router(voice_router)
    app.include_router(voice_stream_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "workspace_id": settings.workspace_id}

    @app.get("/api/status")
    def status() -> dict:
        return get_system_status()

    @app.get("/api/launcher/progress")
    def launcher_progress() -> dict:
        """桌面启动页轮询用的启动进度（由桌面进程注入，浏览器端无则返回空进度）。"""
        fn = getattr(app.state, "launcher_progress", None)
        if fn is None:
            return {"starting": False, "done": False, "ok": None, "error": "", "percent": 0, "steps": []}
        return fn()

    @app.get("/", include_in_schema=False)
    def web_workbench() -> RedirectResponse:
        return RedirectResponse(url="/static/index.html")

    live2d_dir = settings.project_root / "data" / "live2d"
    live2d_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/live2d-assets", StaticFiles(directory=live2d_dir), name="live2d-assets")
    app.mount("/static", NoCacheStaticFiles(directory=STATIC_DIR), name="static")

    return app
