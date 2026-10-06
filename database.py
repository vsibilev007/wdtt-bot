"""
SQLite БД: история трафика, настройки алертов, сессии пользователей
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WDTT_BOT_DB_PATH", os.path.join(BASE_DIR, "wdtt_bot.db"))


# ─── Инициализация ────────────────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            -- История трафика
            CREATE TABLE IF NOT EXISTS traffic_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                server_name     TEXT NOT NULL,
                user_password   TEXT NOT NULL,
                traffic_used_fmt TEXT,
                traffic_bytes   INTEGER DEFAULT 0,
                online          INTEGER DEFAULT 0,
                sampled_at      INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_traffic_user
                ON traffic_history(server_name, user_password, sampled_at);
            CREATE INDEX IF NOT EXISTS idx_traffic_server_time
                ON traffic_history(server_name, sampled_at);

            -- Статус серверов
            CREATE TABLE IF NOT EXISTS server_status (
                server_name  TEXT PRIMARY KEY,
                wdtt_active  INTEGER DEFAULT 0,
                xray_active  INTEGER DEFAULT 0,
                users_count  INTEGER DEFAULT 0,
                checked_at   INTEGER NOT NULL DEFAULT 0
            );

            -- Настройки алертов пользователей
            CREATE TABLE IF NOT EXISTS alert_settings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                server_name TEXT NOT NULL,
                alert_type  TEXT NOT NULL,
                enabled     INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, server_name, alert_type)
            );

            -- Лог сработавших алертов
            CREATE TABLE IF NOT EXISTS alert_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                server_name TEXT NOT NULL,
                alert_type  TEXT NOT NULL,
                message     TEXT NOT NULL,
                fired_at    INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_alert_log_lookup
                ON alert_log(server_name, alert_type, fired_at);
        """)
        # Раньше снапшоты писались под маскированным password (bhv****) и
        # были недостижимы для UI (он ищет по полному password_key). Такие
        # строки только замусоривали отчёт по трафику — удаляем при старте.
        await db.execute(
            "DELETE FROM traffic_history WHERE user_password GLOB '*[*][*][*][*]'"
        )
        await db.commit()
    # История трафика хранит полные пароли VPN — ограничиваем доступ к файлу
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass
    logger.info("БД инициализирована: %s", DB_PATH)


# ─── Трафик ───────────────────────────────────────────────────────────────────

async def save_traffic_snapshot(server_name: str, users: list):
    now = _now()
    rows = []
    for u in users:
        fmt = u.get("traffic_used_fmt", "")
        if not fmt:
            # Нет данных — не пишем нулевую точку: она даёт фейковые
            # провалы/пики в дельтах на графике.
            continue
        # Ключ — полный password_key: все UI-пути (карточка, графики)
        # передают именно его, а password маскирован (bhv****).
        pwd = u.get("password_key", "") or u.get("password", "")
        if not pwd:
            continue
        rows.append((server_name, pwd, fmt, _parse_traffic_bytes(fmt), int(u.get("online", False)), now))
    if not rows:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO traffic_history(server_name, user_password, traffic_used_fmt, traffic_bytes, online, sampled_at)"
            " VALUES(?,?,?,?,?,?)",
            rows,
        )
        await db.commit()


def _parse_traffic_bytes(fmt: str) -> int:
    """Парсит '1.2 GB' → bytes."""
    if not fmt:
        return 0
    try:
        parts = fmt.strip().split()
        if len(parts) != 2:
            return 0
        val = float(parts[0])
        unit = parts[1].upper()
        multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(val * multipliers.get(unit, 1))
    except (ValueError, IndexError):
        return 0


async def get_traffic_history(server_name: str, user_password: str, days: int = 7) -> list[dict]:
    since = _now() - days * 86400
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT sampled_at, traffic_bytes, online
               FROM traffic_history
               WHERE server_name=? AND user_password=? AND sampled_at>=?
               ORDER BY sampled_at""",
            (server_name, user_password, since),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_traffic_delta(server_name: str, user_password: str, days: int = 7) -> dict:
    rows = await get_traffic_history(server_name, user_password, days)
    if len(rows) < 2:
        return {"delta_bytes": 0, "points": len(rows), "days": days}
    delta = rows[-1]["traffic_bytes"] - rows[0]["traffic_bytes"]
    return {
        "delta_bytes": max(0, delta),
        "points": len(rows),
        "days": days,
    }


async def get_all_users_traffic_delta(server_name: str, days: int = 7) -> list[dict]:
    """Дельта трафика всех пользователей за период."""
    since = _now() - days * 86400
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT user_password,
                      MAX(traffic_bytes) - MIN(traffic_bytes) AS delta_bytes,
                      MAX(online)          AS max_online,
                      COUNT(*)             AS points
               FROM traffic_history
               WHERE server_name=? AND sampled_at>=?
               GROUP BY user_password
               ORDER BY delta_bytes DESC""",
            (server_name, since),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ─── Статус серверов ──────────────────────────────────────────────────────────

async def update_server_status(
    server_name: str, wdtt_active: bool, xray_active: bool, users_count: int,
):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO server_status(server_name, wdtt_active, xray_active, users_count, checked_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(server_name) DO UPDATE SET
                 wdtt_active=excluded.wdtt_active,
                 xray_active=excluded.xray_active,
                 users_count=excluded.users_count,
                 checked_at=excluded.checked_at""",
            (server_name, int(wdtt_active), int(xray_active), users_count, _now()),
        )
        await db.commit()


async def get_server_status(server_name: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM server_status WHERE server_name=?", (server_name,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


# ─── Алерты ───────────────────────────────────────────────────────────────────

async def set_alert(user_id: int, server_name: str, alert_type: str, enabled: bool = True):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO alert_settings(user_id, server_name, alert_type, enabled)
               VALUES(?,?,?,?)
               ON CONFLICT(user_id, server_name, alert_type) DO UPDATE SET
                 enabled=excluded.enabled""",
            (user_id, server_name, alert_type, int(enabled)),
        )
        await db.commit()


async def get_alert(user_id: int, server_name: str, alert_type: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT enabled FROM alert_settings"
            " WHERE user_id=? AND server_name=? AND alert_type=?",
            (user_id, server_name, alert_type),
        ) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False


async def get_alerts(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM alert_settings WHERE user_id=? AND enabled=1", (user_id,)
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def log_alert(server_name: str, alert_type: str, message: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO alert_log(server_name, alert_type, message, fired_at) VALUES(?,?,?,?)",
            (server_name, alert_type, message, _now()),
        )
        await db.commit()


async def was_alert_fired_recently(server_name: str, alert_type: str, within_secs: int = 300) -> bool:
    since = _now() - within_secs
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM alert_log"
            " WHERE server_name=? AND alert_type=? AND fired_at>? LIMIT 1",
            (server_name, alert_type, since),
        ) as cur:
            return await cur.fetchone() is not None


async def get_recent_alerts(server_name: str, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT alert_type, message, fired_at
               FROM alert_log
               WHERE server_name=?
               ORDER BY fired_at DESC
               LIMIT ?""",
            (server_name, limit),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ─── Очистка ──────────────────────────────────────────────────────────────────

async def cleanup_old_data(days: int = 30):
    cutoff = _now() - days * 86400
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM traffic_history WHERE sampled_at<?", (cutoff,))
        await db.execute("DELETE FROM alert_log WHERE fired_at<?", (cutoff,))
        await db.commit()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _now() -> int:
    return int(datetime.now(timezone.utc).timestamp())
