from pydantic import BaseModel


class McpServerSave(BaseModel):
    mcp_key: str
    display_name: str
    endpoint_url: str
    auth_headers: dict = {}
    remark: str | None = None


class McpServerStatus(BaseModel):
    mcp_key: str
    enable_status: int


class McpTestConnect(BaseModel):
    endpoint_url: str
    auth_headers: dict = {}


class McpToolAllow(BaseModel):
    mcp_key: str
    tool_name: str
    is_allow: int


class McpAgentBind(BaseModel):
    mcp_key: str
    agent_codes: list[str]
