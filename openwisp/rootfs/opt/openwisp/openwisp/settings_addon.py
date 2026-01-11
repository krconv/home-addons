import os

from .settings import *  # noqa: F401,F403


def _env_truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


if _env_truthy(os.environ.get("DJANGO_CORS_ALLOW_ALL_ORIGINS", "")):
    CORS_ALLOW_ALL_ORIGINS = True
    CORS_ALLOWED_ORIGINS = []
