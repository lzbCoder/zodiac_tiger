"""技能缓存失效辅助：将指定技能从 Redis 中删除。"""

from app.skills.registry import SkillRegistry


class GlobalSkillManager:

    @staticmethod
    async def invalidate(skill_key: str) -> None:
        await SkillRegistry.delete_skill(skill_key)
