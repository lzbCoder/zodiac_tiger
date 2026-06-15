from fastapi import APIRouter, UploadFile, File, Form
from loguru import logger

from app.schemas.skill_info import SkillEdit, SkillStatus, AgentSkillBind
from app.services import skill_info_service, agent_skill_service
from app.utils.response import success, fail

router = APIRouter(tags=["技能管理"])


@router.post("/skill/upload")
async def skill_upload(
    skill_name: str = Form(...),
    skill_desc: str | None = Form(None),
    file: UploadFile = File(...),
):
    try:
        file_bytes = await file.read()
        result = await skill_info_service.upload_skill(file_bytes, file.filename or "", skill_name, skill_desc)
        return success(result, "技能上传成功")
    except ValueError as e:
        return fail(message=str(e))
    except Exception as e:
        logger.error(f"技能上传失败: {e}")
        return fail(message=f"上传失败: {e}")


@router.get("/skill/list")
async def skill_list():
    try:
        data = await skill_info_service.list_skills()
        return success(data)
    except Exception as e:
        logger.error(f"获取技能列表失败: {e}")
        return fail(message=str(e))


@router.put("/skill/edit")
async def skill_edit(req: SkillEdit):
    try:
        await skill_info_service.edit_skill(req.skill_key, req.skill_name, req.skill_desc)
        return success(message="保存成功")
    except Exception as e:
        logger.error(f"编辑技能失败: {e}")
        return fail(message=str(e))


@router.put("/skill/status")
async def skill_status(req: SkillStatus):
    try:
        await skill_info_service.toggle_enable(req.skill_key, req.enable_status)
        return success(message="状态已更新")
    except Exception as e:
        logger.error(f"更新技能状态失败: {e}")
        return fail(message=str(e))


@router.delete("/skill/delete/{skill_key}")
async def skill_delete(skill_key: str):
    try:
        await skill_info_service.delete_skill(skill_key)
        return success(message="已删除")
    except Exception as e:
        logger.error(f"删除技能失败: {e}")
        return fail(message=str(e))


@router.get("/skill/agent-bind")
async def skill_agent_bind_get(skill_key: str):
    try:
        agents = await agent_skill_service.get_bound_agents(skill_key)
        return success({"skill_key": skill_key, "agent_codes": agents})
    except Exception as e:
        logger.error(f"查询绑定关系失败: {e}")
        return fail(message=str(e))


@router.put("/skill/agent-bind")
async def skill_agent_bind_put(req: AgentSkillBind):
    try:
        await agent_skill_service.update_bindings(req.skill_key, req.agent_codes)
        return success(message="绑定关系已更新")
    except Exception as e:
        logger.error(f"更新绑定关系失败: {e}")
        return fail(message=str(e))
