"""
Менеджер серверных сессий — выбор активного сервера для пользователя.
Кеширование WdtClient для сохранения cookie между запросами.
"""

from __future__ import annotations

from api_client import WdtClient
from config import Config, ServerConfig
import database as db

# Кеш клиентов: url → WdtClient (чтобы cookie не терялась между запросами)
_client_cache: dict[str, WdtClient] = {}


async def get_server_index(user_id: int, config: Config) -> int:
    idx = await db.get_user_server_index(user_id, default=config.default_server)
    return max(0, min(idx, len(config.servers) - 1))


async def set_server_index(user_id: int, idx: int) -> None:
    await db.set_user_server_index(user_id, idx)


def get_cached_client(srv: ServerConfig) -> WdtClient:
    """Возвращает кешированный WdtClient для сервера (cookie сохраняется)."""
    key = f"{srv.url}:{srv.username}"
    if key not in _client_cache:
        _client_cache[key] = WdtClient(srv.url, srv.username, srv.password)
    return _client_cache[key]


async def get_client(user_id: int, config: Config) -> tuple[WdtClient, ServerConfig]:
    idx = await get_server_index(user_id, config)
    srv = config.servers[idx]
    return get_cached_client(srv), srv
