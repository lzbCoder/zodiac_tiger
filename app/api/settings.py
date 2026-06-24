from fastapi import APIRouter
from pydantic import BaseModel
import os
import json
from loguru import logger
from app.db.redis import get_redis
from app.utils.response import success, fail
from app.utils.logger import get_log_config, reconfigure_logging
from langsmith._internal import _context as langsmith_context

router = APIRouter(tags=["系统设置"])

_LOG_REDIS_KEY = "config:log"


class LangSmithStatus(BaseModel):
    enabled: bool


class LogConfig(BaseModel):
    level: str
    rotationSize: int
    retentionDays: int


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


def _log_payload(cfg: dict) -> dict:
    return {
        "level": cfg["level"],
        "rotationSize": cfg["rotation_mb"],
        "retentionDays": cfg["retention_days"],
    }


@router.get("/settings/log")
async def get_log():
    """返回当前生效的日志配置（启动时已从 Redis 恢复，内存值即实际值）。"""
    try:
        return success(_log_payload(get_log_config()))
    except Exception as e:
        return fail(message=str(e))


@router.post("/settings/log")
async def update_log(body: LogConfig):
    """运行时重配日志：级别/大小即时生效，保留天数下次切割生效；并持久化到 Redis。"""
    try:
        cfg = reconfigure_logging(
            level=body.level,
            rotation_mb=body.rotationSize,
            retention_days=body.retentionDays,
        )
        try:
            r = await get_redis()
            await r.set(_LOG_REDIS_KEY, json.dumps(cfg))
        except Exception as e:
            logger.warning(f"日志配置持久化失败: {e}")
        return success(_log_payload(cfg))
    except Exception as e:
        return fail(message=str(e))
