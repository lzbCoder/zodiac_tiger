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
from app.skills.registry import SkillRegistry, build_skill_xml


async def upload_skill(file_bytes: bytes, filename: str, skill_name: str, skill_desc: str | None) -> dict:
    """
    上传并解压技能压缩包（.zip）：
    1. 后缀校验
    2. 解压 → 找 SKILL.md → 解析 frontmatter 取 skill_key
    3. 唯一性检验
    4. 落地到 {SKILLS_ROOT}/{skill_key}/
    5. 写 skill_info + 完整 skill_md_meta + Redis per-skill key
    """
    from agentskills_core import split_frontmatter
    from app.skills.file_system_provider import FileSystemSkillProvider

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

        skill_mds = list(extract_dir.rglob("SKILL.md"))
        if not skill_mds:
            raise ValueError("压缩包中未找到 SKILL.md 文件，请检查格式是否正确")

        skill_md_path = min(skill_mds, key=lambda p: len(p.parts))
        skill_folder = skill_md_path.parent

        raw = skill_md_path.read_text(encoding="utf-8")
        frontmatter, body = split_frontmatter(raw)

        skill_key = frontmatter.get("name", "").strip()
        if not skill_key:
            raise ValueError("SKILL.md frontmatter 中未找到 name 字段")

        md_desc = frontmatter.get("description", "").strip()

        async with get_db_session() as session:
            existing = (await session.execute(
                select(SkillInfo).where(SkillInfo.skill_key == skill_key)
            )).scalar_one_or_none()

        if existing:
            raise ValueError(f"技能 [{skill_key}] 已存在，请先删除后再上传")

        skills_root = Path(settings.SKILLS_ROOT)
        skills_root.mkdir(parents=True, exist_ok=True)
        target_dir = skills_root / skill_key

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(str(skill_folder), str(target_dir))

        folder_abs_path = str(target_dir.resolve())

        # 完整写入 skill_md_meta（使用 FileSystemSkillProvider，保证从落地后的磁盘读取）
        provider = FileSystemSkillProvider(settings.SKILLS_ROOT)
        disk_frontmatter = await provider.get_frontmatter(skill_key)
        disk_body = await provider.get_body(skill_key)

        allowed_tools = disk_frontmatter.get("allowed-tools", [])
        bind_tools = json.dumps(allowed_tools, ensure_ascii=False) if allowed_tools else None
        system_prompt = disk_body.strip() or None

        skill_xml_body = build_skill_xml(skill_key, disk_body, folder_abs_path)

        async with get_db_session() as session:
            session.add(SkillInfo(
                skill_key=skill_key,
                display_name=skill_name.strip() if skill_name else skill_key,
                skill_desc=md_desc,
                display_desc=skill_desc,
                folder_abs_path=folder_abs_path,
            ))
            session.add(SkillMdMeta(
                skill_key=skill_key,
                full_md_content=raw,
                system_prompt=system_prompt,
                bind_tools=bind_tools,
                update_time=datetime.now(),
            ))
            await session.commit()

        await SkillRegistry.write_skill(skill_key, {
            "skill_key": skill_key,
            "skill_desc": md_desc,
            "skill_body": disk_body,
            "skill_xml_body": skill_xml_body,
        })

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
    async with get_db_session() as session:
        await session.execute(
            update(SkillInfo)
            .where(SkillInfo.skill_key == skill_key)
            .values(enable_status=enable_status, update_time=datetime.now())
        )
        await session.commit()

    if enable_status == 0:
        await SkillRegistry.delete_skill(skill_key)
    else:
        # 重新启用：从 DB 读取完整数据，写入 Redis
        async with get_db_session() as session:
            row = (await session.execute(
                select(SkillInfo.skill_desc, SkillInfo.folder_abs_path)
                .where(SkillInfo.skill_key == skill_key)
            )).one_or_none()
            meta_row = (await session.execute(
                select(SkillMdMeta.system_prompt)
                .where(SkillMdMeta.skill_key == skill_key)
            )).scalar_one_or_none()

        if row:
            body = meta_row or ""
            xml_body = build_skill_xml(skill_key, body, row.folder_abs_path)
            await SkillRegistry.write_skill(skill_key, {
                "skill_key": skill_key,
                "skill_desc": row.skill_desc or "",
                "skill_body": body,
                "skill_xml_body": xml_body,
            })


async def delete_skill(skill_key: str) -> None:
    async with get_db_session() as session:
        row = (await session.execute(
            select(SkillInfo).where(SkillInfo.skill_key == skill_key)
        )).scalar_one_or_none()

    if row:
        folder = Path(row.folder_abs_path)
        if folder.exists():
            shutil.rmtree(folder)

    async with get_db_session() as session:
        await session.execute(delete(AgentSkillRel).where(AgentSkillRel.skill_key == skill_key))
        await session.execute(delete(SkillMdMeta).where(SkillMdMeta.skill_key == skill_key))
        await session.execute(delete(SkillInfo).where(SkillInfo.skill_key == skill_key))
        await session.commit()

    await SkillRegistry.delete_skill(skill_key)
    logger.info(f"[Skill] 已删除: {skill_key}")


async def get_skill_detail(skill_key: str) -> dict | None:
    """获取技能详情（含 full_md_content，供前端 Markdown 渲染）。"""
    async with get_db_session() as session:
        info_row = (await session.execute(
            select(SkillInfo.display_name).where(SkillInfo.skill_key == skill_key)
        )).scalar_one_or_none()
        meta_row = (await session.execute(
            select(SkillMdMeta.full_md_content).where(SkillMdMeta.skill_key == skill_key)
        )).scalar_one_or_none()

    if info_row is None:
        return None
    return {
        "skill_key": skill_key,
        "display_name": info_row,
        "full_md_content": meta_row or "",
    }


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
