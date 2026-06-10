"""Milvus 过期记忆定时清理服务。"""

import asyncio
import time

from loguru import logger

from app.db.milvus import get_episodic_collection

_CLEANUP_INTERVAL_SECONDS = 3600  # 每小时执行一次

_cleanup_task: asyncio.Task | None = None


async def _cleanup_expired_memories():
    """定时清理 episodic_memory 中过期的记忆。"""
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
