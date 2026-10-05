"""
Кеширование WdtClient — cookie-сессия живёт между запросами.
"""

from __future__ import annotations

from api_client import WdtClient
from config import Config, ServerConfig

# Кеш клиентов: url → WdtClient (чтобы cookie не терялась между запросами)
_client_cache: dict[str, WdtClient] = {}


def get_cached_client(srv: ServerConfig) -> WdtClient:
    """Возвращает кешированный WdtClient для сервера (cookie сохраняется)."""
    key = f"{srv.url}:{srv.username}"
    if key not in _client_cache:
        _client_cache[key] = WdtClient(srv.url, srv.username, srv.password)
    return _client_cache[key]


def get_client(config: Config) -> tuple[WdtClient, ServerConfig]:
    """Единственный сконфигурированный сервер + кешированный клиент."""
    srv = config.servers[0]
    return get_cached_client(srv), srv
