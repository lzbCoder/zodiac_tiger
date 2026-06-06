import redis.asyncio as aioredis
from loguru import logger

from app.config import settings

_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _client
    if _client is not None:
        try:
            await _client.ping()
            logger.info("Redis 连接成功")
            return _client
        except Exception:
            await _client.close()
            _client = None

    redis_url = settings.REDIS_URI
    if redis_url.startswith("http://"):
        redis_url = redis_url.replace("http://", "redis://", 1)
    elif redis_url.startswith("https://"):
        redis_url = redis_url.replace("https://", "rediss://", 1)
    _client = aioredis.from_url(
        redis_url,
        password=settings.REDIS_PASSWORD,
        decode_responses=True,
    )
    await _client.ping()
    logger.info("Redis 连接成功")
    return _client


async def close_redis() -> None:
    global _client
    if _client:
        await _client.close()
        _client = None
