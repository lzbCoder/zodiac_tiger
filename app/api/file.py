import os
from fastapi import APIRouter
from fastapi.responses import FileResponse
from loguru import logger

from app.services import file_service
from app.utils.response import success, fail

router = APIRouter(tags=["文件资源"])


@router.get("/file/list")
async def file_list(
    page: int = 1,
    page_size: int = 20,
    file_type: str | None = None,
    keyword: str | None = None,
):
    try:
        data, total = await file_service.get_files(page, page_size, file_type, keyword)
        return success({"list": data, "total": total, "page": page, "page_size": page_size})
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        return fail(message=str(e))


@router.get("/file/types")
async def file_types():
    try:
        data = await file_service.get_type_counts()
        return success(data)
    except Exception as e:
        logger.error(f"获取文件分类失败: {e}")
        return fail(message=str(e))


@router.delete("/file/delete/{file_id}")
async def file_delete(file_id: int):
    try:
        ok = await file_service.delete_file(file_id)
        return success(message="已删除") if ok else fail(message="文件不存在")
    except Exception as e:
        logger.error(f"文件删除失败: {e}")
        return fail(message=str(e))


@router.get("/file/download/{file_id}")
async def file_download(file_id: int):
    try:
        record = await file_service.get_file_by_id(file_id)
        if not record:
            return fail(message="该文件已被删除，请检查后重试")
        if not os.path.exists(record.file_path):
            return fail(message="该文件已被删除，请检查后重试")
        return FileResponse(record.file_path, filename=record.file_name)
    except Exception as e:
        logger.error(f"文件下载失败: {e}")
        return fail(message=str(e))
