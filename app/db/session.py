from contextlib import asynccontextmanager
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from loguru import logger

from app.config import settings
from app.models.base import Base

_engine = None
_sessionmaker = None
_INIT_SQL = Path(__file__).parent / "init_db.sql"


def _get_database_url() -> str:
    import urllib.parse
    return (
        f"postgresql+asyncpg://{settings.PG_USER}:{urllib.parse.quote_plus(settings.PG_PASSWORD)}"
        f"@{settings.PG_HOST}:{settings.PG_PORT}/{settings.PG_DATABASE}"
    )


async def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            _get_database_url(),
            pool_size=5,        # 池中 5 个长连接
            max_overflow=20,    # 最多临时创建 20 个
            pool_timeout=30,    # 获取不到时最多等 30 秒
            pool_pre_ping=True,
        )
    return _engine


async def get_sessionmaker():
    global _sessionmaker
    if _sessionmaker is None:
        engine = await get_engine()
        _sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncSession:
    sm = await get_sessionmaker()
    return sm()


@asynccontextmanager
async def get_db_session():
    """异常安全的 session 上下文管理器，自动 close。"""
    sm = await get_sessionmaker()
    session = sm()
    try:
        yield session
    finally:
        await session.close()


def _split_sql(sql: str) -> list[str]:
    """将多语句 SQL 脚本拆分为单条语句，跳过注释行和空行。"""
    statements = []
    for raw in sql.split(";"):
        stmt = raw.strip()
        if not stmt:
            continue
        # 跳过纯注释行（以 -- 开头）
        lines = [l for l in stmt.split("\n") if l.strip() and not l.strip().startswith("--")]
        if not lines:
            continue
        statements.append(stmt)
    return statements


async def init_tables() -> None:
    """建表 + 建索引。

    1. SQLAlchemy create_all：创建 ORM 表（IF NOT EXISTS 幂等）
    2. 执行 init_db.sql：补充创建索引（IF NOT EXISTS 幂等，双重保险）
    """
    engine = await get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sql = _INIT_SQL.read_text(encoding="utf-8")
    async with engine.begin() as conn:
        for stmt in _split_sql(sql):
            await conn.execute(text(stmt))
    logger.info("数据库表初始化完成 (SQLAlchemy create_all + init_db.sql)")
async def close_engine() -> None:
    global _engine, _sessionmaker
    if _engine:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None
