from sqlalchemy import select, update, delete, func, and_

from app.db.session import get_db_session
from app.models.prompt_template import PromptTemplate


async def get_templates(
    category: str | None = None,
    status: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    async with get_db_session() as session:
        conditions = []
        if category:
            conditions.append(PromptTemplate.category == category)
        if status is not None:
            conditions.append(PromptTemplate.status == status)

        where_clause = and_(*conditions) if conditions else True

        count_stmt = select(func.count()).select_from(PromptTemplate).where(where_clause)
        count_result = await session.execute(count_stmt)
        total = count_result.scalar() or 0

        stmt = (
            select(PromptTemplate)
            .where(where_clause)
            .order_by(PromptTemplate.id.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    return [
        {
            "id": r.id,
            "name": r.name,
            "category": r.category,
            "content": r.content,
            "status": r.status,
            "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else "",
        }
        for r in rows
    ], total


async def save_template(data: dict) -> dict:
    async with get_db_session() as session:
        if data.get("id"):
            stmt = (
                update(PromptTemplate)
                .where(PromptTemplate.id == data["id"])
                .values(
                    name=data["name"],
                    category=data["category"],
                    content=data["content"],
                    status=data.get("status", 1),
                )
            )
            await session.execute(stmt)
            tmpl_id = data["id"]
        else:
            obj = PromptTemplate(
                name=data["name"],
                category=data["category"],
                content=data["content"],
                status=data.get("status", 1),
            )
            session.add(obj)
            await session.flush()
            tmpl_id = obj.id
        await session.commit()
    return {"id": tmpl_id}


async def delete_template(template_id: int) -> None:
    async with get_db_session() as session:
        stmt = delete(PromptTemplate).where(PromptTemplate.id == template_id)
        await session.execute(stmt)
        await session.commit()


async def toggle_template_status(template_id: int, status: int) -> None:
    async with get_db_session() as session:
        stmt = update(PromptTemplate).where(PromptTemplate.id == template_id).values(status=status)
        await session.execute(stmt)
        await session.commit()
