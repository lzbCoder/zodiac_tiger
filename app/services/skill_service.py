from sqlalchemy import select, update, delete

from app.db.session import get_db_session
from app.models.skill import Skill
from app.skills.registry import SkillRegistry


async def get_skills() -> list[dict]:
    async with get_db_session() as session:
        stmt = select(Skill).order_by(Skill.id)
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "desc": r.desc,
            "skill_type": r.skill_type,
            "mcp_id": r.mcp_id,
            "timeout": r.timeout,
            "status": r.status,
            "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
        }
        for r in rows
    ]


async def save_skill(data: dict) -> dict:
    async with get_db_session() as session:
        if data.get("id"):
            stmt = (
                update(Skill)
                .where(Skill.id == data["id"])
                .values(
                    name=data["name"],
                    desc=data.get("desc"),
                    skill_type=data.get("skill_type", "custom"),
                    mcp_id=data.get("mcp_id"),
                    timeout=data.get("timeout", 30),
                    status=data.get("status", 1),
                )
            )
            await session.execute(stmt)
            skill_id = data["id"]
        else:
            obj = Skill(
                name=data["name"],
                desc=data.get("desc"),
                skill_type=data.get("skill_type", "custom"),
                mcp_id=data.get("mcp_id"),
                timeout=data.get("timeout", 30),
                status=data.get("status", 1),
            )
            session.add(obj)
            await session.flush()
            skill_id = obj.id
        await session.commit()
    await SkillRegistry.refresh()
    return {"id": skill_id}


async def toggle_skill_status(skill_id: int, status: int) -> None:
    async with get_db_session() as session:
        stmt = update(Skill).where(Skill.id == skill_id).values(status=status)
        await session.execute(stmt)
        await session.commit()
    await SkillRegistry.refresh()


async def delete_skill(skill_id: int) -> None:
    async with get_db_session() as session:
        stmt = delete(Skill).where(Skill.id == skill_id)
        await session.execute(stmt)
        await session.commit()
    await SkillRegistry.refresh()
