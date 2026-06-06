from fastapi import APIRouter
from pydantic import BaseModel
import os
from loguru import logger
from app.db.redis import get_redis
from app.utils.response import success, fail
from langsmith._internal import _context as langsmith_context

router = APIRouter(tags=["系统设置"])


class LangSmithStatus(BaseModel):
    enabled: bool


@router.get("/settings/langsmith")
async def get_langsmith():
    try:
        r = await get_redis()
        stored = await r.get("config:langsmith_enabled")
        enabled = stored == "true" if stored is not None else os.environ.get("LANGCHAIN_TRACING_V2") == "true"
        return success({
            "enabled": enabled,
            "project": os.environ.get("LANGCHAIN_PROJECT", ""),
        })
    except Exception as e:
        return fail(message=str(e))


@router.post("/settings/langsmith")
async def toggle_langsmith(body: LangSmithStatus):
    try:
        val = "true" if body.enabled else "false"
        r = await get_redis()
        await r.set("config:langsmith_enabled", val)
        os.environ["LANGCHAIN_TRACING_V2"] = val
        # 覆盖 lru_cache 缓存的 env var 读取（优先级高于 env var）
        langsmith_context._GLOBAL_TRACING_ENABLED = body.enabled
        logger.info(f"LangSmith tracing {'enabled' if body.enabled else 'disabled'}")
        return success({"enabled": body.enabled})
    except Exception as e:
        return fail(message=str(e))
