from pydantic import BaseModel


class FileListQuery(BaseModel):
    page: int = 1
    page_size: int = 20
    file_type: str | None = None
    keyword: str | None = None


class FileInfoOut(BaseModel):
    id: int
    file_name: str
    file_path: str
    file_size: int | None = None
    file_type: str | None = None
    file_extension: str | None = None
    session_id: str | None = None
    created_by: str | None = None
    created_at: str

    class Config:
        from_attributes = True
