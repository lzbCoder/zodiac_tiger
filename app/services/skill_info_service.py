"""技能主表 CRUD + 压缩包上传解压"""

import json
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import select, update, delete

from app.config import settings
from app.db.session import get_db_session
from app.models.skill_info import SkillInfo
from app.models.skill_md_meta import SkillMdMeta
from app.models.agent_skill_rel import AgentSkillRel


async def upload_skill(file_bytes: bytes, filename: str, skill_name: str, skill_desc: str | None) -> dict:
    """
    上传并解压技能压缩包（.zip
    1. 后缀校验
    2. 解压 → 找 SKILL.md → 解析 frontmatter 取 skill_key
    3. 唯一性检验
    4. 落地到 {SKILLS_ROOT}/{skill_key}/
    5. 写 skill_info + skill_md_meta 占位
    """
    from agentskills_core import split_frontmatter

    suffix = Path(filename).suffix.lower()
    if suffix != ".zip":
        raise ValueError("仅支持 .zip 格式")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_file = Path(tmpdir) / filename
        tmp_file.write_bytes(file_bytes)

        extract_dir = Path(tmpdir) / "extracted"
        extract_dir.mkdir()

        with zipfile.ZipFile(tmp_file) as zf:
            zf.extractall(extract_dir)

        # 找 SKILL.md（取路径最浅的那个）
        skill_mds = list(extract_dir.rglob("SKILL.md"))
        if not skill_mds:
            raise ValueError("压缩包中未找到 SKILL.md 文件，请检查格式是否正确")

        skill_md_path = min(skill_mds, key=lambda p: len(p.parts))
        skill_folder = skill_md_path.parent

        raw = skill_md_path.read_text(encoding="utf-8")
        meta, _ = split_frontmatter(raw)

        skill_key = meta.get("name", "").strip()
        if not skill_key:
            raise ValueError("SKILL.md frontmatter 中未找到 name 字段")

        md_desc = meta.get("description", "").strip()

        # 唯一性检验
        async with get_db_session() as session:
            existing = (await session.execute(
                select(SkillInfo).where(SkillInfo.skill_key == skill_key)
            )).scalar_one_or_none()

        if existing:
            raise ValueError(f"技能 [{skill_key}] 已存在，请先删除后再上传")

        # 落地到 SKILLS_ROOT
        skills_root = Path(settings.SKILLS_ROOT)
        skills_root.mkdir(parents=True, exist_ok=True)
        target_dir = skills_root / skill_key

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(str(skill_folder), str(target_dir))

        folder_abs_path = str(target_dir.resolve())

        # 写 DB
        async with get_db_session() as session:
            session.add(SkillInfo(
                skill_key=skill_key,
                display_name=skill_name.strip() if skill_name else skill_key,
                skill_desc=md_desc,
                display_desc=skill_desc,
                folder_abs_path=folder_abs_path,
            ))
            session.add(SkillMdMeta(skill_key=skill_key))
            await session.commit()

        logger.info(f"[Skill] 上传成功: {skill_key} → {folder_abs_path}")
        return {
            "skill_key": skill_key,
            "display_name": skill_name.strip() if skill_name else skill_key,
            "skill_desc": md_desc,
            "folder_abs_path": folder_abs_path,
        }


async def list_skills() -> list[dict]:
    async with get_db_session() as session:
        rows = (await session.execute(
            select(SkillInfo).order_by(SkillInfo.sort, SkillInfo.id)
        )).scalars().all()
    return [_row_to_dict(r) for r in rows]


async def edit_skill(skill_key: str, display_name: str, display_desc: str | None) -> None:
    async with get_db_session() as session:
        await session.execute(
            update(SkillInfo)
            .where(SkillInfo.skill_key == skill_key)
            .values(
                display_name=display_name.strip(),
                display_desc=display_desc,
                update_time=datetime.now(),
            )
        )
        await session.commit()


async def toggle_enable(skill_key: str, enable_status: int) -> None:
    from app.skills.manager import GlobalSkillManager

    async with get_db_session() as session:
        await session.execute(
            update(SkillInfo)
            .where(SkillInfo.skill_key == skill_key)
            .values(enable_status=enable_status, update_time=datetime.now())
        )
        await session.commit()

    if enable_status == 0:
        GlobalSkillManager.invalidate(skill_key)


async def delete_skill(skill_key: str) -> None:
    from app.skills.manager import GlobalSkillManager

    # 读取磁盘路径
    async with get_db_session() as session:
        row = (await session.execute(
            select(SkillInfo).where(SkillInfo.skill_key == skill_key)
        )).scalar_one_or_none()

    if row:
        folder = Path(row.folder_abs_path)
        if folder.exists():
            shutil.rmtree(folder)

    # 删三表记录
    async with get_db_session() as session:
        await session.execute(delete(AgentSkillRel).where(AgentSkillRel.skill_key == skill_key))
        await session.execute(delete(SkillMdMeta).where(SkillMdMeta.skill_key == skill_key))
        await session.execute(delete(SkillInfo).where(SkillInfo.skill_key == skill_key))
        await session.commit()

    GlobalSkillManager.invalidate(skill_key)
    logger.info(f"[Skill] 已删除: {skill_key}")


def _row_to_dict(r: SkillInfo) -> dict:
    return {
        "skill_key": r.skill_key,
        "display_name": r.display_name,
        "skill_desc": r.skill_desc,
        "display_desc": r.display_desc,
        "folder_abs_path": r.folder_abs_path,
        "enable_status": r.enable_status,
        "sort": r.sort,
        "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S") if r.create_time else None,
        "update_time": r.update_time.strftime("%Y-%m-%d %H:%M:%S") if r.update_time else None,
    }
