import asyncio
import selectors
import sys

if sys.platform == "win32":
    class _SelectorPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            return asyncio.SelectorEventLoop(selectors.SelectSelector())

    asyncio.set_event_loop_policy(_SelectorPolicy())

import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.utils.logger import setup_logging
from app.db.session import init_tables, close_engine
from app.db.postgres import close_pool
from app.db.milvus import connect_milvus, init_collection
from app.db.redis import get_redis, close_redis
from app.db.checkpoint import get_checkpointer, close_checkpointer
from app.skills.registry import SkillRegistry
from app.services.cleanup_service import start_cleanup_task, stop_cleanup_task
from app.agents.graph import build_graph, build_graph_with_checkpointer, set_agent_graph
from app.api import chat, template, skill, mcp, file, settings as settings_router, intent_display


@asynccontextmanager
async def lifespan(app: FastAPI):
    from loguru import logger
    setup_logging()

    # 数据库初始化 — 各自独立 try/except，一个失败不阻塞整体启动
    try:
        await init_tables()
    except Exception as e:
        logger.error(f"PostgreSQL 初始化失败: {e}")

    try:
        checkpointer = await get_checkpointer()
        set_agent_graph(build_graph_with_checkpointer(checkpointer))
        logger.info("Agent graph 初始化完成 (with checkpoint)")
    except Exception as e:
        logger.error(f"Checkpoint 初始化失败: {e}")
        set_agent_graph(build_graph())  # 降级：不使用 checkpoint

    try:
        connect_milvus()
        init_collection()
    except Exception as e:
        logger.error(f"Milvus 初始化失败: {e}")

    try:
        await get_redis()
    except Exception as e:
        logger.error(f"Redis 初始化失败: {e}")

    # LangSmith 初始化：优先读 Redis 缓存，回退到 .env
    try:
        redis_client = await get_redis()
        stored = await redis_client.get("config:langsmith_enabled")
        if stored is not None:
            os.environ["LANGCHAIN_TRACING_V2"] = stored
        else:
            if settings.LANGCHAIN_TRACING_V2 == "true" and settings.LANGCHAIN_API_KEY:
                os.environ.setdefault("LANGCHAIN_TRACING_V2", settings.LANGCHAIN_TRACING_V2)
                os.environ.setdefault("LANGCHAIN_API_KEY", settings.LANGCHAIN_API_KEY)
                os.environ.setdefault("LANGCHAIN_PROJECT", settings.LANGCHAIN_PROJECT)
    except Exception:
        pass

    if os.environ.get("LANGCHAIN_TRACING_V2") == "true":
        logger.info(f"LangSmith tracing enabled for project: {os.environ.get('LANGCHAIN_PROJECT', '')}")

    try:
        await SkillRegistry.refresh()
    except Exception as e:
        logger.error(f"技能缓存刷新失败: {e}")

    try:
        from app.mcp.mcp_manager import GlobalMcpManager
        await GlobalMcpManager.init()
        # SSE 服务预热：后台建连，避免首次工具调用触发冷启动
        asyncio.create_task(GlobalMcpManager.warmup_sse())
    except Exception as e:
        logger.error(f"MCP Manager 初始化失败: {e}")

    # 启动定时清理任务
    start_cleanup_task()

    yield
    # 关闭时
    await stop_cleanup_task()
    try:
        await close_checkpointer()
    except Exception:
        pass
    try:
        await close_engine()
    except Exception:
        pass
    try:
        await close_pool()
    except Exception:
        pass
    try:
        await close_redis()
    except Exception:
        pass


app = FastAPI(
    title="越群山智能生活助手",
    description="基于 LangGraph 的多 Agent 智能生活助手 API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat.router, prefix="/api")
app.include_router(template.router, prefix="/api")
app.include_router(skill.router, prefix="/api")
app.include_router(mcp.router, prefix="/api")
app.include_router(file.router, prefix="/api")
app.include_router(settings_router.router, prefix="/api")
app.include_router(intent_display.router, prefix="/api")


@app.get("/")
async def root():
    return {"service": "越群山智能生活助手", "version": "0.1.0"}


if __name__ == "__main__":
    import uvicorn
    import uvicorn.loops.asyncio as _uv_asyncio
    _uv_asyncio.asyncio_loop_factory = lambda use_subprocess=False: asyncio.SelectorEventLoop
    uvicorn.run(app, host=settings.APP_HOST, port=settings.APP_PORT)
