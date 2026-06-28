import os
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, func
from loguru import logger

from app.db.session import get_db_session
from app.models.file_info import FileInfo

FILE_DIR = Path(__file__).resolve().parent.parent.parent / "files"
os.makedirs(FILE_DIR, exist_ok=True)

_TYPE_MAP: dict[str, str] = {
    "xlsx": "表格", "xls": "表格", "csv": "表格",
    "html": "HTML",
    "pptx": "PPT", "ppt": "PPT",
    "docx": "文档", "doc": "文档", "txt": "文档", "md": "文档",
}

ALL_TYPES = ["表格", "HTML", "PPT", "文档", "其他"]


def _map_type(ext: str) -> str:
    return _TYPE_MAP.get(ext.lower(), "其他")


def format_size(size: int | None) -> str:
    if size is None:
        return ""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.2f} MB"


async def save_file(
    session_id: str,
    chat_id: str,
    file_name: str,
    content: bytes,
    created_by: str = "智能 Agent",
) -> FileInfo:
    """保存文件到本地 + 入库，返回 FileInfo 记录。"""
    date_dir = datetime.now().strftime("%Y-%m-%d")
    target_dir = FILE_DIR / date_dir / session_id
    target_dir.mkdir(parents=True, exist_ok=True)

    # 避免重名
    file_path = target_dir / file_name
    counter = 1
    stem, ext = os.path.splitext(file_name)
    while file_path.exists():
        file_path = target_dir / f"{stem}_{counter}{ext}"
        counter += 1

    file_path.write_bytes(content)

    ext_clean = ext.lstrip(".").lower() if ext else ""
    async with get_db_session() as session:
        record = FileInfo(
            file_name=file_path.name,
            file_path=str(file_path),
            file_size=len(content),
            file_type=_map_type(ext_clean),
            file_extension=ext_clean,
            chat_id=chat_id,
            session_id=session_id,
            created_by=created_by,
        )
        session.add(record)
        await session.commit()
        await session.refresh(record)
    logger.info(f"文件已保存: {file_path.name} ({format_size(len(content))})")
    return record


async def get_files(
    page: int = 1,
    page_size: int = 20,
    file_type: str | None = None,
    keyword: str | None = None,
) -> tuple[list[dict], int]:
    """分页查询，支持 file_type 筛选 + keyword 搜索。"""
    async with get_db_session() as session:
        conditions = []
        if file_type:
            conditions.append(FileInfo.file_type == file_type)
        if keyword:
            conditions.append(FileInfo.file_name.ilike(f"%{keyword}%"))

        where = conditions[0] if len(conditions) == 1 else None
        if len(conditions) > 1:
            where = conditions[0]
            for c in conditions[1:]:
                where = where & c

        base = select(FileInfo)
        if where is not None:
            base = base.where(where)

        count_stmt = select(func.count()).select_from(base.subquery())
        count_result = await session.execute(count_stmt)
        total = count_result.scalar() or 0

        stmt = (
            base
            .order_by(FileInfo.created_at.desc())
            .limit(page_size)
            .offset((page - 1) * page_size)
        )
        result = await session.execute(stmt)
        rows = result.scalars().all()

    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "file_name": r.file_name,
            "file_path": r.file_path,
            "file_size": r.file_size,
            "file_type": r.file_type,
            "file_extension": r.file_extension,
            "session_id": r.session_id,
            "created_by": r.created_by,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
        })
    return items, total


async def get_type_counts() -> list[dict]:
    """返回每种文件类型的数量。"""
    async with get_db_session() as session:
        stmt = (
            select(FileInfo.file_type, func.count(FileInfo.id))
            .group_by(FileInfo.file_type)
        )
        result = await session.execute(stmt)
        rows = result.all()

    counts = {t: 0 for t in ALL_TYPES}
    total = 0
    for r in rows:
        if r[0] and r[0] in counts:
            counts[r[0]] = r[1]
            total += r[1]
        elif r[0]:
            counts["其他"] += r[1]
            total += r[1]

    items = [{"file_type": "全部", "count": total}]
    for t in ALL_TYPES:
        items.append({"file_type": t, "count": counts[t]})
    return items


async def get_file_by_id(file_id: int) -> FileInfo | None:
    async with get_db_session() as session:
        stmt = select(FileInfo).where(FileInfo.id == file_id)
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
    return record


async def delete_file(file_id: int) -> bool:
    """删除文件：磁盘 + 数据库记录。返回是否成功。"""
    import os
    from sqlalchemy import delete as sa_delete

    async with get_db_session() as session:
        stmt = select(FileInfo).where(FileInfo.id == file_id)
        result = await session.execute(stmt)
        record = result.scalar_one_or_none()
        if not record:
            return False

        # 删除磁盘文件
        if record.file_path and os.path.exists(record.file_path):
            os.remove(record.file_path)
            parent = os.path.dirname(record.file_path)
            while parent and parent != str(FILE_DIR) and os.path.isdir(parent):
                try:
                    if os.listdir(parent):
                        break
                    os.rmdir(parent)
                    parent = os.path.dirname(parent)
                except OSError:
                    break

        await session.execute(sa_delete(FileInfo).where(FileInfo.id == file_id))
        await session.commit()
    logger.info(f"文件已删除: {record.file_name} (id={file_id})")
    return True
