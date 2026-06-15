"""SKILL.md 结构化解析与缓存（激活阶段懒加载）"""

import json
from datetime import datetime

from sqlalchemy import select, update
from loguru import logger

from app.db.session import get_db_session
from app.models.skill_md_meta import SkillMdMeta


async def activate_skill(skill_key: str, folder_abs_path: str) -> dict:
    """
    激活技能：读取 SKILL.md 解析后写入 skill_md_meta 缓存。
    若已有缓存（system_prompt 非空）则直接返回，零磁盘 IO。
    """
    async with get_db_session() as session:
        row = (await session.execute(
            select(SkillMdMeta).where(SkillMdMeta.skill_key == skill_key)
        )).scalar_one_or_none()

    if row and row.system_prompt:
        return _row_to_dict(row)

    # 磁盘读取 SKILL.md
    from pathlib import Path
    from agentskills_core import split_frontmatter

    skill_md_path = Path(folder_abs_path) / "SKILL.md"
    if not skill_md_path.exists():
        logger.warning(f"[SkillMd] SKILL.md 不存在: {skill_md_path}")
        return {}

    raw = skill_md_path.read_text(encoding="utf-8")
    meta, body = split_frontmatter(raw)

    allowed_tools = meta.get("allowed-tools", [])
    bind_tools = json.dumps(allowed_tools, ensure_ascii=False) if allowed_tools else None
    system_prompt = body.strip() if body.strip() else None

    now = datetime.now()
    async with get_db_session() as session:
        existing = (await session.execute(
            select(SkillMdMeta).where(SkillMdMeta.skill_key == skill_key)
        )).scalar_one_or_none()

        if existing:
            await session.execute(
                update(SkillMdMeta)
                .where(SkillMdMeta.skill_key == skill_key)
                .values(
                    full_md_content=raw,
                    system_prompt=system_prompt,
                    bind_tools=bind_tools,
                    update_time=now,
                )
            )
        else:
            session.add(SkillMdMeta(
                skill_key=skill_key,
                full_md_content=raw,
                system_prompt=system_prompt,
                bind_tools=bind_tools,
                update_time=now,
            ))
        await session.commit()

    logger.info(f"[SkillMd] 技能 {skill_key} 激活完成")
    return {
        "skill_key": skill_key,
        "system_prompt": system_prompt,
        "bind_tools": bind_tools,
        "full_md_content": raw,
    }


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
