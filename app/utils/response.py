from typing import Any

from fastapi.responses import JSONResponse


def success(data: Any = None, message: str = "success") -> JSONResponse:
    return JSONResponse(
        content={"code": 0, "message": message, "data": data}
    )


def fail(code: int = -1, message: str = "error", data: Any = None) -> JSONResponse:
    return JSONResponse(
        content={"code": code, "message": message, "data": data}
    )
