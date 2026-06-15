"""Agent-MCP 绑定关系管理"""

from sqlalchemy import select, delete

from app.db.session import get_db_session
from app.models.agent_mcp_rel import AgentMcpRel


async def get_mcp_keys_for_agent(agent_code: str) -> list[str]:
    """返回指定 Agent 绑定的所有 mcp_key 列表。"""
    async with get_db_session() as session:
        rows = (await session.execute(
            select(AgentMcpRel.mcp_key).where(AgentMcpRel.agent_code == agent_code)
        )).scalars().all()
    return list(rows)


async def get_bound_agents(mcp_key: str) -> list[str]:
    """返回绑定了指定 MCP 的所有 agent_code 列表。"""
    async with get_db_session() as session:
        rows = (await session.execute(
            select(AgentMcpRel.agent_code).where(AgentMcpRel.mcp_key == mcp_key)
        )).scalars().all()
    return list(rows)


async def update_bindings(mcp_key: str, agent_codes: list[str]):
    """批量更新 Agent 绑定关系（先删后插）。"""
    async with get_db_session() as session:
        await session.execute(
            delete(AgentMcpRel).where(AgentMcpRel.mcp_key == mcp_key)
        )
        for code in agent_codes:
            session.add(AgentMcpRel(agent_code=code, mcp_key=mcp_key))
        await session.commit()
