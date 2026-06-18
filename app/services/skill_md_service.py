"""SKILL.md 结构化元数据查询"""

from sqlalchemy import select

from app.db.session import get_db_session
from app.models.skill_md_meta import SkillMdMeta


async def get_cached_meta(skill_key: str) -> dict | None:
    async with get_db_session() as session:
        row = (await session.execute(
            select(SkillMdMeta).where(SkillMdMeta.skill_key == skill_key)
        )).scalar_one_or_none()
    return _row_to_dict(row) if row else None


def _row_to_dict(r: SkillMdMeta) -> dict:
    return {
        "skill_key": r.skill_key,
        "system_prompt": r.system_prompt,
        "bind_tools": r.bind_tools,
        "full_md_content": r.full_md_content,
    }
