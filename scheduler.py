"""
Фоновые задачи: мониторинг, сбор трафика, алерты
"""

from __future__ import annotations

import logging
import os
from functools import wraps
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from api_client import WdtClient, ApiError
from config import Config
import database as db

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_bot = None
_config: Config | None = None

HEARTBEAT_PATH = Path(os.environ.get("WDTT_BOT_HEARTBEAT_PATH", "/tmp/healthy"))


# ─── Декоратор: защищённый запуск джобы ─────────────────────────────────────

def safe_job(name: str):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                logger.error("Джоба [%s] упала с ошибкой: %s", name, e, exc_info=True)
        return wrapper
    return decorator


# ─── Setup ──────────────────────────────────────────────────────────────────

def setup(bot, config: Config):
    global _scheduler, _bot, _config
    _bot = bot
    _config = config
    _scheduler = AsyncIOScheduler(timezone="UTC")

    _scheduler.add_job(
        _collect_traffic,
        IntervalTrigger(minutes=15),
        id="collect_traffic",
        replace_existing=True,
    )
    _scheduler.add_job(
        _check_health,
        IntervalTrigger(minutes=2),
        id="check_health",
        replace_existing=True,
    )
    _scheduler.add_job(
        _cleanup,
        IntervalTrigger(hours=24),
        id="cleanup",
        replace_existing=True,
    )
    _scheduler.add_job(
        _heartbeat,
        IntervalTrigger(seconds=20),
        id="heartbeat",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler запущен (4 задачи)")


def stop():
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler остановлен")


@safe_job("heartbeat")
async def _heartbeat():
    HEARTBEAT_PATH.touch()


# ─── Сбор трафика ────────────────────────────────────────────────────────────

@safe_job("collect_traffic")
async def _collect_traffic():
    if not _config:
        return
    for srv in _config.servers:
        try:
            client = WdtClient(srv.url, srv.username, srv.password)
            data = await client.get_users()
            users = data.get("users", [])
            inbound = data.get("inbound", {})
            await db.save_traffic_snapshot(srv.name, users, inbound)
            logger.debug("Трафик собран: %s (%d пользователей)", srv.name, len(users))
        except Exception as e:
            logger.warning("Ошибка сбора трафика %s: %s", srv.name, e)


# ─── Проверка здоровья ───────────────────────────────────────────────────────

@safe_job("check_health")
async def _check_health():
    if not _config:
        return

    for srv in _config.servers:
        try:
            client = WdtClient(srv.url, srv.username, srv.password)
            status = await client.get_status()

            wdtt_active = status.get("wdtt_active", False)
            xray_active = status.get("xray_active", False)
            users_count = status.get("users_count", 0)

            await db.update_server_status(srv.name, wdtt_active, xray_active, users_count)

            # ── Алерты ────────────────────────────────────────────────────────
            thresholds = _config.thresholds

            if thresholds.wdtt_down and not wdtt_active:
                await _fire_alert(srv.name, "wdtt_down", f"WDTT сервис недоступен на {srv.name}")

            if thresholds.xray_down and not xray_active:
                await _fire_alert(srv.name, "xray_down", f"Xray сервис недоступен на {srv.name}")

            # Проверка лимита пользователей
            inbound = await client.get_inbound()
            max_users = inbound.get("max_users", 0)
            if max_users > 0 and thresholds.users_limit_pct > 0:
                limit = int(max_users * thresholds.users_limit_pct / 100)
                if users_count >= limit:
                    await _fire_alert(
                        srv.name, "users_limit",
                        f"Лимит пользователей: {users_count}/{max_users} на {srv.name}",
                    )

        except Exception as e:
            logger.warning("Ошибка health check %s: %s", srv.name, e)
            await _fire_alert(srv.name, "wdtt_down", f"Панель WDTT недоступна: {srv.name}")


async def _fire_alert(server_name: str, alert_type: str, message: str):
    """Отправляет алерт всем подписанным пользователям."""
    if await db.was_alert_fired_recently(server_name, alert_type, within_secs=300):
        return

    await db.log_alert(server_name, alert_type, message)
    logger.warning("ALERT [%s] %s: %s", server_name, alert_type, message)

    if not _bot or not _config:
        return

    for uid in _config.allowed_users:
        try:
            enabled = await db.get_alert(uid, server_name, alert_type)
            if enabled:
                await _bot.send_message(uid, f"🚨 <b>{alert_type}</b>\n{message}")
        except Exception as e:
            logger.debug("Не удалось отправить алерт %d: %s", uid, e)


# ─── Очистка ────────────────────────────────────────────────────────────────

@safe_job("cleanup")
async def _cleanup():
    await db.cleanup_old_data(days=30)
    logger.debug("Очистка старых данных завершена")
