from __future__ import annotations

from fastapi import HTTPException


def http_error(status_code: int, code: str, message: str, exc: Exception | None = None) -> HTTPException:
    detail: dict[str, str] = {
        "code": code,
        "message": message,
    }
    if exc is not None:
        detail["error"] = str(exc)
        detail["error_type"] = type(exc).__name__
    return HTTPException(status_code=status_code, detail=detail)
