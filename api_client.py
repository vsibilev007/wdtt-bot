"""
Клиент для WDTT Panel API.

Авторизация через cookie (POST /login → wdtt-panel cookie).
Все ответы: { success: true, obj: {...} } или { success: false, msg: "..." }
"""

from __future__ import annotations

import asyncio
import json as json_lib
import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=10, connect=3)

# Ограничение параллельных запросов к одному инстансу API
_semaphores: dict[str, asyncio.Semaphore] = {}


def _get_semaphore(base_url: str) -> asyncio.Semaphore:
    if base_url not in _semaphores:
        _semaphores[base_url] = asyncio.Semaphore(5)
    return _semaphores[base_url]


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 0):
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"[{code}] {message}")


class WdtClient:
    """Async HTTP клиент для WDTT Panel API с cookie-сессией."""

    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._cookies: dict[str, str] = {}

    async def login(self) -> bool:
        """POST /login — получает cookie wdtt-panel."""
        url = f"{self.base_url}/login"
        sem = _get_semaphore(self.base_url)
        async with sem:
            try:
                # Используем CookieJar для автоматического сбора cookie
                jar = aiohttp.CookieJar(unsafe=True)
                async with aiohttp.ClientSession(timeout=TIMEOUT, cookie_jar=jar) as session:
                    async with session.post(
                        url,
                        data={"username": self.username, "password": self.password},
                    ) as resp:
                        logger.debug("WDTT login %s → %d", url, resp.status)

                        # Собираем cookie из jar
                        cookies = {}
                        for cookie in jar:
                            cookies[cookie.key] = cookie.value

                        if cookies:
                            self._cookies = cookies
                            logger.debug("WDTT login OK: %s, cookies: %s", self.base_url, list(cookies.keys()))
                            return True

                        # Фолбэк: пробуем из resp.cookies
                        for cookie in resp.cookies.values():
                            cookies[cookie.key] = cookie.value
                        if cookies:
                            self._cookies = cookies
                            logger.debug("WDTT login OK (resp.cookies): %s", self.base_url)
                            return True

                        logger.warning("WDTT login: нет cookie в ответе")
                        return resp.status == 200
            except Exception as e:
                logger.error("WDTT login error: %s", e)
                return False

    async def _request(
        self,
        method: str,
        path: str,
        json: Any = None,
        auto_login: bool = True,
    ) -> dict:
        """HTTP request с auto-re-login на 401."""
        url = f"{self.base_url}{path}"

        # Cookie передаём через заголовок — надёжнее чем через параметр cookies
        headers = {}
        if self._cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
            headers["Cookie"] = cookie_str

        # CSRF token для POST/PUT/DELETE запросов
        if method.upper() in ("POST", "PUT", "DELETE", "PATCH"):
            csrf = self._cookies.get("wdtt-csrf", "")
            if csrf:
                headers["X-CSRF-Token"] = csrf

        sem = _get_semaphore(self.base_url)
        async with sem:
            try:
                async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
                    async with session.request(method, url, json=json, headers=headers) as resp:
                        # Если 401 — пробуем перелогиниться
                        if resp.status == 401 and auto_login:
                            logger.debug("WDTT 401, re-login: %s (cookie=%s)", url, bool(self._cookies))
                            if await self.login():
                                return await self._request(
                                    method, path, json=json, auto_login=False,
                                )
                            raise ApiError("auth_failed", "Не удалось авторизоваться в WDTT")

                        text = await resp.text()
                        logger.debug("WDTT %s %s → %d, body=%d bytes, cookie=%s", method, path, resp.status, len(text), bool(self._cookies))

                        if not text.strip():
                            # Пустой ответ — некоторые POST-эндпоинты возвращают 200 без тела
                            if resp.status >= 400:
                                raise ApiError("http_error", f"HTTP {resp.status}", status=resp.status)
                            return {}

                        # Если ответ — HTML (а не JSON), значит cookie протухла или не отправилась
                        stripped = text.lstrip()
                        if stripped.startswith("<") and auto_login:
                            logger.debug("WDTT получил HTML вместо JSON, пробуем re-login: %s", url)
                            if await self.login():
                                return await self._request(
                                    method, path, json=json, auto_login=False,
                                )
                            raise ApiError("auth_failed", "Панель вернула HTML — авторизация не удалась")

                        try:
                            data = json_lib.loads(text)
                        except Exception:
                            # Ответ не JSON
                            logger.warning("WDTT не-JSON ответ: %s %s → %d, body=%s", method, path, resp.status, text[:200])
                            if resp.status >= 400:
                                raise ApiError("http_error", f"HTTP {resp.status}: {text[:200]}", status=resp.status)
                            raise ApiError("parse_error", f"Ответ не JSON: {text[:200]}", status=resp.status)

                        if not data.get("success"):
                            raise ApiError(
                                code="api_error",
                                message=data.get("msg", str(data)),
                                status=resp.status,
                            )
                        return data.get("obj", {})
            except aiohttp.ServerTimeoutError:
                raise ApiError("timeout", "Нет ответа от API за 10с")
            except aiohttp.ClientConnectorError as e:
                raise ApiError("unreachable", f"Не удалось подключиться: {e}")
            except ApiError:
                raise
            except Exception as e:
                raise ApiError("error", str(e)[:200])

    # ─── Status ───────────────────────────────────────────────────────────────

    async def get_status(self) -> dict:
        """GET /panel/api/status — сводка: сервисы, IP, статистика."""
        return await self._request("GET", "/panel/api/status")

    # ─── Inbound ──────────────────────────────────────────────────────────────

    async def get_inbound(self) -> dict:
        """GET /panel/api/inbound — текущие настройки входа + статус."""
        return await self._request("GET", "/panel/api/inbound")

    async def save_inbound(self, data: dict) -> dict:
        """POST /panel/api/inbound/save — сохранить inbound и перезапустить WDTT."""
        return await self._request("POST", "/panel/api/inbound/save", json=data)

    # ─── Users ────────────────────────────────────────────────────────────────

    async def get_users(self) -> dict:
        """GET /panel/api/users — список пользователей + inbound для ссылок."""
        return await self._request("GET", "/panel/api/users")

    async def add_user(self, **kwargs) -> dict:
        """POST /panel/api/users/add — создать пользователя."""
        return await self._request("POST", "/panel/api/users/add", json=kwargs)

    async def update_user(self, **kwargs) -> dict:
        """POST /panel/api/users/update — обновить пользователя."""
        return await self._request("POST", "/panel/api/users/update", json=kwargs)

    async def delete_user(self, password: str) -> dict:
        """POST /panel/api/users/delete — удалить пользователя."""
        return await self._request("POST", "/panel/api/users/delete", json={"password": password})

    async def reset_traffic(self, password: str) -> dict:
        """POST /panel/api/users/reset-traffic — сбросить трафик."""
        return await self._request("POST", "/panel/api/users/reset-traffic", json={"password": password})

    # ─── Services ─────────────────────────────────────────────────────────────

    async def restart_wdtt(self) -> dict:
        """POST /panel/api/server/restartWdttService — перезапуск VPN."""
        return await self._request("POST", "/panel/api/server/restartWdttService")

    async def restart_xray(self) -> dict:
        """POST /panel/api/server/restartXrayService — перезапуск Xray."""
        return await self._request("POST", "/panel/api/server/restartXrayService")

    # ─── Password ─────────────────────────────────────────────────────────────

    async def change_main_password(self, password: str) -> dict:
        """POST /panel/api/password/main — сменить главный пароль VPN."""
        return await self._request("POST", "/panel/api/password/main", json={"password": password})

    # ─── Xray ─────────────────────────────────────────────────────────────────

    async def get_xray_config(self) -> dict:
        """GET /panel/api/xray/config — JSON-конфиг Xray."""
        return await self._request("GET", "/panel/api/xray/config")

    async def save_xray_config(self, config: dict) -> dict:
        """POST /panel/api/xray/config — сохранить конфиг + restart xray."""
        return await self._request("POST", "/panel/api/xray/config", json=config)

    async def get_xray_versions(self) -> dict:
        """GET /panel/api/xray/versions — доступные версии Xray."""
        return await self._request("GET", "/panel/api/xray/versions")

    async def install_xray(self, tag: str) -> dict:
        """POST /panel/api/xray/install/{tag} — установить версию Xray."""
        return await self._request("POST", f"/panel/api/xray/install/{tag}")

    # ─── Ping ─────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Проверка доступности панели."""
        try:
            await self.get_status()
            return True
        except Exception:
            return False


# ─── Кластерные операции ─────────────────────────────────────────────────────

@dataclass
class NodeResult:
    """Результат операции на одном узле кластера."""
    server_name: str
    ok: bool
    data: Any = None
    error: str = ""


async def cluster_read(servers: list, method_name: str, *args, **kwargs) -> Any:
    """Читает данные с первого доступного узла кластера."""
    last_error = None
    for srv in servers:
        try:
            client = WdtClient(srv.url, srv.username, srv.password)
            method = getattr(client, method_name)
            return await method(*args, **kwargs)
        except ApiError as e:
            last_error = e
            continue
    raise last_error or ApiError("unreachable", "Все узлы кластера недоступны")


async def cluster_write(
    servers: list,
    method_name: str,
    *args,
    **kwargs,
) -> list[NodeResult]:
    """Выполняет write-операцию параллельно на всех узлах кластера."""
    async def _call_one(srv) -> NodeResult:
        try:
            client = WdtClient(srv.url, srv.username, srv.password)
            method = getattr(client, method_name)
            data = await method(*args, **kwargs)
            return NodeResult(server_name=srv.name, ok=True, data=data)
        except ApiError as e:
            return NodeResult(server_name=srv.name, ok=False, error=f"{e.code}: {e.message}")
        except Exception as e:
            return NodeResult(server_name=srv.name, ok=False, error=str(e)[:100])

    results = await asyncio.gather(*[_call_one(srv) for srv in servers])
    return list(results)


async def cluster_users_with_nodes(servers: list) -> list[dict]:
    """
    Получает список пользователей со всех узлов кластера.
    Объединяет пользователей по password (уникальный идентификатор в WDTT).
    """
    async def _get_users_from(srv) -> tuple[str, dict]:
        try:
            client = WdtClient(srv.url, srv.username, srv.password)
            data = await client.get_users()
            return srv.name, data
        except Exception:
            return srv.name, {"users": []}

    results = await asyncio.gather(*[_get_users_from(srv) for srv in servers])

    users_by_pwd: dict[str, dict] = {}
    for srv_name, data in results:
        users = data.get("users", [])
        for u in users:
            pwd = u.get("password", "")
            if not pwd:
                continue
            if pwd not in users_by_pwd:
                users_by_pwd[pwd] = dict(u)
                users_by_pwd[pwd]["_nodes"] = {}
            users_by_pwd[pwd]["_nodes"][srv_name] = u.get("online", False)

    return list(users_by_pwd.values())
