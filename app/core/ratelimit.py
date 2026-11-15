import time
from collections import defaultdict, deque

from fastapi import Depends, Request

from app.api.deps import get_optional_user
from app.core.errors import ApiError
from app.models import User

_buckets: dict[str, deque] = defaultdict(deque)


def rate_limit(scope: str, max_calls: int, window_seconds: int):
    """In-memory sliding-window limiter keyed by user id (or IP when anonymous).

    Suitable for the single-instance deployment; swap for Redis if the API
    ever runs multi-process.
    """

    def dep(
        request: Request, user: User | None = Depends(get_optional_user)
    ) -> None:
        key = f"{scope}:{user.id if user else (request.client.host if request.client else 'unknown')}"
        now = time.monotonic()
        bucket = _buckets[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= max_calls:
            raise ApiError(429, "RATE_LIMITED", "Too many requests — slow down")
        bucket.append(now)

    return dep
