"""长任务 CRUD 服务。"""
import uuid
from datetime import datetime

from sqlalchemy import select, update

from app.db.session import get_db_session
from app.models.task import Task


async def get_active_task(session_id: str) -> Task | None:
    """当前焦点任务 = 该会话中 status='active' 且 updated_at 最新的任务。"""
    async with get_db_session() as session:
        row = (await session.execute(
            select(Task)
            .where(Task.session_id == session_id, Task.status == "active")
            .order_by(Task.updated_at.desc())
            .limit(1)
        )).scalar_one_or_none()
    return row


async def get_task(task_id: str) -> Task | None:
    async with get_db_session() as session:
        return (await session.execute(
            select(Task).where(Task.task_id == task_id)
        )).scalar_one_or_none()


async def list_recent_tasks(session_id: str, limit: int = 10) -> list[Task]:
    """最近任务（排除已归档），供 task_manager 判定切换候选。"""
    async with get_db_session() as session:
        rows = (await session.execute(
            select(Task)
            .where(Task.session_id == session_id, Task.status != "archived")
            .order_by(Task.updated_at.desc())
            .limit(limit)
        )).scalars().all()
    return list(rows)


async def create_task(session_id: str, user_id: str, title: str,
                      task_type: str | None = None) -> Task:
    """新建任务并置 active，返回该任务。"""
    task = Task(
        task_id=uuid.uuid4().hex,
        session_id=session_id,
        user_id=user_id,
        title=title,
        task_type=task_type,
        status="active",
    )
    async with get_db_session() as session:
        session.add(task)
        await session.commit()
        await session.refresh(task)
    return task


async def touch(task_id: str) -> None:
    """刷新 updated_at，使其成为当前焦点任务。"""
    async with get_db_session() as session:
        await session.execute(
            update(Task).where(Task.task_id == task_id).values(updated_at=datetime.now())
        )
        await session.commit()


async def reopen(task_id: str) -> None:
    """切回一个已完成任务时重新打开为 active。"""
    async with get_db_session() as session:
        await session.execute(
            update(Task).where(Task.task_id == task_id)
            .values(status="active", completed_at=None, updated_at=datetime.now())
        )
        await session.commit()


async def update_status(task_id: str, status: str) -> None:
    values: dict = {"status": status, "updated_at": datetime.now()}
    if status == "completed":
        values["completed_at"] = datetime.now()
    async with get_db_session() as session:
        await session.execute(
            update(Task).where(Task.task_id == task_id).values(**values)
        )
        await session.commit()


async def set_task_type(task_id: str, task_type: str) -> None:
    """dispatcher 出 intent 后回填任务类型。"""
    async with get_db_session() as session:
        await session.execute(
            update(Task).where(Task.task_id == task_id).values(task_type=task_type)
        )
        await session.commit()


async def set_current_artifact(task_id: str, artifact_id: str) -> None:
    async with get_db_session() as session:
        await session.execute(
            update(Task).where(Task.task_id == task_id)
            .values(current_artifact_id=artifact_id, updated_at=datetime.now())
        )
        await session.commit()


async def delete_by_session(session_id: str) -> None:
    """会话删除时清理该会话所有任务（产物由 artifact_service 一并清理）。"""
    from sqlalchemy import delete
    async with get_db_session() as session:
        await session.execute(delete(Task).where(Task.session_id == session_id))
        await session.commit()
