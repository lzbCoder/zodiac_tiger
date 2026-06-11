from sqlalchemy import select, update

from app.db.session import get_db_session
from app.models.intent_display import IntentDisplayConfig


async def get_display_list() -> list[dict]:
    async with get_db_session() as session:
        stmt = (
            select(IntentDisplayConfig)
            .order_by(IntentDisplayConfig.sort.asc())
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    return [
        {
            "intent_key": r.intent_key,
            "show_name": r.show_name,
            "intent_desc": r.intent_desc,
            "demo_input": r.demo_input,
            "icon": r.icon,
            "sort": r.sort,
            "enable": r.enable,
        }
        for r in rows
    ]


async def save_config(data: dict) -> dict:
    async with get_db_session() as session:
        # 查询是否存在
        stmt = select(IntentDisplayConfig).where(
            IntentDisplayConfig.intent_key == data["intent_key"]
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            updates = {}
            for field in ("show_name", "intent_desc", "demo_input", "icon", "sort", "enable"):
                if field in data and data[field] is not None:
                    updates[field] = data[field]
            if updates:
                update_stmt = (
                    update(IntentDisplayConfig)
                    .where(IntentDisplayConfig.intent_key == data["intent_key"])
                    .values(**updates)
                )
                await session.execute(update_stmt)
        else:
            obj = IntentDisplayConfig(
                intent_key=data["intent_key"],
                show_name=data.get("show_name", ""),
                intent_desc=data.get("intent_desc", ""),
                demo_input=data.get("demo_input", ""),
                icon=data.get("icon"),
                sort=data.get("sort", 0),
                enable=data.get("enable", 1),
            )
            session.add(obj)

        await session.commit()

    return {"intent_key": data["intent_key"]}
