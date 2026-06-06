from pydantic import BaseModel


class McpSave(BaseModel):
    id: int | None = None
    name: str
    url: str
    auth_type: str = "none"
    api_key: str | None = None
    timeout: int = 20
    status: int = 1


class McpTest(BaseModel):
    id: int


class McpStatus(BaseModel):
    id: int
    status: int


class McpDelete(BaseModel):
    id: int
