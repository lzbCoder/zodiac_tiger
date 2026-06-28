"""客户端访问记录服务：去重 + 双写（loguru 文件 + PostgreSQL）。

去重策略（满足"按访问会话/IP 首次记录"）：
同一 client_ip（有 session_id 时优先按 session_id）在 ACCESS_LOG_DEDUP_TTL 秒内
只记录一次，避免每次接口调用都落库造成噪音与膨胀。去重用 Redis SET NX EX 实现；
Redis 不可用时采取"失败即记录"策略，保证审计完整性优先。
"""
from loguru import logger

from app.config import settings
from app.db.redis import get_redis
from app.db.session import get_db_session
from app.models.access_log import AccessLog


async def _is_first_visit(dedup_key: str) -> bool:
    """该 key 是否为去重窗口内首次出现。Redis 异常时返回 True（失败即记录）。"""
    try:
        r = await get_redis()
        # nx=True：仅当 key 不存在时写入并返回 True；已存在返回 None
        ok = await r.set(
            f"access:seen:{dedup_key}", "1",
            ex=settings.ACCESS_LOG_DEDUP_TTL, nx=True,
        )
        return bool(ok)
    except Exception as e:
        logger.warning(f"访问去重 Redis 失败，按首次处理: {e}")
        return True


async def record_access(
    *,
    client_ip: str,
    method: str,
    path: str,
    status_code: int,
    cost_ms: int,
    user_agent: str = "",
    referer: str = "",
    session_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """记录一次访问（已通过去重）。文件日志必落；DB 失败不影响请求。"""
    dedup_key = session_id or client_ip
    if not await _is_first_visit(dedup_key):
        return

    # 1) 文件日志（复用现有 loguru 体系，写入 app.log）
    logger.info(
        f"[ACCESS] ip={client_ip} method={method} path={path} "
        f"status={status_code} cost_ms={cost_ms} session={session_id or '-'} "
        f"ua=\"{(user_agent or '')[:200]}\""
    )

    # 2) 结构化落库（供查询/统计/风控）；失败仅告警，绝不影响主流程
    try:
        async with get_db_session() as session:
            session.add(AccessLog(
                client_ip=client_ip,
                session_id=session_id,
                user_id=user_id,
                method=method,
                path=path[:500] if path else None,
                status_code=status_code,
                user_agent=user_agent or None,
                referer=referer[:500] if referer else None,
                cost_ms=cost_ms,
            ))
            await session.commit()
    except Exception as e:
        logger.warning(f"访问记录落库失败: {e}")
