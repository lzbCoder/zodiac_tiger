"""全局技能管理器：meta 内存缓存 + Agent 运行时注入"""

from loguru import logger


class GlobalSkillManager:
    """
    管理所有本地技能的激活态缓存。
    三层懒加载：discovery（DB）→ activation（SKILL.md 磁盘）→ execution（按需）。
    """

    _meta_cache: dict[str, dict] = {}  # skill_key → 已激活的 meta dict

    @classmethod
    async def init(cls) -> None:
        """启动时预热：激活所有 enable_status=1 的技能 meta 缓存。"""
        from sqlalchemy import select
        from app.db.session import get_db_session
        from app.models.skill_info import SkillInfo
        from app.services.skill_md_service import activate_skill

        async with get_db_session() as session:
            rows = (await session.execute(
                select(SkillInfo.skill_key, SkillInfo.folder_abs_path)
                .where(SkillInfo.enable_status == 1)
            )).all()

        for r in rows:
            try:
                meta = await activate_skill(r.skill_key, r.folder_abs_path)
                if meta:
                    cls._meta_cache[r.skill_key] = meta
            except Exception as e:
                logger.warning(f"[SkillManager] 预热技能 {r.skill_key} 失败: {e}")

        logger.info(f"Skill Manager 初始化完成，预热 {len(cls._meta_cache)} 个技能")

    @classmethod
    async def get_skills_for_agent(cls, agent_code: str) -> list[dict]:
        """
        返回指定 Agent 绑定的、已启用的技能 meta 列表：
        [{skill_key, skill_name, system_prompt, bind_tools}, ...]
        """
        from sqlalchemy import select
        from app.db.session import get_db_session
        from app.models.agent_skill_rel import AgentSkillRel
        from app.models.skill_info import SkillInfo
        from app.services.skill_md_service import activate_skill

        async with get_db_session() as session:
            rows = (await session.execute(
                select(SkillInfo.skill_key, SkillInfo.display_name, SkillInfo.folder_abs_path)
                .join(AgentSkillRel, AgentSkillRel.skill_key == SkillInfo.skill_key)
                .where(
                    AgentSkillRel.agent_code == agent_code,
                    SkillInfo.enable_status == 1,
                )
            )).all()

        result = []
        for r in rows:
            if r.skill_key not in cls._meta_cache:
                try:
                    meta = await activate_skill(r.skill_key, r.folder_abs_path)
                    if meta:
                        cls._meta_cache[r.skill_key] = meta
                except Exception as e:
                    logger.warning(f"[SkillManager] 激活技能 {r.skill_key} 失败: {e}")
                    continue

            cached = cls._meta_cache.get(r.skill_key, {})
            result.append({
                "skill_key": r.skill_key,
                "display_name": r.display_name,
                "system_prompt": cached.get("system_prompt"),
                "bind_tools": cached.get("bind_tools"),
            })

        return result

    @classmethod
    def invalidate(cls, skill_key: str) -> None:
        cls._meta_cache.pop(skill_key, None)
