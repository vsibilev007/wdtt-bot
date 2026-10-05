"""
Конфигурация бота из переменных окружения или .env файла
"""

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Применяем TZ сразу после загрузки .env
_tz_raw = os.environ.get("TZ", "")
_tz = _tz_raw.split("#")[0].strip() if _tz_raw else ""
if _tz:
    os.environ["TZ"] = _tz
    try:
        import time
        time.tzset()
    except AttributeError:
        pass


@dataclass
class ServerConfig:
    name: str
    url: str
    username: str = "admin"
    password: str = "wdtt"
    group: str = ""   # Имя кластерной группы. Пусто = одиночный сервер
    csqtt_port: int = 46000  # UDP-порт CSQTT-сервера (amurcanov/csqtt default)


@dataclass
class AlertThresholds:
    wdtt_down: bool = True
    xray_down: bool = True
    users_limit_pct: float = 80.0


@dataclass
class Config:
    bot_token: str
    allowed_users: list[int]
    servers: list[ServerConfig]
    thresholds: AlertThresholds = field(default_factory=AlertThresholds)
    default_server: int = 0
    tg_proxy_url: str = ""

    def get_group_members(self, server: ServerConfig) -> list[ServerConfig]:
        """Возвращает все узлы группы. Если сервер одиночный — только он."""
        if not server.group:
            return [server]
        return [s for s in self.servers if s.group == server.group]

    def get_menu_servers(self) -> list[ServerConfig]:
        """Список серверов для меню — группы показываем как один сервер."""
        seen_groups: set[str] = set()
        result: list[ServerConfig] = []
        for srv in self.servers:
            if srv.group:
                if srv.group not in seen_groups:
                    seen_groups.add(srv.group)
                    result.append(srv)
            else:
                result.append(srv)
        return result

    def is_cluster(self, server: ServerConfig) -> bool:
        """True если сервер входит в кластерную группу."""
        return bool(server.group) and len(self.get_group_members(server)) > 1


def _clean(val: str) -> str:
    return val.split("#")[0].strip()


def _int_env(key: str, default: int) -> int:
    try:
        return int(_clean(os.environ.get(key, str(default))))
    except (ValueError, TypeError):
        return default


def _float_env(key: str, default: float) -> float:
    try:
        return float(_clean(os.environ.get(key, str(default))))
    except (ValueError, TypeError):
        return default


def load_config() -> Config:
    bot_token = _clean(os.environ.get("BOT_TOKEN", ""))
    if not bot_token:
        raise RuntimeError("BOT_TOKEN не задан в .env или переменных окружения")

    allowed_raw = _clean(os.environ.get("ALLOWED_USERS", ""))
    allowed_users = [int(x) for x in allowed_raw.split(",") if x.strip().isdigit()]
    if not allowed_users:
        raise ValueError("ALLOWED_USERS не задан (укажите Telegram user_id через запятую)")

    # ── Серверы WDTT ──────────────────────────────────────────────────────────
    servers: list[ServerConfig] = []
    csqtt_port = _int_env("CSQTT_PORT", 46000)
    i = 1
    while True:
        url = _clean(os.environ.get(f"SERVER_{i}_URL", ""))
        if not url:
            break
        name = _clean(os.environ.get(f"SERVER_{i}_NAME", f"Server {i}"))
        username = _clean(os.environ.get(f"SERVER_{i}_USERNAME", "admin"))
        password = _clean(os.environ.get(f"SERVER_{i}_PASSWORD", "wdtt"))
        group = _clean(os.environ.get(f"SERVER_{i}_GROUP", ""))
        servers.append(ServerConfig(
            name=name, url=url.rstrip("/"),
            username=username, password=password, group=group,
            csqtt_port=_int_env(f"SERVER_{i}_CSQTT_PORT", csqtt_port),
        ))
        i += 1

    # Fallback: одиночный сервер
    if not servers:
        url = _clean(os.environ.get("SERVER_URL", "https://127.0.0.1:2860/wdtt"))
        name = _clean(os.environ.get("SERVER_NAME", "WDTT"))
        username = _clean(os.environ.get("SERVER_USERNAME", "admin"))
        password = _clean(os.environ.get("SERVER_PASSWORD", "wdtt"))
        servers.append(ServerConfig(
            name=name, url=url.rstrip("/"),
            username=username, password=password,
            csqtt_port=csqtt_port,
        ))

    thresholds = AlertThresholds(
        wdtt_down=_clean(os.environ.get("ALERT_WDTT_DOWN", "true")).lower() in ("true", "1", "yes"),
        xray_down=_clean(os.environ.get("ALERT_XRAY_DOWN", "true")).lower() in ("true", "1", "yes"),
        users_limit_pct=_float_env("ALERT_USERS_LIMIT_PCT", 80.0),
    )

    return Config(
        bot_token=bot_token,
        allowed_users=allowed_users,
        servers=servers,
        thresholds=thresholds,
        tg_proxy_url=_clean(os.environ.get("TELEGRAM_PROXY_URL", "")),
    )
