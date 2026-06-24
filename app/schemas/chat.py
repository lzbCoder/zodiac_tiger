from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str
    message: str
    enable_search: bool = False


class ResumeRequest(BaseModel):
    params: dict
    config: dict
    chat_id: str | None = None


class AbortRequest(BaseModel):
    session_id: str


class RenameSessionRequest(BaseModel):
    session_id: str
    title: str


class PinSessionRequest(BaseModel):
    session_id: str
    pinned: bool


class ChatHistoryItem(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    chat_id: str | None = None
    create_time: str
