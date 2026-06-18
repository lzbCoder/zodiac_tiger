import asyncio
from fastapi import APIRouter
from loguru import logger

from app.schemas.mcp_server import McpServerSave, McpServerStatus, McpTestConnect, McpToolAllow, McpAgentBind
from app.services import mcp_server_service, mcp_tool_service, agent_mcp_service
from app.utils.response import success, fail

router = APIRouter(tags=["MCP 服务管理"])


# ==================== MCP 服务基础管理 ====================

@router.get("/mcp/server/list")
async def server_list():
    try:
        return success(await mcp_server_service.list_servers())
    except Exception as e:
        logger.error(f"获取 MCP 列表失败: {e}")
        return fail(message=str(e))


@router.post("/mcp/server/save")
async def server_save(req: McpServerSave):
    """新增/编辑 MCP 服务，保存后自动同步工具。"""
    try:
        result = await mcp_server_service.save_server(req.model_dump())
        if req.transport_type == "sse":
            # SSE 冷启动需 3-5 分钟，后台异步同步工具，避免阻塞保存响应
            task = asyncio.create_task(mcp_server_service.sync_tools(req.mcp_key))
            task.add_done_callback(
                lambda t: logger.error(f"SSE 工具后台同步失败 [{req.mcp_key}]: {t.exception()}")
                if not t.cancelled() and t.exception() else None
            )
            return success({**result, "tool_count": 0},
                           "保存成功（SSE 工具同步在后台进行，约 3-5 分钟后刷新工具列表）")
        tool_count = await mcp_server_service.sync_tools(req.mcp_key)
        return success({**result, "tool_count": tool_count}, "保存成功")
    except Exception as e:
        logger.error(f"保存 MCP 配置失败: {e}")
        return fail(message=str(e))


@router.delete("/mcp/server/delete")
async def server_delete(mcp_key: str):
    try:
        await mcp_server_service.delete_server(mcp_key)
        return success(message="已删除")
    except Exception as e:
        logger.error(f"删除 MCP 配置失败: {e}")
        return fail(message=str(e))


@router.put("/mcp/server/status")
async def server_status(req: McpServerStatus):
    try:
        await mcp_server_service.toggle_enable_status(req.mcp_key, req.enable_status)
        return success(message="状态已更新")
    except Exception as e:
        logger.error(f"更新 MCP 状态失败: {e}")
        return fail(message=str(e))


@router.post("/mcp/server/test-connect")
async def server_test_connect(req: McpTestConnect):
    """测试连通性，不入库。"""
    try:
        result = await mcp_server_service.test_connect(req.endpoint_url, req.auth_headers, req.transport_type, req.mcp_key)
        return success(result)
    except Exception as e:
        logger.error(f"测试 MCP 连接失败: {e}")
        return fail(message=str(e))


@router.post("/mcp/server/sync-tools")
async def server_sync_tools(mcp_key: str):
    """手动重新拉取远端工具并同步到数据库。"""
    try:
        tool_count = await mcp_server_service.sync_tools(mcp_key)
        return success({"tool_count": tool_count}, f"同步完成，共 {tool_count} 个工具")
    except Exception as e:
        logger.error(f"同步 MCP 工具失败: {e}")
        return fail(message=str(e))


# ==================== MCP 工具白名单管理 ====================

@router.get("/mcp/tools")
async def tool_list(mcp_key: str):
    try:
        return success(await mcp_tool_service.get_tools_by_mcp(mcp_key))
    except Exception as e:
        logger.error(f"获取工具列表失败: {e}")
        return fail(message=str(e))


@router.put("/mcp/tool/allow")
async def tool_allow(req: McpToolAllow):
    try:
        await mcp_tool_service.toggle_tool_allow(req.mcp_key, req.tool_name, req.is_allow)
        return success(message="已更新")
    except Exception as e:
        logger.error(f"更新工具白名单失败: {e}")
        return fail(message=str(e))


# ==================== Agent 绑定管理 ====================

@router.get("/mcp/agent-bind")
async def agent_bind_get(mcp_key: str):
    try:
        return success(await agent_mcp_service.get_bound_agents(mcp_key))
    except Exception as e:
        logger.error(f"获取 Agent 绑定失败: {e}")
        return fail(message=str(e))


@router.put("/mcp/agent-bind")
async def agent_bind_update(req: McpAgentBind):
    try:
        await agent_mcp_service.update_bindings(req.mcp_key, req.agent_codes)
        return success(message="绑定关系已更新")
    except Exception as e:
        logger.error(f"更新 Agent 绑定失败: {e}")
        return fail(message=str(e))
