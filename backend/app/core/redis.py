from redis.asyncio import Redis

from app.core.config import get_settings

_redis_client: Redis | None = None


async def init_redis() -> Redis:
    global _redis_client
    settings = get_settings()
    _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    await _redis_client.ping()
    return _redis_client


def get_redis() -> Redis:
    if _redis_client is None:
        raise RuntimeError("Redis client is not initialized")
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client is not None:
        if hasattr(_redis_client, "aclose"):
            await _redis_client.aclose()
        else:
            await _redis_client.close()
        _redis_client = None
