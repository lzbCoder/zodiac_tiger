from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。metadata 使用 root schema（等价于原 root. 前缀）。"""
    pass


Base.metadata.schema = "root"
