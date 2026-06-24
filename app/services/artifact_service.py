"""任务产物 CRUD 服务，维护版本树。"""
import uuid

from sqlalchemy import select, delete

from app.db.session import get_db_session
from app.models.task import Task
from app.models.task_artifact import TaskArtifact


async def get_artifact(artifact_id: str) -> TaskArtifact | None:
    async with get_db_session() as session:
        return (await session.execute(
            select(TaskArtifact).where(TaskArtifact.artifact_id == artifact_id)
        )).scalar_one_or_none()


async def add_artifact(task_id: str, artifact_type: str | None, content: str | None,
                       title: str | None = None, content_summary: str | None = None,
                       parent_artifact_id: str | None = None,
                       file_id: str | None = None) -> TaskArtifact:
    """新增产物版本。version = 父版本 + 1（无父则为 1）。"""
    version = 1
    if parent_artifact_id:
        parent = await get_artifact(parent_artifact_id)
        if parent:
            version = parent.version + 1

    artifact = TaskArtifact(
        artifact_id=uuid.uuid4().hex,
        task_id=task_id,
        parent_artifact_id=parent_artifact_id,
        artifact_type=artifact_type,
        version=version,
        title=title,
        content=content,
        content_summary=content_summary,
        file_id=file_id,
    )
    async with get_db_session() as session:
        session.add(artifact)
        await session.commit()
        await session.refresh(artifact)
    return artifact


async def list_versions(task_id: str) -> list[TaskArtifact]:
    async with get_db_session() as session:
        rows = (await session.execute(
            select(TaskArtifact).where(TaskArtifact.task_id == task_id)
            .order_by(TaskArtifact.version.asc())
        )).scalars().all()
    return list(rows)


async def delete_by_session(session_id: str) -> None:
    """会话删除时清理该会话所有任务的产物。"""
    async with get_db_session() as session:
        task_ids = (await session.execute(
            select(Task.task_id).where(Task.session_id == session_id)
        )).scalars().all()
        if task_ids:
            await session.execute(
                delete(TaskArtifact).where(TaskArtifact.task_id.in_(list(task_ids)))
            )
            await session.commit()
