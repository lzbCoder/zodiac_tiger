import json
from sqlalchemy import select
from loguru import logger

from app.db.redis import get_redis
from app.db.session import get_db_session
from app.models.skill import Skill

SKILL_CACHE_KEY = "skill:all"


class SkillRegistry:
    """技能注册中心：从 PostgreSQL 加载技能，缓存到 Redis。"""

    @staticmethod
    async def refresh() -> None:
        """重新加载所有启用技能到 Redis 缓存。"""
        async with get_db_session() as session:
            stmt = (
                select(
                    Skill.id,
                    Skill.name,
                    Skill.desc,
                    Skill.skill_type,
                    Skill.mcp_id,
                    Skill.timeout,
                )
                .where(Skill.status == 1)
                .order_by(Skill.id)
            )
            result = await session.execute(stmt)
            rows = result.all()

        skills = [
            {
                "id": r.id,
                "name": r.name,
                "desc": r.desc,
                "skill_type": r.skill_type,
                "mcp_id": r.mcp_id,
                "timeout": r.timeout,
            }
            for r in rows
        ]
        r = await get_redis()
        await r.set(SKILL_CACHE_KEY, json.dumps(skills, ensure_ascii=False))
        logger.info(f"技能缓存已刷新，共 {len(skills)} 个启用技能")

    @staticmethod
    async def get_available() -> list[dict]:
        """获取当前所有可用技能（从 Redis 读取）。Redis 不可用时降级返回空列表。"""
        try:
            r = await get_redis()
            data = await r.get(SKILL_CACHE_KEY)
        except Exception as e:
            logger.warning(f"Redis 读取技能缓存失败，降级为空列表: {e}")
            return []
        if data is None:
            return []
        return json.loads(data)
