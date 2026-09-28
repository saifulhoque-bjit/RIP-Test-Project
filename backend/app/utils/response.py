"""Generic API response envelope.

Usage:
    return ApiResponse.ok(data=my_schema, message="Created")
    return ApiResponse.error(message="Not found")
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    success: bool
    message: str
    data: T | None = None

    @classmethod
    def ok(
        cls,
        data: T | None = None,
        message: str = "Success",
    ) -> ApiResponse[T]:
        return cls(success=True, message=message, data=data)

    @classmethod
    def error(cls, message: str = "An error occurred") -> ApiResponse[None]:
        return cls(success=False, message=message, data=None)
