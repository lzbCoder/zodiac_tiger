from fastapi import APIRouter
from loguru import logger

from app.schemas.template import TemplateSave, TemplateStatus
from app.services import template_service
from app.utils.response import success, fail

router = APIRouter(tags=["提示词模板"])


@router.get("/template/list")
async def template_list(category: str | None = None, status: int | None = None,
                        page: int = 1, page_size: int = 20):
    try:
        data, total = await template_service.get_templates(category, status, page, page_size)
        return success({"list": data, "total": total, "page": page, "page_size": page_size})
    except Exception as e:
        logger.error(f"获取模板列表失败: {e}")
        return fail(message=str(e))


@router.post("/template/save")
async def template_save(req: TemplateSave):
    try:
        result = await template_service.save_template(req.model_dump())
        return success(result, "保存成功")
    except Exception as e:
        logger.error(f"保存模板失败: {e}")
        return fail(message=str(e))


@router.delete("/template/delete")
async def template_delete(template_id: int):
    try:
        await template_service.delete_template(template_id)
        return success(message="删除成功")
    except Exception as e:
        logger.error(f"删除模板失败: {e}")
        return fail(message=str(e))


@router.post("/template/status")
async def template_status(req: TemplateStatus):
    try:
        await template_service.toggle_template_status(req.id, req.status)
        return success(message="状态已更新")
    except Exception as e:
        logger.error(f"更新模板状态失败: {e}")
        return fail(message=str(e))
