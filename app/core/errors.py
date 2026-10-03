from fastapi import HTTPException


class ApiError(HTTPException):
    """HTTPException carrying a stable machine-readable error code."""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(status_code=status_code)
        self.code = code
        self.message = message


def not_found(code: str, message: str) -> ApiError:
    return ApiError(404, code, message)


def forbidden(message: str = "You do not have access to this resource") -> ApiError:
    return ApiError(403, "FORBIDDEN", message)


def bad_request(code: str, message: str) -> ApiError:
    return ApiError(400, code, message)


def unauthorized(message: str = "Authentication required") -> ApiError:
    return ApiError(401, "UNAUTHORIZED", message)


def conflict(code: str, message: str) -> ApiError:
    return ApiError(409, code, message)
