from fastapi import APIRouter
from loguru import logger

from app.schemas.mcp import McpDelete, McpSave, McpTest, McpStatus
from app.services import mcp_service
from app.utils.response import success, fail

router = APIRouter(tags=["MCP 服务管理"])


@router.get("/mcp/list")
async def mcp_list():
    try:
        data = await mcp_service.get_mcp_list()
        return success(data)
    except Exception as e:
        logger.error(f"获取 MCP 列表失败: {e}")
        return fail(message=str(e))


@router.post("/mcp/save")
async def mcp_save(req: McpSave):
    try:
        result = await mcp_service.save_mcp(req.model_dump())
        return success(result, "保存成功")
    except Exception as e:
        logger.error(f"保存 MCP 配置失败: {e}")
        return fail(message=str(e))


@router.post("/mcp/test")
async def mcp_test(req: McpTest):
    try:
        result = await mcp_service.test_mcp(req.id)
        return success(result)
    except Exception as e:
        logger.error(f"测试 MCP 连接失败: {e}")
        return fail(message=str(e))


@router.post("/mcp/status")
async def mcp_status(req: McpStatus):
    try:
        await mcp_service.toggle_mcp_status(req.id, req.status)
        return success(message="状态已更新")
    except Exception as e:
        logger.error(f"更新 MCP 状态失败: {e}")
        return fail(message=str(e))


@router.get("/mcp/log")
async def mcp_log(mcp_id: int, limit: int = 50):
    try:
        data = await mcp_service.get_mcp_logs(mcp_id, limit)
        return success(data)
    except Exception as e:
        logger.error(f"获取 MCP 日志失败: {e}")
        return fail(message=str(e))


@router.delete("/mcp/delete")
async def mcp_delete(req: McpDelete):
    try:
        await mcp_service.delete_mcp(req.id)
        return success(message="已删除")
    except Exception as e:
        logger.error(f"删除 MCP 配置失败: {e}")
        return fail(message=str(e))
