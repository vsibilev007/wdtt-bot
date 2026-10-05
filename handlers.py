"""
Обработчики команд и callback-кнопок Telegram-бота
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import secrets
from datetime import datetime, timezone, timedelta

import tz as _tz

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from api_client import ApiError, WdtClient, cluster_write, cluster_read, cluster_users_with_nodes, NodeResult
from config import Config
import database as db
from export_utils import users_to_csv, users_to_xlsx
from formatters import (
    format_status, format_users_list, format_user_detail, format_user_link,
    format_inbound, format_services, format_xray_config, format_xray_versions,
    format_alerts, format_alert_log, format_online_sessions, ALERT_TYPES,
)
from keyboards import (
    main_menu_kb, dashboard_kb, users_list_kb, user_detail_kb, user_edit_kb,
    user_delete_confirm_kb, user_traffic_kb, inbound_kb, services_kb,
    service_confirm_kb, xray_kb, alerts_kb, export_menu_kb, traffic_report_kb,
    back_kb, noop_kb,
)
from session import get_client, get_cached_client, get_server_index, set_server_index
import charts
from states import AddUserFSM, EditFieldFSM, SearchUserFSM, InboundEditFSM
from database import set_alert, get_alert

logger = logging.getLogger(__name__)

router = Router()

# Валидация имени пользователя
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _is_command(message: Message) -> bool:
    if not message.text or not message.text.startswith("/"):
        return False
    FSM_INTERNALS = {"/skip", "/gen"}
    cmd = message.text.split()[0].lower()
    return cmd not in FSM_INTERNALS


def _uid(event) -> int:
    if isinstance(event, CallbackQuery):
        return event.from_user.id
    return event.from_user.id


def _format_cluster_result(results: list[NodeResult]) -> str:
    lines = []
    for r in results:
        icon = "✅" if r.ok else "❌"
        lines.append(f"{icon} <b>{r.server_name}</b>" + (f": {r.error}" if not r.ok else ""))
    return "\n".join(lines)


async def _api_call(target, func, *args, **kwargs):
    """Обёртка для API-вызовов с обработкой ошибок."""
    try:
        return await func(*args, **kwargs)
    except ApiError as e:
        msg = f"❌ Ошибка API: {e.message}"
        if isinstance(target, CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return None
    except Exception as e:
        msg = f"❌ Ошибка: {e}"
        if isinstance(target, CallbackQuery):
            await target.answer(msg, show_alert=True)
        else:
            await target.answer(msg)
        return None


async def _safe_edit(cq: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup = None):
    """Безопасное редактирование сообщения."""
    try:
        await cq.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest:
        pass
    except Exception as e:
        logger.debug("Ошибка edit_text: %s", e)
    finally:
        await cq.answer()


async def _fetch_users(user_id: int, config: Config) -> tuple[list[dict], dict]:
    """Пользователи + inbound. На кластере — merge со всех узлов, inbound с живого узла."""
    idx = await get_server_index(user_id, config)
    srv = config.servers[idx]
    if config.is_cluster(srv):
        members = config.get_group_members(srv)
        users = await cluster_users_with_nodes(members)
        inbound = await cluster_read(members, "get_inbound")
        return users, inbound
    client = get_cached_client(srv)
    data = await client.get_users()
    return data.get("users", []), data.get("inbound", {})


# ─── /start ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, config: Config):
    await state.clear()
    idx = await get_server_index(message.from_user.id, config)
    srv = config.servers[idx]
    text = (
        f"<b>WDTT Manager Bot</b>\n\n"
        f"Сервер: <b>{srv.name}</b>\n"
        f"URL: <code>{srv.url}</code>\n\n"
        f"Выберите действие:"
    )
    await message.answer(text, reply_markup=main_menu_kb(config.servers, idx, config))


# ─── /menu ───────────────────────────────────────────────────────────────────

@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, config: Config):
    await state.clear()
    idx = await get_server_index(message.from_user.id, config)
    srv = config.servers[idx]

    client = WdtClient(srv.url, srv.username, srv.password)
    status = await _api_call(message, client.get_status)
    if status is None:
        await message.answer("Меню:", reply_markup=main_menu_kb(config.servers, idx, config))
        return

    text = format_status(status, srv.name)
    await message.answer(text, reply_markup=main_menu_kb(config.servers, idx, config))


# ─── /id ─────────────────────────────────────────────────────────────────────

@router.message(Command("id"))
async def cmd_id(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "<b>📖 Справка по командам</b>\n\n"
        "<b>/start</b> — Приветствие и главное меню\n"
        "<b>/menu</b> — Главное меню (Dashboard)\n"
        "<b>/find запрос</b> — Поиск пользователя по паролю или комментарию\n"
        "<b>/id</b> — Ваш Telegram ID\n"
        "<b>/cancel</b> — Отменить текущее действие\n"
        "<b>/help</b> — Эта справка\n\n"
        "<b>Меню бота:</b>\n"
        "📊 <b>Dashboard</b> — статус сервисов, IP, количество пользователей\n"
        "👥 <b>Пользователи</b> — список, создание, редактирование, удаление\n"
        "🔧 <b>Inbound</b> — настройки подключения (порты, DNS)\n"
        "🔄 <b>Сервисы</b> — перезапуск WDTT и Xray\n"
        "📡 <b>Xray</b> — конфиг и версии Xray\n"
        "🚨 <b>Алерты</b> — уведомления при проблемах\n"
        "➕ <b>Новый клиент</b> — создание пользователя\n"
        "📤 <b>Экспорт</b> — выгрузка в CSV/Excel\n"
    )
    await message.answer(text)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Действие отменено. /menu — главное меню.")


# ─── /find ───────────────────────────────────────────────────────────────────

@router.message(Command("find"))
async def cmd_find(message: Message, state: FSMContext):
    await message.answer("Введите пароль или комментарий для поиска:")
    await state.set_state(SearchUserFSM.waiting_query)


@router.message(SearchUserFSM.waiting_query)
async def process_search(message: Message, state: FSMContext, config: Config):
    await state.clear()
    query = message.text.lower().strip()

    users, _inbound = await _api_call(message, _fetch_users, message.from_user.id, config)
    if users is None:
        return

    found = [
        u for u in users
        if query in (u.get("password_key", "") or u.get("password", "")).lower()
        or query in u.get("comment", "").lower()
    ]

    if not found:
        await message.answer(f"Ничего не найдено по запросу: <b>{query}</b>")
        return

    text = format_users_list(found, page=0)
    await message.answer(text, reply_markup=users_list_kb(found, page=0))


# ─── Menu callbacks ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:main")
async def cb_menu_main(cq: CallbackQuery, config: Config):
    idx = await get_server_index(cq.from_user.id, config)
    srv = config.servers[idx]

    client = WdtClient(srv.url, srv.username, srv.password)
    status = await _api_call(cq, client.get_status)
    if status is None:
        return

    text = format_status(status, srv.name)
    await _safe_edit(cq, text, main_menu_kb(config.servers, idx, config))


@router.callback_query(F.data == "menu:dashboard")
async def cb_dashboard(cq: CallbackQuery, config: Config):
    idx = await get_server_index(cq.from_user.id, config)
    srv = config.servers[idx]

    client = WdtClient(srv.url, srv.username, srv.password)
    status = await _api_call(cq, client.get_status)
    if status is None:
        return

    text = format_status(status, srv.name)
    await _safe_edit(cq, text, dashboard_kb())


@router.callback_query(F.data == "dashboard:refresh")
async def cb_dashboard_refresh(cq: CallbackQuery, config: Config):
    await cb_dashboard(cq, config)


@router.callback_query(F.data == "menu:online")
async def cb_online_sessions(cq: CallbackQuery, config: Config):
    client, srv = await get_client(cq.from_user.id, config)
    status = await _api_call(cq, client.get_status)
    if status is None:
        return

    text = format_online_sessions(status)
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Обновить", callback_data="menu:online")
    kb.button(text="◀️ Меню", callback_data="menu:main")
    kb.adjust(2)
    await _safe_edit(cq, text, kb.as_markup())


# ─── Server select ───────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("server:select:"))
async def cb_server_select(cq: CallbackQuery, config: Config):
    try:
        idx = int(cq.data.split(":")[2])
    except (IndexError, ValueError):
        await cq.answer("Ошибка")
        return

    if idx < 0 or idx >= len(config.servers):
        await cq.answer("Сервер не найден")
        return

    await set_server_index(cq.from_user.id, idx)
    await cq.answer(f"Сервер: {config.servers[idx].name}")

    srv = config.servers[idx]
    client = WdtClient(srv.url, srv.username, srv.password)
    status = await _api_call(cq, client.get_status)
    if status is None:
        return

    text = format_status(status, srv.name)
    await _safe_edit(cq, text, main_menu_kb(config.servers, idx, config))


# ─── Users list ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:users")
async def cb_users_list(cq: CallbackQuery, config: Config):
    users, _inbound = await _api_call(cq, _fetch_users, cq.from_user.id, config)
    if users is None:
        return

    text = format_users_list(users, page=0)
    await _safe_edit(cq, text, users_list_kb(users, page=0))


@router.callback_query(F.data.startswith("users:page:"))
async def cb_users_page(cq: CallbackQuery, config: Config):
    try:
        page = int(cq.data.split(":")[2])
    except (IndexError, ValueError):
        await cq.answer("Ошибка")
        return

    users, _inbound = await _api_call(cq, _fetch_users, cq.from_user.id, config)
    if users is None:
        return

    text = format_users_list(users, page=page)
    await _safe_edit(cq, text, users_list_kb(users, page=page))


# ─── User detail ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:view:"))
async def cb_user_view(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:view:"):]

    users, inbound = await _api_call(cq, _fetch_users, cq.from_user.id, config)
    if users is None:
        return

    user = next((u for u in users if (u.get("password_key", "") or u.get("password", "")) == password), None)
    if not user:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    text = format_user_detail(user, inbound)
    await _safe_edit(cq, text, user_detail_kb(password, user.get("active", True)))


# ─── User toggle ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:toggle:"))
async def cb_user_toggle(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:toggle:"):]

    # Получаем текущее состояние
    users, _inbound = await _api_call(cq, _fetch_users, cq.from_user.id, config)
    if users is None:
        return

    user = next((u for u in users if (u.get("password_key", "") or u.get("password", "")) == password), None)
    if not user:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    new_active = not user.get("active", True)

    idx = await get_server_index(cq.from_user.id, config)
    srv = config.servers[idx]
    if config.is_cluster(srv):
        results = await cluster_write(
            config.get_group_members(srv), "update_user",
            old_password=password, active=new_active,
        )
        if not any(r.ok for r in results):
            await cq.answer(f"❌ {_format_cluster_result(results)}", show_alert=True)
            return
    else:
        client, _srv = await get_client(cq.from_user.id, config)
        result = await _api_call(cq, client.update_user, old_password=password, active=new_active)
        if result is None:
            return

    await cq.answer("✅ Готово")
    # Обновляем view
    await cb_user_view(cq, config)


# ─── User delete ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:delete:"))
async def cb_user_delete(cq: CallbackQuery):
    password = cq.data[len("user:delete:"):]
    text = f"<b>⚠️ Удалить пользователя?</b>\n\nПароль: <code>{password}</code>"
    await _safe_edit(cq, text, user_delete_confirm_kb(password))


@router.callback_query(F.data.startswith("user:delete_confirm:"))
async def cb_user_delete_confirm(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:delete_confirm:"):]

    if config.is_cluster(config.servers[await get_server_index(cq.from_user.id, config)]):
        members = config.get_group_members(config.servers[await get_server_index(cq.from_user.id, config)])
        results = await cluster_write(members, "delete_user", password)
        text = _format_cluster_result(results)
        await _safe_edit(cq, text, back_kb("menu:users"))
    else:
        client, srv = await get_client(cq.from_user.id, config)
        result = await _api_call(cq, client.delete_user, password)
        if result is None:
            return
        await _safe_edit(cq, "✅ Пользователь удалён", back_kb("menu:users"))


# ─── User link ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:link:"))
async def cb_user_link(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:link:"):]

    users, inbound = await _api_call(cq, _fetch_users, cq.from_user.id, config)
    if users is None:
        return

    user = next((u for u in users if (u.get("password_key", "") or u.get("password", "")) == password), None)
    if not user:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    text = format_user_link(user, inbound)
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data=f"user:view:{password}")
    await _safe_edit(cq, text, kb.as_markup())


# ─── User traffic ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:traffic:"))
async def cb_user_traffic(cq: CallbackQuery):
    password = cq.data[len("user:traffic:"):]
    text = f"<b>📊 Трафик — {password[:12]}…</b>\n\nВыберите период:"
    await _safe_edit(cq, text, user_traffic_kb(password))


@router.callback_query(F.data.startswith("user:traffic_period:"))
async def cb_user_traffic_period(cq: CallbackQuery, config: Config):
    parts = cq.data.split(":")
    password = parts[2]
    days = int(parts[3])

    client, srv = await get_client(cq.from_user.id, config)

    # Получаем историю из БД
    rows = await db.get_traffic_history(srv.name, password, days)
    if len(rows) < 2:
        await cq.answer("Недостаточно данных за этот период", show_alert=True)
        return

    delta = await db.get_traffic_delta(srv.name, password, days)
    text = (
        f"<b>📊 Трафик — {password[:12]}…</b>\n"
        f"Период: {days}д\n"
        f"Дельта: {delta['delta_bytes']} байт ({delta['points']} точек)"
    )
    await _safe_edit(cq, text, user_traffic_kb(password))


@router.callback_query(F.data.startswith("user:traffic_chart:"))
async def cb_user_traffic_chart(cq: CallbackQuery, config: Config):
    parts = cq.data.split(":")
    password = parts[2]
    days = int(parts[3])

    client, srv = await get_client(cq.from_user.id, config)

    rows = await db.get_traffic_history(srv.name, password, days)
    buf = charts.render_user_traffic(rows, password, days, srv.name)
    if buf is None:
        await cq.answer("Недостаточно данных или matplotlib не установлен", show_alert=True)
        return

    photo = BufferedInputFile(buf.read(), filename="traffic.png")
    await cq.message.answer_photo(photo, caption=f"📊 Трафик {password[:12]}… ({days}д)")
    await cq.answer()


# ─── User reset traffic ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:reset_traffic:"))
async def cb_user_reset_traffic(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:reset_traffic:"):]

    idx = await get_server_index(cq.from_user.id, config)
    srv = config.servers[idx]
    if config.is_cluster(srv):
        results = await cluster_write(config.get_group_members(srv), "reset_traffic", password)
        if not any(r.ok for r in results):
            await cq.answer(f"❌ {_format_cluster_result(results)}", show_alert=True)
            return
    else:
        client, _srv = await get_client(cq.from_user.id, config)
        if await _api_call(cq, client.reset_traffic, password) is None:
            return

    await cq.answer("✅ Трафик сброшен")
    await cb_user_view(cq, config)


# ─── Add user (FSM) ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "user:add")
async def cb_user_add(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await cq.message.answer(
        "➕ <b>Создание пользователя</b>\n\n"
        "Введите комментарий (имя) или /skip:"
    )
    await state.set_state(AddUserFSM.comment)


@router.message(AddUserFSM.comment)
async def adduser_comment(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    comment = message.text.strip() if message.text != "/skip" else ""
    await state.update_data(comment=comment)
    await message.answer("Введите пароль или /gen для автогенерации:")
    await state.set_state(AddUserFSM.password)


@router.message(AddUserFSM.password)
async def adduser_password(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    if message.text == "/gen":
        pwd = secrets.token_urlsafe(12)
    else:
        pwd = message.text.strip()
    await state.update_data(password=pwd)
    await message.answer("Срок действия в днях (0 = бессрочно) или /skip:")
    await state.set_state(AddUserFSM.expires_at)


@router.message(AddUserFSM.expires_at)
async def adduser_expires(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    text = message.text.strip() if message.text != "/skip" else "0"
    try:
        days = int(text)
    except ValueError:
        await message.answer("Введите число дней:")
        return
    await state.update_data(expires_days=days)
    await message.answer("Лимит трафика в GB (0 = без лимита) или /skip:")
    await state.set_state(AddUserFSM.total_gb)


@router.message(AddUserFSM.total_gb)
async def adduser_total_gb(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    text = message.text.strip() if message.text != "/skip" else "0"
    try:
        gb = int(text)
    except ValueError:
        await message.answer("Введите число GB:")
        return
    await state.update_data(total_gb=gb)
    await message.answer("Макс. устройств (1) или /skip:")
    await state.set_state(AddUserFSM.max_devices)


@router.message(AddUserFSM.max_devices)
async def adduser_max_devices(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    text = message.text.strip() if message.text != "/skip" else "1"
    try:
        devs = int(text)
    except ValueError:
        await message.answer("Введите число:")
        return
    await state.update_data(max_devices=devs)
    await message.answer("Max download Mbps (0 = без лимита) или /skip:")
    await state.set_state(AddUserFSM.max_down_mbps)


@router.message(AddUserFSM.max_down_mbps)
async def adduser_max_down(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    text = message.text.strip() if message.text != "/skip" else "0"
    try:
        mbps = int(text)
    except ValueError:
        await message.answer("Введите число:")
        return
    await state.update_data(max_down_mbps=mbps)
    await message.answer("Max upload Mbps (0 = без лимита) или /skip:")
    await state.set_state(AddUserFSM.max_up_mbps)


@router.message(AddUserFSM.max_up_mbps)
async def adduser_max_up(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    text = message.text.strip() if message.text != "/skip" else "0"
    try:
        mbps = int(text)
    except ValueError:
        await message.answer("Введите число:")
        return
    await state.update_data(max_up_mbps=mbps)
    await message.answer(
        "VK Hash (обязательно):\n"
        "Один или несколько хешей (до 4) через запятую или с новой строки."
    )
    await state.set_state(AddUserFSM.vk_hash)


@router.message(AddUserFSM.vk_hash)
async def adduser_vk_hash(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    vk_hash = message.text.strip()
    if not vk_hash:
        await message.answer("VK Hash обязателен. Введите хеш:")
        return
    # Нормализуем: запятые и переносы строк → запятые
    vk_hash = ",".join(
        h.strip() for h in vk_hash.replace("\n", ",").split(",") if h.strip()
    )
    await state.update_data(vk_hash=vk_hash)

    data = await state.get_data()
    expires_at = 0
    if data.get("expires_days", 0) > 0:
        expires_at = int((datetime.now(timezone.utc) + timedelta(days=data["expires_days"])).timestamp())

    exp_days = data.get("expires_days", 0)
    total_gb = data.get("total_gb", 0)
    expires_str = f"{exp_days} дней" if exp_days else "бессрочно"
    traffic_str = f"{total_gb} GB" if total_gb else "без лимита"

    summary = (
        f"<b>Подтверждение:</b>\n\n"
        f"Комментарий: {data.get('comment', '—')}\n"
        f"Пароль: <code>{data['password']}</code>\n"
        f"Срок: {expires_str}\n"
        f"Трафик: {traffic_str}\n"
        f"Устройства: {data.get('max_devices', 1)}\n"
        f"Max Down: {data.get('max_down_mbps', 0) or 'без лимита'} Mbps\n"
        f"Max Up: {data.get('max_up_mbps', 0) or 'без лимита'} Mbps\n"
        f"VK Hash: {data.get('vk_hash', '—') or '—'}\n\n"
        f"Отправьте /confirm для создания или /cancel для отмены:"
    )
    await state.update_data(expires_at=expires_at)
    await state.set_state(AddUserFSM.confirm)
    await message.answer(summary)


@router.message(AddUserFSM.confirm)
async def adduser_confirm(message: Message, state: FSMContext, config: Config):
    if message.text.strip() != "/confirm":
        await state.clear()
        await message.answer("❌ Отменено.")
        return

    data = await state.get_data()
    await state.clear()

    payload = {}
    if data.get("comment"):
        payload["comment"] = data["comment"]
    if data.get("password"):
        payload["password"] = data["password"]
    if data.get("expires_at"):
        payload["expires_at"] = data["expires_at"]
    if data.get("total_gb"):
        payload["total_gb"] = data["total_gb"]
    if data.get("max_devices"):
        payload["max_devices"] = data["max_devices"]
    if data.get("max_down_mbps"):
        payload["max_down_mbps"] = data["max_down_mbps"]
    if data.get("max_up_mbps"):
        payload["max_up_mbps"] = data["max_up_mbps"]
    if data.get("vk_hash"):
        payload["vk_hash"] = data["vk_hash"]

    client, srv = await get_client(message.from_user.id, config)

    if config.is_cluster(srv):
        members = config.get_group_members(srv)
        results = await cluster_write(members, "add_user", **payload)
        text = _format_cluster_result(results)
        ok = any(r.ok for r in results)
        if ok:
            pwd = data.get("password", "")
            text = f"✅ Пользователь создан\nПароль: <code>{pwd}</code>\n\n{text}"
        await message.answer(text)
    else:
        result = await _api_call(message, client.add_user, **payload)
        if result is None:
            return
        pwd = result.get("password", data.get("password", ""))
        await message.answer(f"✅ Пользователь создан\nПароль: <code>{pwd}</code>")


# ─── Edit user ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:edit:"))
async def cb_user_edit(cq: CallbackQuery):
    password = cq.data[len("user:edit:"):]
    text = f"<b>✏️ Редактирование — {password[:12]}…</b>\n\nВыберите поле:"
    await _safe_edit(cq, text, user_edit_kb(password))


@router.callback_query(F.data.startswith("user:editfield:"))
async def cb_user_editfield(cq: CallbackQuery, state: FSMContext):
    parts = cq.data.split(":")
    password = parts[2]
    field = parts[3]

    field_names = {
        "comment": "комментарий",
        "password": "пароль",
        "expires_at": "срок действия (timestamp или дата YYYY-MM-DD)",
        "total_gb": "лимит трафика (GB)",
        "max_devices": "макс. устройств",
        "max_down_mbps": "max download (Mbps)",
        "max_up_mbps": "max upload (Mbps)",
    }
    field_name = field_names.get(field, field)

    await state.update_data(edit_password=password, edit_field=field)
    await cq.answer()
    await cq.message.answer(f"Введите новое значение для <b>{field_name}</b>:")
    await state.set_state(EditFieldFSM.waiting_value)


@router.message(EditFieldFSM.waiting_value)
async def process_edit_field(message: Message, state: FSMContext, config: Config):
    data = await state.get_data()
    await state.clear()

    password = data.get("edit_password", "")
    field = data.get("edit_field", "")
    value = message.text.strip()

    payload = {"old_password": password}

    # Конвертация значений
    if field in ("total_gb", "max_devices", "max_down_mbps", "max_up_mbps"):
        try:
            payload[field] = int(value)
        except ValueError:
            await message.answer("Введите число:")
            return
    elif field == "expires_at":
        try:
            # Пробуем как timestamp
            payload[field] = int(value)
        except ValueError:
            # Пробуем как дату
            try:
                dt = datetime.strptime(value, "%Y-%m-%d")
                payload[field] = int(dt.replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                await message.answer("Введите timestamp или дату YYYY-MM-DD:")
                return
    elif field == "password":
        payload["password"] = value
    else:
        payload[field] = value

    client, srv = await get_client(message.from_user.id, config)

    if config.is_cluster(srv):
        members = config.get_group_members(srv)
        results = await cluster_write(members, "update_user", **payload)
        text = _format_cluster_result(results)
        await message.answer(text)
    else:
        result = await _api_call(message, client.update_user, **payload)
        if result is None:
            return
        await message.answer("✅ Пользователь обновлён")


# ─── Inbound ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:inbound")
async def cb_inbound(cq: CallbackQuery, config: Config):
    client, srv = await get_client(cq.from_user.id, config)
    data = await _api_call(cq, client.get_inbound)
    if data is None:
        return

    text = format_inbound(data)
    await _safe_edit(cq, text, inbound_kb())


@router.callback_query(F.data == "inbound:edit")
async def cb_inbound_edit(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await cq.message.answer(
        "🔧 <b>Редактирование Inbound</b>\n\n"
        "DTLS порт (текущий будет показан):"
    )
    await state.set_state(InboundEditFSM.dtls_port)


@router.message(InboundEditFSM.dtls_port)
async def inbound_dtls(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    try:
        port = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число:")
        return
    await state.update_data(dtls_port=port)
    await message.answer("WireGuard порт:")
    await state.set_state(InboundEditFSM.wg_port)


@router.message(InboundEditFSM.wg_port)
async def inbound_wg(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    try:
        port = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число:")
        return
    await state.update_data(wg_port=port)
    await message.answer("Клиентский порт:")
    await state.set_state(InboundEditFSM.client_port)


@router.message(InboundEditFSM.client_port)
async def inbound_client(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    try:
        port = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число:")
        return
    await state.update_data(client_port=port)
    await message.answer("DNS сервер:")
    await state.set_state(InboundEditFSM.dns)


@router.message(InboundEditFSM.dns)
async def inbound_dns(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    await state.update_data(dns=message.text.strip())
    await message.answer("Макс. пользователей:")
    await state.set_state(InboundEditFSM.max_users)


@router.message(InboundEditFSM.max_users)
async def inbound_max_users(message: Message, state: FSMContext, config: Config):
    if _is_command(message):
        await state.clear()
        return
    try:
        max_users = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число:")
        return
    await state.update_data(max_users=max_users)

    data = await state.get_data()
    await state.clear()

    client, srv = await get_client(message.from_user.id, config)

    # Получаем текущий inbound для сохранения остальных полей
    inbound = await _api_call(message, client.get_inbound)
    if inbound is None:
        return

    payload = {
        "tag": inbound.get("tag", "wdtt-in"),
        "remark": inbound.get("remark", "WDTT"),
        "listen_host": inbound.get("listen_host", "0.0.0.0"),
        "server_host": inbound.get("server_host", ""),
        "dtls_port": data.get("dtls_port", inbound.get("dtls_port", 56000)),
        "wg_port": data.get("wg_port", inbound.get("wg_port", 56001)),
        "client_port": data.get("client_port", inbound.get("client_port", 9000)),
        "dns": data.get("dns", inbound.get("dns", "1.1.1.1")),
        "max_users": data.get("max_users", inbound.get("max_users", 10)),
    }

    result = await _api_call(message, client.save_inbound, payload)
    if result is None:
        return

    await message.answer("✅ Inbound сохранён и WDTT перезапущен")


# ─── Services ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:services")
async def cb_services(cq: CallbackQuery, config: Config):
    client, srv = await get_client(cq.from_user.id, config)
    status = await _api_call(cq, client.get_status)
    if status is None:
        return

    text = format_services(status)
    await _safe_edit(cq, text, services_kb(status))


@router.callback_query(F.data == "service:restart_wdtt")
async def cb_restart_wdtt(cq: CallbackQuery):
    text = "<b>⚠️ Перезапустить WDTT?</b>\n\nVPN будет перезапущен."
    await _safe_edit(cq, text, service_confirm_kb("wdtt"))


@router.callback_query(F.data == "service:restart_xray")
async def cb_restart_xray(cq: CallbackQuery):
    text = "<b>⚠️ Перезапустить Xray?</b>\n\nXray будет перезапущен."
    await _safe_edit(cq, text, service_confirm_kb("xray"))


@router.callback_query(F.data == "service:restart_wdtt_confirm")
async def cb_restart_wdtt_confirm(cq: CallbackQuery, config: Config):
    client, srv = await get_client(cq.from_user.id, config)
    result = await _api_call(cq, client.restart_wdtt)
    if result is None:
        return
    await _safe_edit(cq, "✅ WDTT перезапускается...", back_kb("menu:services"))


@router.callback_query(F.data == "service:restart_xray_confirm")
async def cb_restart_xray_confirm(cq: CallbackQuery, config: Config):
    client, srv = await get_client(cq.from_user.id, config)
    result = await _api_call(cq, client.restart_xray)
    if result is None:
        return
    await _safe_edit(cq, "✅ Xray перезапускается...", back_kb("menu:services"))


# ─── Xray ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:xray")
async def cb_xray_menu(cq: CallbackQuery, config: Config):
    client, srv = await get_client(cq.from_user.id, config)
    status = await _api_call(cq, client.get_status)
    if status is None:
        return

    lines = ["<b>📡 Xray</b>\n"]
    lines.append(f"Статус: {'✅' if status.get('xray_active') else '❌'}")
    text = "\n".join(lines)
    await _safe_edit(cq, text, xray_kb())


@router.callback_query(F.data == "xray:config")
async def cb_xray_config(cq: CallbackQuery, config: Config):
    client, srv = await get_client(cq.from_user.id, config)
    data = await _api_call(cq, client.get_xray_config)
    if data is None:
        return

    text = format_xray_config(data)
    await _safe_edit(cq, text, back_kb("menu:xray"))


@router.callback_query(F.data == "xray:versions")
async def cb_xray_versions(cq: CallbackQuery, config: Config):
    client, srv = await get_client(cq.from_user.id, config)
    data = await _api_call(cq, client.get_xray_versions)
    if data is None:
        return

    text = format_xray_versions(data)
    await _safe_edit(cq, text, back_kb("menu:xray"))


# ─── Alerts ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:alerts")
async def cb_alerts(cq: CallbackQuery, config: Config):
    uid = cq.from_user.id
    idx = await get_server_index(uid, config)
    srv = config.servers[idx]

    states = {}
    for atype in ALERT_TYPES:
        states[atype] = await get_alert(uid, srv.name, atype)

    text = format_alerts(states)
    await _safe_edit(cq, text, alerts_kb(states))


@router.callback_query(F.data.startswith("alert:toggle:"))
async def cb_alert_toggle(cq: CallbackQuery, config: Config):
    atype = cq.data[len("alert:toggle:"):]
    uid = cq.from_user.id
    idx = await get_server_index(uid, config)
    srv = config.servers[idx]

    current = await get_alert(uid, srv.name, atype)
    await set_alert(uid, srv.name, atype, not current)
    await cq.answer("✅ Переключено")

    # Обновляем меню
    states = {}
    for t in ALERT_TYPES:
        states[t] = await get_alert(uid, srv.name, t)
    text = format_alerts(states)
    await _safe_edit(cq, text, alerts_kb(states))


@router.callback_query(F.data == "alert:log")
async def cb_alert_log(cq: CallbackQuery, config: Config):
    idx = await get_server_index(cq.from_user.id, config)
    srv = config.servers[idx]

    alerts = await db.get_recent_alerts(srv.name, limit=20)
    text = format_alert_log(alerts)
    await _safe_edit(cq, text, back_kb("menu:alerts"))


# ─── Export ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:export")
async def cb_export_menu(cq: CallbackQuery):
    text = "<b>📤 Экспорт пользователей</b>\n\nВыберите формат:"
    await _safe_edit(cq, text, export_menu_kb())


@router.callback_query(F.data == "users:export:csv")
async def cb_export_csv(cq: CallbackQuery, config: Config):
    idx = await get_server_index(cq.from_user.id, config)
    srv = config.servers[idx]
    name = srv.group or srv.name

    users, _inbound = await _api_call(cq, _fetch_users, cq.from_user.id, config)
    if users is None:
        return

    csv_bytes = users_to_csv(users, name)
    doc = BufferedInputFile(csv_bytes, filename=f"wdtt_users_{name}.csv")
    await cq.message.answer_document(doc, caption=f"📄 CSV — {len(users)} пользователей")
    await cq.answer()


@router.callback_query(F.data == "users:export:xlsx")
async def cb_export_xlsx(cq: CallbackQuery, config: Config):
    idx = await get_server_index(cq.from_user.id, config)
    srv = config.servers[idx]
    name = srv.group or srv.name

    users, _inbound = await _api_call(cq, _fetch_users, cq.from_user.id, config)
    if users is None:
        return

    xlsx_bytes = users_to_xlsx(users, name)
    doc = BufferedInputFile(xlsx_bytes, filename=f"wdtt_users_{name}.xlsx")
    await cq.message.answer_document(doc, caption=f"📊 Excel — {len(users)} пользователей")
    await cq.answer()


# ─── Traffic report ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:traffic_report")
async def cb_traffic_report_menu(cq: CallbackQuery):
    text = "<b>📊 Отчёт по трафику</b>\n\nВыберите период:"
    await _safe_edit(cq, text, traffic_report_kb())


@router.callback_query(F.data.startswith("traffic_report:"))
async def cb_traffic_report(cq: CallbackQuery, config: Config):
    days = int(cq.data.split(":")[1])
    client, srv = await get_client(cq.from_user.id, config)

    deltas = await db.get_all_users_traffic_delta(srv.name, days)
    if not deltas:
        await cq.answer("Нет данных за этот период", show_alert=True)
        return

    text = f"<b>📊 Отчёт по трафику — {days}д</b>\n\n"
    for d in deltas[:10]:
        text += f"• <code>{d['user_password'][:12]}</code>: {d['delta_bytes']} байт\n"

    await _safe_edit(cq, text, traffic_report_kb())


@router.callback_query(F.data.startswith("traffic_report_chart:"))
async def cb_traffic_report_chart(cq: CallbackQuery, config: Config):
    days = int(cq.data.split(":")[1])
    client, srv = await get_client(cq.from_user.id, config)

    deltas = await db.get_all_users_traffic_delta(srv.name, days)
    buf = charts.render_traffic_report(deltas, days, srv.name)
    if buf is None:
        await cq.answer("Недостаточно данных или matplotlib не установлен", show_alert=True)
        return

    photo = BufferedInputFile(buf.read(), filename="traffic_report.png")
    await cq.message.answer_photo(photo, caption=f"📊 Топ трафика ({days}д)")
    await cq.answer()


# ─── Search (callback) ───────────────────────────────────────────────────────

@router.callback_query(F.data == "users:search")
async def cb_users_search(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await cq.message.answer("🔍 Введите пароль или комментарий для поиска:")
    await state.set_state(SearchUserFSM.waiting_query)


# ─── Noop ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "noop")
async def cb_noop(cq: CallbackQuery):
    await cq.answer()
