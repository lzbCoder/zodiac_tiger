from fastapi import APIRouter
from loguru import logger

from app.schemas.skill import SkillSave, SkillStatus
from app.services import skill_service
from app.skills.registry import SkillRegistry
from app.utils.response import success, fail

router = APIRouter(tags=["技能管理"])


@router.get("/skill/list")
async def skill_list():
    try:
        data = await skill_service.get_skills()
        return success(data)
    except Exception as e:
        logger.error(f"获取技能列表失败: {e}")
        return fail(message=str(e))


@router.post("/skill/save")
async def skill_save(req: SkillSave):
    try:
        result = await skill_service.save_skill(req.model_dump())
        return success(result, "保存成功")
    except Exception as e:
        logger.error(f"保存技能失败: {e}")
        return fail(message=str(e))


@router.post("/skill/status")
async def skill_status(req: SkillStatus):
    try:
        await skill_service.toggle_skill_status(req.id, req.status)
        return success(message="状态已更新")
    except Exception as e:
        logger.error(f"更新技能状态失败: {e}")
        return fail(message=str(e))


@router.delete("/skill/delete")
async def skill_delete(id: int):
    try:
        await skill_service.delete_skill(id)
        return success(message="已删除")
    except Exception as e:
        logger.error(f"删除技能失败: {e}")
        return fail(message=str(e))


@router.get("/skill/available")
async def skill_available():
    try:
        data = await SkillRegistry.get_available()
        return success(data)
    except Exception as e:
        logger.error(f"获取可用技能失败: {e}")
        return fail(message=str(e))
