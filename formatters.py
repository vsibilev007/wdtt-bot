"""
Форматтеры ответов API в читаемый текст для Telegram
"""

from __future__ import annotations

import math
from typing import Optional
from urllib.parse import quote

import tz as _tz


def fmt_bytes(b: Optional[int]) -> str:
    if b is None:
        return "—"
    if b == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(b, 1024)))
    i = min(i, len(units) - 1)
    p = math.pow(1024, i)
    return f"{b / p:.1f} {units[i]}"


def fmt_bool(v: bool) -> str:
    return "✅" if v else "❌"


# ─── Dashboard / Status ──────────────────────────────────────────────────────

def format_status(obj: dict, server_name: str = "") -> str:
    """Форматирует GET /panel/api/status."""
    lines = [f"<b>📊 Панель WDTT</b>"]
    if server_name:
        lines.append(f"Сервер: {server_name}")
    lines.append("")

    lines.append(f"IP: {obj.get('server_ip', '—')}")
    lines.append(f"WDTT: {fmt_bool(obj.get('wdtt_active', False))}")
    lines.append(f"Xray: {fmt_bool(obj.get('xray_active', False))}")
    lines.append(f"Интерфейс: {obj.get('wdtt_iface', '—')}")
    lines.append(f"Пользователей: <b>{obj.get('users_count', 0)}</b>")

    main_pwd = obj.get("main_password", "")
    if main_pwd:
        lines.append(f"Главный пароль: <code>{main_pwd}</code>")

    stats = obj.get("stats", {})
    if stats:
        lines.append("")
        lines.append("<b>Статистика:</b>")
        for key, val in stats.items():
            if key == "online" and isinstance(val, list):
                lines.append(f"  Онлайн сессий: {len(val)}")
                continue
            lines.append(f"  {key}: {val}")

    return "\n".join(lines)


def format_online_sessions(obj: dict) -> str:
    """Форматирует онлайн-сессии из stats.online."""
    stats = obj.get("stats", {})
    online = stats.get("online", [])

    lines = ["<b>🟢 Онлайн сессии</b>\n"]

    if not online:
        lines.append("Нет активных сессий.")
        return "\n".join(lines)

    lines.append(f"Всего: {len(online)}\n")

    for s in online:
        user = s.get("user", "?")
        ip = s.get("ip", "?")
        mode = s.get("mode", "?")
        device = s.get("device_id", "")
        sessions = s.get("sessions", "1")

        lines.append(f"<b>{user}</b>")
        lines.append(f"  IP: {ip} | Режим: {mode} | Сессий: {sessions}")
        if device:
            lines.append(f"  Устройство: {device[:8]}…")

    return "\n".join(lines)


# ─── Users ────────────────────────────────────────────────────────────────────

def format_users_list(users: list, page: int = 0, per_page: int = 10) -> str:
    """Форматирует заголовок списка пользователей."""
    if not users:
        return "<b>👥 Пользователи</b>\n\nНет пользователей."

    total = len(users)
    online = sum(1 for u in users if u.get("online"))

    return f"<b>👥 Клиенты: {online} / {total}</b>\n"


def format_user_detail(user: dict) -> str:
    """Форматирует детальную информацию о пользователе."""
    comment = user.get("comment", "")
    # password_key — полный пароль, password — маскированный
    pwd = user.get("password_key", "") or user.get("password", "")
    label = comment if comment else pwd[:16]

    lines = [f"<b>👤 {label}</b>\n"]

    lines.append(f"Пароль: <code>{pwd}</code>")
    lines.append(f"Активен: {fmt_bool(user.get('active', True))}")
    lines.append(f"Онлайн: {fmt_bool(user.get('online', False))}")

    expires = user.get("expires", "бессрочно")
    lines.append(f"Истекает: {expires}")

    total_gb = user.get("total_gb", 0)
    if total_gb:
        lines.append(f"Лимит трафика: {total_gb} GB")
    else:
        lines.append("Лимит трафика: без лимита")

    traffic = user.get("traffic_used_fmt", "")
    if traffic:
        lines.append(f"Использовано: {traffic}")

    devices_bound = user.get("devices_bound", 0)
    max_devices = user.get("max_devices", 1)
    lines.append(f"Устройства: {devices_bound}/{max_devices}")

    device_ids = user.get("device_ids", [])
    if device_ids:
        lines.append(f"Device IDs: {', '.join(device_ids[:3])}")

    return "\n".join(lines)


def format_user_link(user: dict, inbound: dict = None, csqtt_port: int = 46000) -> str:
    """Форматирует ссылки пользователя: csqtt:// и wdtt:// (colon-формат)."""
    comment = user.get("comment", "")
    # password_key — полный пароль, password — маскированный
    pwd = user.get("password_key", "") or user.get("password", "")
    label = comment if comment else pwd[:12]

    # Данные из inbound
    host = ""
    dtls_port = 56000
    wg_port = 56001
    client_port = 9000
    if inbound:
        # default_link_host — основной хост для ссылок
        host = inbound.get("default_link_host", "") or inbound.get("server_host", "") or ""
        dtls_port = inbound.get("dtls_port", 56000)
        wg_port = inbound.get("wg_port", 56001)
        client_port = inbound.get("client_port", 9000)

    # Фолбэк: декодируем wdtt:// ссылку для получения хоста
    link = user.get("link", "")
    if not host and link:
        try:
            import base64, json as _json
            if link.startswith("wdtt://"):
                payload = link[7:]
                decoded = _json.loads(base64.b64decode(payload))
                host = decoded.get("ip", "") or decoded.get("add", "")
        except Exception:
            pass

    if not host:
        host = "?"

    # VK hash из user
    vk_hash = user.get("vk_hash", "")

    # CSQTT (WRAP CSQTT-WRAP-v1 + VKQUIC): csqtt://password@host:peerPort.
    # Peer-порт — порт CSQTT-сервера (amurcanov/csqtt, по умолчанию 46000),
    # он НЕ совпадает с dtls_port. В ссылке нет ни vk-хеша, ни device_id —
    # клиент авторизуется в VK сам. Пароль percent-кодируется — клиент
    # делает removingPercentEncoding.
    csqtt_link = f"csqtt://{quote(pwd, safe='')}@{host}:{csqtt_port}"

    lines = [f"<b>🔗 Ссылки — {label}</b>\n"]

    lines.append("<b>CSQTT — WRAP v1 + VKQUIC</b>")
    lines.append(f"<code>{csqtt_link}</code>\n")

    # Формируем colon-ссылки: wdtt://host:dtls:wg:local:pass:hash[#name]
    def _colon(local_port: int, hash_limit: int = 0, with_name: bool = False) -> str:
        h = vk_hash
        if hash_limit and h:
            parts = h.split(",")
            h = ",".join(parts[:hash_limit])
        name_suffix = f"#{comment}" if (with_name and comment) else ""
        link = f"wdtt://{host}:{dtls_port}:{wg_port}:{local_port}:{pwd}:{h}{name_suffix}"
        return f"<code>{link}</code>"

    # iOS — VK Turn Proxy (1 hash, local=0)
    ios_link = _colon(0, hash_limit=1)
    lines.append(f"<b>iOS — VK Turn Proxy</b>")
    lines.append(f"{ios_link}\n")

    # Android — WDTT (up to 4 hashes, local=client_port)
    android_link = _colon(client_port)
    lines.append(f"<b>Android — WDTT</b>")
    lines.append(f"{android_link}\n")

    # PWDTT — Desktop Win/Linux (up to 4 hashes, with name)
    desktop_link = _colon(0, with_name=True)
    lines.append(f"<b>PWDTT — Desktop</b>")
    lines.append(f"{desktop_link}\n")

    # WDTT — Windows (up to 4 hashes, with name)
    win_link = _colon(0, with_name=True)
    lines.append(f"<b>WDTT — Windows</b>")
    lines.append(f"{win_link}")

    return "\n".join(lines)


# ─── Inbound ──────────────────────────────────────────────────────────────────

def format_inbound(obj: dict) -> str:
    """Форматирует GET /panel/api/inbound."""
    lines = ["<b>🔧 Настройки подключения (Inbound)</b>\n"]

    lines.append(f"Tag: <code>{obj.get('tag', '—')}</code>")
    lines.append(f"Remark: {obj.get('remark', '—')}")
    lines.append(f"Listen: <code>{obj.get('listen_host', '—')}</code>")
    lines.append(f"DTLS порт: <code>{obj.get('dtls_port', '—')}</code>")
    lines.append(f"WG порт: <code>{obj.get('wg_port', '—')}</code>")
    lines.append(f"Клиентский порт: <code>{obj.get('client_port', '—')}</code>")
    lines.append(f"DNS: <code>{obj.get('dns', '—')}</code>")
    lines.append(f"Макс. пользователей: <b>{obj.get('max_users', '—')}</b>")

    lines.append("")
    lines.append(f"WDTT сервис: {fmt_bool(obj.get('service_active', False))}")
    lines.append(f"Интерфейс: {fmt_bool(obj.get('iface_up', False))}")
    lines.append(f"DTLS: {fmt_bool(obj.get('dtls_listening', False))}")
    lines.append(f"WireGuard: {fmt_bool(obj.get('wg_listening', False))}")
    lines.append(f"Активных: {obj.get('active_users', 0)}")
    lines.append(f"Онлайн: {obj.get('online_users', 0)}")
    lines.append(f"Xray: {fmt_bool(obj.get('xray_active', False))}")

    return "\n".join(lines)


# ─── Services ─────────────────────────────────────────────────────────────────

def format_services(status: dict) -> str:
    """Форматирует статус сервисов."""
    lines = ["<b>🔄 Сервисы</b>\n"]

    lines.append(f"WDTT: {fmt_bool(status.get('wdtt_active', False))}")
    lines.append(f"Xray: {fmt_bool(status.get('xray_active', False))}")

    iface = status.get("wdtt_iface", "")
    if iface:
        lines.append(f"Интерфейс: <code>{iface}</code>")

    ip = status.get("server_ip", "")
    if ip:
        lines.append(f"IP: <code>{ip}</code>")

    return "\n".join(lines)


# ─── Xray ─────────────────────────────────────────────────────────────────────

def format_xray_config(config: dict) -> str:
    """Форматирует Xray конфиг (сокращённо — полный конфиг слишком длинный)."""
    import json
    lines = ["<b>📡 Xray конфиг</b>\n"]

    # Показываем краткую сводку
    inbounds = config.get("inbounds", [])
    outbounds = config.get("outbounds", [])
    routing = config.get("routing", {})
    dns = config.get("dns", {})

    lines.append(f"Inbounds: {len(inbounds)}")
    for ib in inbounds[:5]:
        proto = ib.get("protocol", "?")
        port = ib.get("port", "?")
        tag = ib.get("tag", "")
        lines.append(f"  • {proto} :{port} ({tag})")

    lines.append(f"\nOutbounds: {len(outbounds)}")
    for ob in outbounds[:5]:
        proto = ob.get("protocol", "?")
        tag = ob.get("tag", "")
        lines.append(f"  • {proto} ({tag})")

    rules = routing.get("rules", [])
    lines.append(f"\nRouting rules: {len(rules)}")
    servers = dns.get("servers", [])
    if servers:
        lines.append(f"DNS: {', '.join(str(s) for s in servers[:3])}")

    # Полный JSON — только если помещается
    try:
        pretty = json.dumps(config, indent=2, ensure_ascii=False)
        if len(pretty) <= 3000:
            lines.append(f"\n<pre>{pretty}</pre>")
        else:
            lines.append(f"\n<i>Полный конфиг слишком длинный ({len(pretty)} символов)</i>")
    except Exception:
        pass

    return "\n".join(lines)


def format_xray_versions(versions: dict) -> str:
    """Форматирует список версий Xray."""
    lines = ["<b>📡 Версии Xray</b>\n"]
    # Структура ответа зависит от WDTT
    if isinstance(versions, list):
        for v in versions[:20]:
            lines.append(f"  • {v}")
    elif isinstance(versions, dict):
        for k, v in versions.items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


# ─── Alerts ───────────────────────────────────────────────────────────────────

ALERT_TYPES = {
    "wdtt_down": "WDTT сервис недоступен",
    "xray_down": "Xray сервис недоступен",
    "users_limit": "Лимит пользователей",
}


def format_alerts(alert_states: dict[str, bool]) -> str:
    """Форматирует настройки алертов."""
    lines = ["<b>🚨 Настройки алертов</b>\n"]
    for atype, desc in ALERT_TYPES.items():
        state = alert_states.get(atype, False)
        icon = "🔔" if state else "🔕"
        lines.append(f"{icon} {desc}")
    return "\n".join(lines)


# ─── Alert log ────────────────────────────────────────────────────────────────

def format_alert_log(alerts: list) -> str:
    """Форматирует историю алертов."""
    lines = ["<b>📋 История алертов</b>\n"]
    if not alerts:
        lines.append("Нет сработавших алертов.")
        return "\n".join(lines)
    for a in alerts:
        atype = a.get("alert_type", "")
        msg = a.get("message", "")
        fired = a.get("fired_at", 0)
        dt_str = _tz.fmt_datetime(fired) if fired else "—"
        lines.append(f"• <b>{atype}</b> — {dt_str}")
        if msg:
            lines.append(f"  {msg[:100]}")
    return "\n".join(lines)
