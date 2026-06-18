import json
from sqlalchemy import select
from loguru import logger

from app.db.redis import get_redis
from app.db.session import get_db_session
from app.models.skill_info import SkillInfo

SKILL_CACHE_KEY = "skill:all"


class SkillRegistry:
    """技能注册中心：从 PostgreSQL 加载技能，缓存到 Redis。"""

    @staticmethod
    async def refresh() -> None:
        async with get_db_session() as session:
            rows = (await session.execute(
                select(
                    SkillInfo.skill_key,
                    SkillInfo.display_name,
                    SkillInfo.skill_desc,
                    SkillInfo.enable_status,
                )
                .where(SkillInfo.enable_status == 1)
                .order_by(SkillInfo.sort, SkillInfo.id)
            )).all()

        skills = [
            {
                "skill_key": r.skill_key,
                "display_name": r.display_name,
                "skill_desc": r.skill_desc,
                "enable_status": r.enable_status,
            }
            for r in rows
        ]
        r = await get_redis()
        await r.set(SKILL_CACHE_KEY, json.dumps(skills, ensure_ascii=False))
        logger.info(f"技能缓存已刷新，共 {len(skills)} 个启用技能")

    @staticmethod
    async def get_available() -> list[dict]:
        try:
            r = await get_redis()
            data = await r.get(SKILL_CACHE_KEY)
        except Exception as e:
            logger.warning(f"Redis 读取技能缓存失败，降级为空列表: {e}")
            return []
        if data is None:
            return []
        return json.loads(data)
