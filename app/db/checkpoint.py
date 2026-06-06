from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from loguru import logger

from app.config import settings


class CheckpointRepository:
    """LangGraph Checkpoint 仓库，封装所有 checkpoint 管理操作。"""

    def __init__(self, conn_string: str, schema: str):
        self._conn_string = conn_string
        self._schema = schema
        self._ctx = None
        self._saver: AsyncPostgresSaver | None = None
        self._maint_engine = None  # 维护操作专用引擎，与业务连接池隔离

    # ---- 生命周期 -----------------------------------------------------------

    async def initialize(self) -> AsyncPostgresSaver:
        """初始化连接池、建表、扩展 schema，返回 AsyncPostgresSaver 实例。"""
        if self._saver is not None:
            return self._saver
        self._ctx = AsyncPostgresSaver.from_conn_string(self._conn_string)
        self._saver = await self._ctx.__aenter__()
        await self._saver.setup()
        await self._extend_schema()
        logger.info("LangGraph checkpoint 初始化完成 (PostgreSQL)")
        return self._saver

    async def close(self) -> None:
        """关闭连接池。"""
        if self._ctx:
            await self._ctx.__aexit__(None, None, None)
            self._ctx = None
            self._saver = None
        if self._maint_engine:
            await self._maint_engine.dispose()
            self._maint_engine = None
        logger.info("LangGraph checkpoint 已关闭")

    @property
    def saver(self) -> AsyncPostgresSaver | None:
        return self._saver

    # ---- 维护引擎（独立于业务连接池）--------------------------------------------

    async def _get_maint_engine(self):
        """懒加载一个独立的引擎用于 checkpoint 维护操作，不占用业务连接池。"""
        if self._maint_engine is None:
            from app.config import settings
            from sqlalchemy.ext.asyncio import create_async_engine
            maint_dsn = (
                f"postgresql+asyncpg://{settings.PG_USER}:{settings.PG_PASSWORD}"
                f"@{settings.PG_HOST}:{settings.PG_PORT}/{settings.PG_DATABASE}"
            )
            self._maint_engine = create_async_engine(
                maint_dsn,
                pool_size=2,
                max_overflow=0,
                pool_pre_ping=True,
                connect_args={"server_settings": {"search_path": self._schema}},
            )
        return self._maint_engine

    # ---- Schema -------------------------------------------------------------

    async def _extend_schema(self) -> None:
        from sqlalchemy import text

        stmts = [
            f"ALTER TABLE {self._schema}.checkpoints ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()",
            f"ALTER TABLE {self._schema}.checkpoints ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP",
            f"ALTER TABLE {self._schema}.checkpoint_writes ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()",
            f"ALTER TABLE {self._schema}.checkpoint_writes ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP",
            f"ALTER TABLE {self._schema}.checkpoint_blobs ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()",
            f"ALTER TABLE {self._schema}.checkpoint_blobs ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP",
            f"ALTER TABLE {self._schema}.checkpoint_blobs ADD COLUMN IF NOT EXISTS blob_size INTEGER",
        ]
        engine = await self._get_maint_engine()
        async with engine.begin() as conn:
            for stmt in stmts:
                try:
                    await conn.execute(text(stmt))
                except Exception:
                    pass

    # ---- 状态读写 -----------------------------------------------------------

    async def get_state(self, config: dict) -> dict | None:
        """读取指定 thread 的 checkpoint 状态。"""
        if self._saver is None:
            return None
        snapshot = await self._saver.aget_tuple(config)
        if snapshot and snapshot.config:
            return snapshot.config
        return None

    async def update_state(self, config: dict, values: dict) -> None:
        """更新指定 thread 的 checkpoint 状态。"""
        if self._saver is None:
            return
        await self._saver.aput(config, values)

    # ---- 删除 ---------------------------------------------------------------

    async def delete_thread(self, thread_id: str) -> None:
        """删除指定 thread_id 的所有 checkpoint 数据。"""
        from sqlalchemy import text

        engine = await self._get_maint_engine()
        async with engine.begin() as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                await conn.execute(
                    text(f"DELETE FROM {self._schema}.{table} WHERE thread_id = :tid"),
                    {"tid": thread_id},
                )
        logger.info(f"Checkpoint 数据已清理: thread_id={thread_id}")

    # ---- 维护 ---------------------------------------------------------------

    async def cleanup_old_versions(self, thread_id: str, keep: int = 50) -> int:
        """每个 thread 保留最近 keep 个 checkpoint，删除多余旧记录。返回删除数。"""
        from sqlalchemy import text

        engine = await self._get_maint_engine()
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    f"SELECT checkpoint_id FROM {self._schema}.checkpoints "
                    "WHERE thread_id = :tid ORDER BY created_at DESC "
                    "OFFSET :keep"
                ),
                {"tid": thread_id, "keep": keep},
            )
            old_ids = [row[0] for row in result.all()]
            if not old_ids:
                return 0

            await conn.execute(
                text(
                    f"DELETE FROM {self._schema}.checkpoint_writes "
                    "WHERE thread_id = :tid AND checkpoint_id = ANY(:ids)"
                ),
                {"tid": thread_id, "ids": old_ids},
            )
            await conn.execute(
                text(
                    f"DELETE FROM {self._schema}.checkpoints "
                    "WHERE thread_id = :tid AND checkpoint_id = ANY(:ids)"
                ),
                {"tid": thread_id, "ids": old_ids},
            )
        logger.info(f"Checkpoint 清理完成: thread_id={thread_id}, 删除了 {len(old_ids)} 个旧版本")
        return len(old_ids)

    async def cleanup_expired_blobs(self, ttl_days: int = 7) -> None:
        """删除超过 ttl_days 的 checkpoint_blobs。"""
        from sqlalchemy import text

        engine = await self._get_maint_engine()
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    f"DELETE FROM {self._schema}.checkpoint_blobs "
                    f"WHERE created_at < NOW() - INTERVAL '{ttl_days} days'"
                )
            )
        logger.info(f"Checkpoint blob TTL 清理完成: {ttl_days} 天")


# ---- 模块级单例 + 便捷函数（向后兼容）----------------------------------------

_repo: CheckpointRepository | None = None


def _build_conn_string() -> str:
    return (
        f"postgresql://{settings.PG_USER}:{settings.PG_PASSWORD}"
        f"@{settings.PG_HOST}:{settings.PG_PORT}/{settings.PG_DATABASE}"
        f"?options=-c%20search_path%3D{settings.PG_SCHEMA}"
    )


async def get_checkpoint_repo() -> CheckpointRepository:
    global _repo
    if _repo is None:
        _repo = CheckpointRepository(_build_conn_string(), settings.PG_SCHEMA)
        await _repo.initialize()
    return _repo


async def get_checkpointer() -> AsyncPostgresSaver:
    """向后兼容：获取 AsyncPostgresSaver 实例。"""
    repo = await get_checkpoint_repo()
    if repo.saver is None:
        await repo.initialize()
    return repo.saver


async def delete_checkpoint_thread(thread_id: str) -> None:
    repo = await get_checkpoint_repo()
    await repo.delete_thread(thread_id)


async def cleanup_checkpoints(thread_id: str, keep: int = 50) -> int:
    repo = await get_checkpoint_repo()
    return await repo.cleanup_old_versions(thread_id, keep)


async def cleanup_expired_blobs(ttl_days: int = 7) -> None:
    repo = await get_checkpoint_repo()
    await repo.cleanup_expired_blobs(ttl_days)


async def close_checkpointer() -> None:
    global _repo
    if _repo:
        await _repo.close()
        _repo = None
