from pydantic import BaseModel


class ChatRequest(BaseModel):
    session_id: str
    message: str
    enable_search: bool = False
    reply_model: str | None = None   # 前端选择的"最终 AI 回复"模型；None/非法时回退 CHAT_MODEL
    show_reasoning: bool = False     # 是否对最终回复开启思维链（开关含义：模型推理开关 + 推理过程是否上屏）


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


class DiagnoseRequest(BaseModel):
    error_id: int


class ChatHistoryItem(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    chat_id: str | None = None
    create_time: str
