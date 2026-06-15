"""Agent-技能绑定关系管理"""

from sqlalchemy import select, delete

from app.db.session import get_db_session
from app.models.agent_skill_rel import AgentSkillRel


async def get_skill_keys_for_agent(agent_code: str) -> list[str]:
    async with get_db_session() as session:
        rows = (await session.execute(
            select(AgentSkillRel.skill_key).where(AgentSkillRel.agent_code == agent_code)
        )).scalars().all()
    return list(rows)


async def get_bound_agents(skill_key: str) -> list[str]:
    async with get_db_session() as session:
        rows = (await session.execute(
            select(AgentSkillRel.agent_code).where(AgentSkillRel.skill_key == skill_key)
        )).scalars().all()
    return list(rows)


async def update_bindings(skill_key: str, agent_codes: list[str]) -> None:
    async with get_db_session() as session:
        await session.execute(
            delete(AgentSkillRel).where(AgentSkillRel.skill_key == skill_key)
        )
        for code in agent_codes:
            session.add(AgentSkillRel(agent_code=code, skill_key=skill_key))
        await session.commit()
