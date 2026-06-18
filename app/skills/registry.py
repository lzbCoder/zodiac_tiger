import json
from pathlib import Path
from sqlalchemy import select
from loguru import logger

from app.db.redis import get_redis
from app.db.session import get_db_session
from app.models.skill_info import SkillInfo
from app.models.skill_md_meta import SkillMdMeta

SKILL_CACHE_KEY = "skill:all"
SKILL_KEY_PREFIX = "skill:"


def build_skill_xml(skill_key: str, body: str, folder_path: str) -> str:
    """将 SKILL.md 正文包装为 XML 格式，附加技能目录路径和资源文件列表。"""
    skill_dir = Path(folder_path)
    resources = []
    for subdir in ("scripts", "assets", "references"):
        subdir_path = skill_dir / subdir
        if subdir_path.exists():
            for f in sorted(subdir_path.iterdir()):
                if f.is_file():
                    resources.append(f"{subdir}/{f.name}")

    resource_xml = ""
    if resources:
        items = "\n".join(f"  <file>{r}</file>" for r in resources)
        resource_xml = f"\n<skill_resources>\n{items}\n</skill_resources>"

    return (
        f'<skill_content name="{skill_key}">\n'
        f"{body}\n"
        f"\nSkill directory: {folder_path}\n"
        f"Relative paths in this skill are relative to the skill directory."
        f"{resource_xml}\n"
        f"</skill_content>"
    )


class SkillRegistry:
    """技能注册中心：从 PostgreSQL 加载技能，缓存到 Redis。"""

    @staticmethod
    async def write_skill(skill_key: str, data: dict) -> None:
        r = await get_redis()
        await r.set(f"{SKILL_KEY_PREFIX}{skill_key}", json.dumps(data, ensure_ascii=False))

    @staticmethod
    async def delete_skill(skill_key: str) -> None:
        r = await get_redis()
        await r.delete(f"{SKILL_KEY_PREFIX}{skill_key}")

    @staticmethod
    async def get_skill(skill_key: str) -> dict | None:
        r = await get_redis()
        raw = await r.get(f"{SKILL_KEY_PREFIX}{skill_key}")
        return json.loads(raw) if raw else None

    @staticmethod
    async def refresh() -> None:
        """启动时加载所有启用技能到 Redis（per-skill key + skill:all 列表）。"""
        async with get_db_session() as session:
            rows = (await session.execute(
                select(
                    SkillInfo.skill_key,
                    SkillInfo.display_name,
                    SkillInfo.skill_desc,
                    SkillInfo.folder_abs_path,
                    SkillInfo.enable_status,
                    SkillMdMeta.system_prompt,
                )
                .outerjoin(SkillMdMeta, SkillMdMeta.skill_key == SkillInfo.skill_key)
                .where(SkillInfo.enable_status == 1)
                .order_by(SkillInfo.sort, SkillInfo.id)
            )).all()

        r = await get_redis()
        skill_list = []
        for row in rows:
            body = row.system_prompt or ""
            xml_body = build_skill_xml(row.skill_key, body, row.folder_abs_path)
            await r.set(
                f"{SKILL_KEY_PREFIX}{row.skill_key}",
                json.dumps({
                    "skill_key": row.skill_key,
                    "skill_desc": row.skill_desc or "",
                    "skill_body": body,
                    "skill_xml_body": xml_body,
                }, ensure_ascii=False),
            )
            skill_list.append({
                "skill_key": row.skill_key,
                "display_name": row.display_name,
                "skill_desc": row.skill_desc,
                "enable_status": row.enable_status,
            })

        await r.set(SKILL_CACHE_KEY, json.dumps(skill_list, ensure_ascii=False))
        logger.info(f"技能缓存已刷新，共 {len(skill_list)} 个启用技能")

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
