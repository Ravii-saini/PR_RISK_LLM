"""Redis client dependency for the FastAPI app."""
import redis

from config import get_settings


def get_redis() -> redis.Redis:
    settings = get_settings()
    return redis.Redis.from_url(settings.redis_url, decode_responses=True)
