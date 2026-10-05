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
    tg_proxy_url: str = ""
    csqtt_port: int = 46000  # UDP-порт CSQTT-сервера для ссылок csqtt://


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

    # ── Сервер WDTT ───────────────────────────────────────────────────────────
    url = _clean(os.environ.get("SERVER_URL", "https://127.0.0.1:2860/wdtt"))
    name = _clean(os.environ.get("SERVER_NAME", "WDTT"))
    username = _clean(os.environ.get("SERVER_USERNAME", "admin"))
    password = _clean(os.environ.get("SERVER_PASSWORD", "wdtt"))
    servers = [ServerConfig(name=name, url=url.rstrip("/"), username=username, password=password)]

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
        csqtt_port=_int_env("CSQTT_PORT", 46000),
    )
