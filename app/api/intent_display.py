from fastapi import APIRouter
from loguru import logger

from app.services import intent_display_service
from app.schemas.intent_display import IntentDisplaySave
from app.utils.response import success, fail

router = APIRouter(tags=["意图展示配置"])


@router.get("/intent/display/list")
async def get_intent_display_list():
    """获取所有意图展示配置（前端唯一数据源，按 sort 排序）。"""
    try:
        data = await intent_display_service.get_display_list()
        return success(data)
    except Exception as e:
        logger.error(f"获取意图展示配置失败: {e}")
        return fail(message=str(e))


@router.post("/intent/display/save")
async def save_intent_display(body: IntentDisplaySave):
    """修改单条意图展示配置。"""
    try:
        result = await intent_display_service.save_config(body.model_dump(exclude_none=True))
        return success(result)
    except Exception as e:
        logger.error(f"保存意图展示配置失败: {e}")
        return fail(message=str(e))
