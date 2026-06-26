"""定时清理服务：episodic 过期记忆删除 + procedural 程序记忆衰减。"""

import asyncio
import time
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import update, and_

from app.db.milvus import get_episodic_collection
from app.db.session import get_db_session
from app.models.procedural_memory import ProceduralMemory
from app.services import task_service
from app.config import settings

_CLEANUP_INTERVAL_SECONDS = 3600  # 每小时执行一次

_cleanup_task: asyncio.Task | None = None


async def _decay_procedural_memories():
    """对长期未命中的程序规则衰减 score，低于阈值则置失效(status=0)。"""
    cutoff = datetime.now() - timedelta(days=settings.PROCEDURAL_DECAY_DAYS)
    async with get_db_session() as session:
        # 超期未命中 → score *= 衰减因子
        await session.execute(
            update(ProceduralMemory)
            .where(and_(ProceduralMemory.status == 1,
                        ProceduralMemory.last_hit_at.is_not(None),
                        ProceduralMemory.last_hit_at < cutoff))
            .values(score=ProceduralMemory.score * settings.PROCEDURAL_DECAY_FACTOR,
                    updated_at=datetime.now())
        )
        # 低分 → 失效
        result = await session.execute(
            update(ProceduralMemory)
            .where(and_(ProceduralMemory.status == 1,
                        ProceduralMemory.score < settings.PROCEDURAL_MIN_SCORE))
            .values(status=0, updated_at=datetime.now())
        )
        await session.commit()
    if result.rowcount:
        logger.info(f"程序记忆衰减失效: {result.rowcount} 条")


async def _cleanup_expired_memories():
    """定时清理 episodic_memory 过期记忆 + 程序记忆衰减。"""
    while True:
        try:
            await asyncio.sleep(_CLEANUP_INTERVAL_SECONDS)
            collection = get_episodic_collection()
            now_ms = int(time.time() * 1000)
            expr = f"expires_at > 0 AND expires_at < {now_ms}"
            count = await asyncio.to_thread(lambda: collection.delete(expr))
            if count > 0:
                logger.info(f"清理过期记忆: {count} 条")
        except Exception as e:
            logger.warning(f"清理过期记忆失败: {e}")
        try:
            await _decay_procedural_memories()
        except Exception as e:
            logger.warning(f"程序记忆衰减失败: {e}")
        try:
            archived = await task_service.archive_inactive_tasks(settings.TASK_ARCHIVE_INACTIVE_DAYS)
            if archived:
                logger.info(f"长任务自动归档: {archived} 条 (超过 {settings.TASK_ARCHIVE_INACTIVE_DAYS} 天无活动)")
        except Exception as e:
            logger.warning(f"长任务自动归档失败: {e}")


def start_cleanup_task():
    """启动后台定时清理任务。"""
    global _cleanup_task
    if _cleanup_task is not None and not _cleanup_task.done():
        logger.debug("清理任务已在运行，跳过")
        return
    _cleanup_task = asyncio.create_task(_cleanup_expired_memories())
    logger.info(f"过期记忆清理任务已启动 (间隔={_CLEANUP_INTERVAL_SECONDS}s)")


async def stop_cleanup_task():
    """停止后台定时清理任务。"""
    global _cleanup_task
    if _cleanup_task is None or _cleanup_task.done():
        return
    _cleanup_task.cancel()
    try:
        await _cleanup_task
    except asyncio.CancelledError:
        pass
    _cleanup_task = None
    logger.info("过期记忆清理任务已停止")
