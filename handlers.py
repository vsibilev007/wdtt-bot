"""
Обработчики команд и callback-кнопок Telegram-бота
"""

from __future__ import annotations

import html
import io
import json
import logging
import re
import secrets
from datetime import datetime, timezone, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from api_client import ApiError
from config import Config
import database as db
from export_utils import users_to_csv, users_to_xlsx
from formatters import (
    format_status, format_users_list, format_user_detail, format_user_link,
    format_user_devices, format_inbound, format_services, format_xray_config,
    format_xray_config_diff, format_xray_versions, format_alerts,
    format_alert_log, format_online_sessions, ALERT_TYPES, fmt_bytes,
)
from keyboards import (
    main_menu_kb, dashboard_kb, users_list_kb, user_detail_kb, user_edit_kb,
    user_delete_confirm_kb, user_traffic_kb, user_devices_kb, inbound_kb,
    services_kb, service_confirm_kb, xray_kb, xray_versions_kb,
    xray_install_confirm_kb, xray_import_confirm_kb, alerts_kb, export_menu_kb,
    traffic_report_kb, back_kb,
)
from session import get_client, get_cached_client
import charts
from states import AddUserFSM, EditFieldFSM, SearchUserFSM, InboundEditFSM, MainPasswordFSM, XrayImportFSM
from database import set_alert, get_alert

logger = logging.getLogger(__name__)

router = Router()

# Пароль VPN попадает в inline-кнопки, а callback_data ограничена 64 байтами:
# самый длинный шаблон user:editfield:{pwd}:max_down_mbps оставляет 35 байт
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,35}$")


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


async def _api_call(target, func, *args, **kwargs):
    """Обёртка для API-вызовов с обработкой ошибок."""
    try:
        return await func(*args, **kwargs)
    except ApiError as e:
        msg = f"❌ Ошибка API: {e.message}"
        # Лимит answerCallbackQuery — 200 символов, sendMessage — 4096.
        # answer может упасть, если callback уже отвечен (длительные действия)
        try:
            if isinstance(target, CallbackQuery):
                await target.answer(msg[:200], show_alert=True)
            else:
                await target.answer(msg[:4000])
        except Exception:
            logger.debug("Не удалось показать ошибку API: %s", msg[:100])
        return None
    except Exception as e:
        msg = f"❌ Ошибка: {e}"
        try:
            if isinstance(target, CallbackQuery):
                await target.answer(msg[:200], show_alert=True)
            else:
                await target.answer(msg[:4000])
        except Exception:
            logger.debug("Не удалось показать ошибку: %s", msg[:100])
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
        try:
            await cq.answer()
        except Exception:
            pass  # callback уже отвечен (например, длительным действием)


async def _fetch_users(config: Config) -> tuple[list[dict], dict]:
    """Пользователи + inbound с панели."""
    data = await get_cached_client(config.servers[0]).get_users()
    return data.get("users", []), data.get("inbound", {})


# ─── /start ──────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, config: Config):
    await state.clear()
    srv = config.servers[0]
    text = (
        f"<b>WDTT Manager Bot</b>\n\n"
        f"Сервер: <b>{srv.name}</b>\n"
        f"URL: <code>{srv.url}</code>\n\n"
        f"Выберите действие:"
    )
    await message.answer(text, reply_markup=main_menu_kb())


# ─── /menu ───────────────────────────────────────────────────────────────────

@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext, config: Config):
    await state.clear()
    srv = config.servers[0]

    client = get_cached_client(srv)
    status = await _api_call(message, client.get_status)
    if status is None:
        await message.answer("Меню:", reply_markup=main_menu_kb())
        return

    text = format_status(status, srv.name)
    await message.answer(text, reply_markup=main_menu_kb())


# ─── /id ─────────────────────────────────────────────────────────────────────

@router.message(Command("id"))
async def cmd_id(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
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
        "👥 <b>Пользователи</b> — список, создание, редактирование, удаление, устройства\n"
        "🔧 <b>Inbound</b> — настройки подключения (порты, DNS)\n"
        "🔄 <b>Сервисы</b> — перезапуск WDTT и Xray, смена главного пароля VPN\n"
        "📡 <b>Xray</b> — конфиг (просмотр/импорт), версии и обновление\n"
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

    users, _inbound = await _api_call(message, _fetch_users, config)
    if users is None:
        return

    found = [
        u for u in users
        if query in (u.get("password_key", "") or u.get("password", "")).lower()
        or query in u.get("comment", "").lower()
    ]

    if not found:
        await message.answer(f"Ничего не найдено по запросу: <b>{html.escape(query)}</b>")
        return

    text = format_users_list(found, page=0)
    await message.answer(text, reply_markup=users_list_kb(found, page=0))


# ─── Menu callbacks ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:main")
async def cb_menu_main(cq: CallbackQuery, config: Config):
    srv = config.servers[0]

    client = get_cached_client(srv)
    status = await _api_call(cq, client.get_status)
    if status is None:
        return

    text = format_status(status, srv.name)
    await _safe_edit(cq, text, main_menu_kb())


@router.callback_query(F.data == "menu:dashboard")
async def cb_dashboard(cq: CallbackQuery, config: Config):
    srv = config.servers[0]

    client = get_cached_client(srv)
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
    client, _srv = get_client(config)
    status = await _api_call(cq, client.get_status)
    if status is None:
        return

    text = format_online_sessions(status)
    kb = InlineKeyboardBuilder()
    kb.button(text="🔄 Обновить", callback_data="menu:online")
    kb.button(text="◀️ Меню", callback_data="menu:main")
    kb.adjust(2)
    await _safe_edit(cq, text, kb.as_markup())


# ─── Users list ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:users")
async def cb_users_list(cq: CallbackQuery, config: Config):
    users, _inbound = await _api_call(cq, _fetch_users, config)
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

    users, _inbound = await _api_call(cq, _fetch_users, config)
    if users is None:
        return

    text = format_users_list(users, page=page)
    await _safe_edit(cq, text, users_list_kb(users, page=page))


# ─── User detail ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:view:"))
async def cb_user_view(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:view:"):]

    users, _inbound = await _api_call(cq, _fetch_users, config)
    if users is None:
        return

    user = next((u for u in users if (u.get("password_key", "") or u.get("password", "")) == password), None)
    if not user:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    text = format_user_detail(user)
    await _safe_edit(cq, text, user_detail_kb(password, user.get("active", True)))


@router.callback_query(F.data == "user:too_long")
async def cb_user_too_long(cq: CallbackQuery):
    await cq.answer(
        "Пароль длиннее 35 символов не помещается в inline-кнопку Telegram "
        "(лимит callback_data — 64 байта). Управляйте таким пользователем "
        "через веб-панель.",
        show_alert=True,
    )

# ─── User toggle ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:toggle:"))
async def cb_user_toggle(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:toggle:"):]

    # Получаем текущее состояние
    users, _inbound = await _api_call(cq, _fetch_users, config)
    if users is None:
        return

    user = next((u for u in users if (u.get("password_key", "") or u.get("password", "")) == password), None)
    if not user:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    new_active = not user.get("active", True)

    client, _srv = get_client(config)
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
    text = f"<b>⚠️ Удалить пользователя?</b>\n\nПароль: <code>{html.escape(password)}</code>"
    await _safe_edit(cq, text, user_delete_confirm_kb(password))


@router.callback_query(F.data.startswith("user:delete_confirm:"))
async def cb_user_delete_confirm(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:delete_confirm:"):]

    client, _srv = get_client(config)
    result = await _api_call(cq, client.delete_user, password)
    if result is None:
        return
    await _safe_edit(cq, "✅ Пользователь удалён", back_kb("menu:users"))


# ─── User link ───────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:link:"))
async def cb_user_link(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:link:"):]

    users, inbound = await _api_call(cq, _fetch_users, config)
    if users is None:
        return

    user = next((u for u in users if (u.get("password_key", "") or u.get("password", "")) == password), None)
    if not user:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    text = format_user_link(user, inbound, csqtt_port=config.csqtt_port)
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Назад", callback_data=f"user:view:{password}")
    await _safe_edit(cq, text, kb.as_markup())


# ─── User traffic ─────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:traffic:"))
async def cb_user_traffic(cq: CallbackQuery):
    password = cq.data[len("user:traffic:"):]
    text = f"<b>📊 Трафик — {html.escape(password[:12])}…</b>\n\nВыберите период:"
    await _safe_edit(cq, text, user_traffic_kb(password))


@router.callback_query(F.data.startswith("user:traffic_period:"))
async def cb_user_traffic_period(cq: CallbackQuery, config: Config):
    parts = cq.data.split(":")
    password = parts[2]
    days = int(parts[3])

    _client, srv = get_client(config)

    # Получаем историю из БД
    rows = await db.get_traffic_history(srv.name, password, days)
    if len(rows) < 2:
        await cq.answer("Истории трафика пока нет — точки собираются каждые 15 минут", show_alert=True)
        return

    delta = await db.get_traffic_delta(srv.name, password, days)
    text = (
        f"<b>📊 Трафик — {html.escape(password[:12])}…</b>\n"
        f"Период: {days}д\n"
        f"Дельта: {fmt_bytes(delta['delta_bytes'])} ({delta['points']} точек)"
    )
    await _safe_edit(cq, text, user_traffic_kb(password))


@router.callback_query(F.data.startswith("user:traffic_chart:"))
async def cb_user_traffic_chart(cq: CallbackQuery, config: Config):
    parts = cq.data.split(":")
    password = parts[2]
    days = int(parts[3])

    _client, srv = get_client(config)

    rows = await db.get_traffic_history(srv.name, password, days)
    if len(rows) < 2:
        await cq.answer("Истории трафика пока нет — точки собираются каждые 15 минут", show_alert=True)
        return

    buf = charts.render_user_traffic(rows, password, days, srv.name)
    if buf is None:
        if not charts.HAS_MPL:
            await cq.answer("matplotlib не установлен — графики недоступны", show_alert=True)
        else:
            await cq.answer("Не удалось построить график — подробности в логах", show_alert=True)
        return

    photo = BufferedInputFile(buf.read(), filename="traffic.png")
    await cq.message.answer_photo(photo, caption=f"📊 Трафик {html.escape(password[:12])}… ({days}д)")
    await cq.answer()


# ─── User reset traffic ──────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:reset_traffic:"))
async def cb_user_reset_traffic(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:reset_traffic:"):]

    client, _srv = get_client(config)
    if await _api_call(cq, client.reset_traffic, password) is None:
        return

    await cq.answer("✅ Трафик сброшен")
    await cb_user_view(cq, config)


# ─── User devices ────────────────────────────────────────────────────────────

async def _find_user(config: Config, password: str) -> tuple[dict | None, list]:
    """Пользователь по паролю + полный список (None — ошибка API)."""
    users, _inbound = await _fetch_users(config)
    user = next(
        (u for u in users if (u.get("password_key", "") or u.get("password", "")) == password),
        None,
    )
    return user, users


@router.callback_query(F.data.startswith("user:devices:"))
async def cb_user_devices(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:devices:"):]

    result = await _api_call(cq, _find_user, config, password)
    if result is None:
        return
    user, _users = result
    if not user:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    devices = user.get("device_ids", []) or []
    text = format_user_devices(user)
    await _safe_edit(cq, text, user_devices_kb(password, len(devices)))


@router.callback_query(F.data.startswith("user:unbind_all:"))
async def cb_user_unbind_all(cq: CallbackQuery, config: Config):
    password = cq.data[len("user:unbind_all:"):]

    result = await _api_call(cq, _find_user, config, password)
    if result is None:
        return
    user, _users = result
    if not user:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    client, _srv = get_client(config)
    # Пустой device_ids отвязывает все устройства (см. docs/API.md users/update)
    if await _api_call(cq, client.update_user, old_password=password, device_ids=[]) is None:
        return

    await cq.answer("✅ Все устройства отвязаны")
    await cb_user_devices(cq, config)


@router.callback_query(F.data.startswith("user:unbind:"))
async def cb_user_unbind(cq: CallbackQuery, config: Config):
    # user:unbind:{password}:{idx} — UUID не влезает в callback_data
    payload = cq.data[len("user:unbind:"):]
    password, _, idx_raw = payload.rpartition(":")
    try:
        idx = int(idx_raw)
    except ValueError:
        await cq.answer("Ошибка", show_alert=True)
        return

    result = await _api_call(cq, _find_user, config, password)
    if result is None:
        return
    user, _users = result
    if not user:
        await cq.answer("Пользователь не найден", show_alert=True)
        return

    devices = list(user.get("device_ids", []) or [])
    if idx < 0 or idx >= len(devices):
        await cq.answer("Список устройств изменился — откройте экран заново", show_alert=True)
        return

    removed = devices.pop(idx)

    client, _srv = get_client(config)
    # Удаление из device_ids отвязывает устройство при сохранении
    if await _api_call(cq, client.update_user, old_password=password, device_ids=devices) is None:
        return

    await cq.answer(f"✅ Устройство {str(removed)[:8]}… отвязано")
    await cb_user_devices(cq, config)


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
    await message.answer(
        "Введите пароль или /gen для автогенерации.\n"
        "Пример: <code>2L3G5WtqrAWu8Peu</code> — 16 символов, латиница и цифры:"
    )
    await state.set_state(AddUserFSM.password)


@router.message(AddUserFSM.password)
async def adduser_password(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    # /skip здесь — автогенерация: пароль обязателен
    text = (message.text or "").strip()
    pwd = secrets.token_urlsafe(12) if text in ("/gen", "/skip") else text
    if not PASSWORD_RE.fullmatch(pwd):
        await message.answer(
            "Пароль: 1–35 символов, латиница, цифры и знаки <code>_</code> <code>.</code> <code>-</code>\n"
            "Введите снова или /gen для автогенерации:"
        )
        return
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
        f"Комментарий: {html.escape(data.get('comment', '—'))}\n"
        f"Пароль: <code>{html.escape(data['password'])}</code>\n"
        f"Срок: {expires_str}\n"
        f"Трафик: {traffic_str}\n"
        f"Устройства: {data.get('max_devices', 1)}\n"
        f"Max Down: {data.get('max_down_mbps', 0) or 'без лимита'} Mbps\n"
        f"Max Up: {data.get('max_up_mbps', 0) or 'без лимита'} Mbps\n"
        f"VK Hash: {html.escape(data.get('vk_hash', '—') or '—')}\n\n"
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

    client, _srv = get_client(config)

    result = await _api_call(message, client.add_user, **payload)
    if result is None:
        return
    pwd = result.get("password", data.get("password", ""))
    await message.answer(f"✅ Пользователь создан\nПароль: <code>{html.escape(pwd)}</code>")


# ─── Edit user ────────────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("user:edit:"))
async def cb_user_edit(cq: CallbackQuery):
    password = cq.data[len("user:edit:"):]
    text = f"<b>✏️ Редактирование — {html.escape(password[:12])}…</b>\n\nВыберите поле:"
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
    if field == "password":
        await cq.message.answer(
            "Введите новый пароль или /gen для автогенерации.\n"
            "Пример: <code>2L3G5WtqrAWu8Peu</code> — 16 символов, латиница и цифры:"
        )
    else:
        await cq.message.answer(f"Введите новое значение для <b>{field_name}</b>:")
    await state.set_state(EditFieldFSM.waiting_value)


@router.message(EditFieldFSM.waiting_value)
async def process_edit_field(message: Message, state: FSMContext, config: Config):
    data = await state.get_data()

    password = data.get("edit_password", "")
    field = data.get("edit_field", "")
    value = (message.text or "").strip()

    # /gen — автогенерация пароля, как в мастере создания
    if field == "password" and value == "/gen":
        value = secrets.token_urlsafe(12)

    payload = {"old_password": password}

    # Конвертация значений — при ошибке состояние сохраняем, можно повторить
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
        if not PASSWORD_RE.fullmatch(value):
            await message.answer(
                "Пароль: 1–35 символов, латиница, цифры и знаки <code>_</code> <code>.</code> <code>-</code> "
                "(или /gen для автогенерации). Введите снова:"
            )
            return
        payload["password"] = value
    else:
        payload[field] = value

    client, _srv = get_client(config)

    result = await _api_call(message, client.update_user, **payload)
    if result is None:
        return
    await state.clear()
    if field == "password":
        await message.answer(
            f"✅ Пароль изменён: <code>{html.escape(value)}</code>\n"
            "Старые ссылки этого клиента больше не работают — выдайте новые "
            "(кнопка 🔗 Ссылка в карточке)."
        )
    else:
        await message.answer("✅ Пользователь обновлён")


# ─── Inbound ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:inbound")
async def cb_inbound(cq: CallbackQuery, config: Config):
    client, srv = get_client(config)
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

    client, srv = get_client(config)

    # Получаем текущий inbound для сохранения остальных полей.
    # Состояние не сбрасываем: при ошибке можно повторить или /cancel.
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

    await state.clear()
    await message.answer("✅ Inbound сохранён и WDTT перезапущен")


# ─── Services ─────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:services")
async def cb_services(cq: CallbackQuery, config: Config):
    client, srv = get_client(config)
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
    client, srv = get_client(config)
    result = await _api_call(cq, client.restart_wdtt)
    if result is None:
        return
    await _safe_edit(cq, "✅ WDTT перезапускается...", back_kb("menu:services"))


@router.callback_query(F.data == "service:restart_xray_confirm")
async def cb_restart_xray_confirm(cq: CallbackQuery, config: Config):
    client, srv = get_client(config)
    result = await _api_call(cq, client.restart_xray)
    if result is None:
        return
    await _safe_edit(cq, "✅ Xray перезапускается...", back_kb("menu:services"))


# ─── Xray ─────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:xray")
async def cb_xray_menu(cq: CallbackQuery, config: Config):
    client, srv = get_client(config)
    status = await _api_call(cq, client.get_status)
    if status is None:
        return

    lines = ["<b>📡 Xray</b>\n"]
    lines.append(f"Статус: {'✅' if status.get('xray_active') else '❌'}")
    text = "\n".join(lines)
    await _safe_edit(cq, text, xray_kb())


@router.callback_query(F.data == "xray:config")
async def cb_xray_config(cq: CallbackQuery, config: Config):
    client, _srv = get_client(config)
    data = await _api_call(cq, client.get_xray_config)
    if data is None:
        return

    text = format_xray_config(data)
    await _safe_edit(cq, text, back_kb("menu:xray"))

    # Полный конфиг — файлом: в сообщение он целиком не помещается
    raw = json.dumps(data, indent=2, ensure_ascii=False).encode()
    doc = BufferedInputFile(raw, filename="xray_config.json")
    await cq.message.answer_document(doc, caption="📡 Полный конфиг Xray")


@router.callback_query(F.data == "xray:versions")
async def cb_xray_versions(cq: CallbackQuery, config: Config):
    client, _srv = get_client(config)
    data = await _api_call(cq, client.get_xray_versions)
    if data is None:
        return

    text, current, tags = format_xray_versions(data)
    await _safe_edit(cq, text, xray_versions_kb(tags, current))


@router.callback_query(F.data.startswith("xray:install:"))
async def cb_xray_install(cq: CallbackQuery):
    tag = cq.data[len("xray:install:"):]
    text = (
        f"<b>⚠️ Установить Xray {html.escape(tag)}?</b>\n\n"
        "Панель скачает и распакует бинарник (может занять пару минут), "
        "затем перезапустит Xray."
    )
    await _safe_edit(cq, text, xray_install_confirm_kb(tag))


@router.callback_query(F.data.startswith("xray:install_confirm:"))
async def cb_xray_install_confirm(cq: CallbackQuery, config: Config):
    tag = cq.data[len("xray:install_confirm:"):]
    # Отвечаем сразу: установка идёт синхронно и может длиться минуты
    await cq.answer("⏳ Скачиваю и устанавливаю — это может занять пару минут…")
    try:
        await cq.message.edit_text(f"⏳ Устанавливаю Xray {html.escape(tag)}…")
    except Exception:
        pass

    client, _srv = get_client(config)
    result = await _api_call(cq, client.install_xray, tag)
    if result is None:
        return

    try:
        await cq.message.edit_text(
            f"✅ Xray {html.escape(tag)} установлен, сервис перезапускается",
            reply_markup=back_kb("menu:xray"),
        )
    except Exception as e:
        logger.debug("Ошибка edit_text после установки: %s", e)


# ─── Main VPN password ────────────────────────────────────────────────────────

@router.callback_query(F.data == "password:main")
async def cb_main_password(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await cq.message.answer(
        "🔑 <b>Смена главного пароля VPN</b>\n\n"
        "Введите новый пароль (1–35 символов, латиница, цифры, <code>_ . -</code>):"
    )
    await state.set_state(MainPasswordFSM.waiting_value)


@router.message(MainPasswordFSM.waiting_value)
async def mainpwd_value(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    value = (message.text or "").strip()
    if not PASSWORD_RE.fullmatch(value):
        await message.answer(
            "Пароль: 1–35 символов, латиница, цифры и знаки <code>_</code> <code>.</code> <code>-</code>. Введите снова:"
        )
        return
    await state.update_data(new_main_password=value)
    await state.set_state(MainPasswordFSM.confirm)
    await message.answer(
        f"<b>⚠️ Сменить главный пароль VPN?</b>\n\n"
        f"Новый пароль: <code>{html.escape(value)}</code>\n\n"
        "После смены <b>все текущие ссылки клиентов перестанут работать</b> — "
        "придётся раздать новые (кнопка 🔗 Ссылка в карточке).\n\n"
        "Отправьте /confirm для смены или /cancel для отмены:"
    )


@router.message(MainPasswordFSM.confirm)
async def mainpwd_confirm(message: Message, state: FSMContext, config: Config):
    if (message.text or "").strip() != "/confirm":
        await state.clear()
        await message.answer("❌ Отменено.")
        return

    data = await state.get_data()
    value = data.get("new_main_password", "")
    await state.clear()

    if not value:
        await message.answer("❌ Отменено.")
        return

    client, _srv = get_client(config)
    if await _api_call(message, client.change_main_password, value) is None:
        return

    await message.answer(
        f"✅ Главный пароль изменён: <code>{html.escape(value)}</code>\n\n"
        "Выдайте клиентам новые ссылки: Пользователи → карточка → 🔗 Ссылка."
    )


# ─── Xray config import ───────────────────────────────────────────────────────

@router.callback_query(F.data == "xray:import")
async def cb_xray_import(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await cq.message.answer(
        "📥 <b>Импорт Xray-конфига</b>\n\n"
        "Отправьте JSON-файл конфигурации документом "
        "(текущий можно скачать: Xray → 📄 Конфиг).\n"
        "Бот проверит файл, покажет сводку изменений и попросит подтверждение.\n\n"
        "/cancel — отмена"
    )
    await state.set_state(XrayImportFSM.waiting_file)


@router.message(XrayImportFSM.waiting_file, F.document)
async def xray_import_file(message: Message, state: FSMContext, config: Config, bot: Bot):
    doc = message.document
    if doc.file_size and doc.file_size > 10 * 1024 * 1024:
        await message.answer("Файл слишком большой (лимит 10 MB). Отправьте другой или /cancel.")
        return

    buf = io.BytesIO()
    await bot.download(doc, destination=buf)
    try:
        new_cfg = json.loads(buf.getvalue().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        await message.answer(f"❌ Это не JSON: {e}. Отправьте файл ещё раз или /cancel.")
        return

    if not isinstance(new_cfg, dict) or not isinstance(new_cfg.get("inbounds"), list):
        await message.answer(
            "❌ Это не похоже на конфиг Xray: нет ключа <code>inbounds</code> "
            "со списком. Отправьте другой файл или /cancel."
        )
        return

    client, _srv = get_client(config)
    try:
        current = await client.get_xray_config()
    except Exception:
        current = None

    await state.update_data(xray_new_config=new_cfg)
    await state.set_state(XrayImportFSM.confirm)

    summary = format_xray_config_diff(current, new_cfg)
    await message.answer(
        summary + "\n\n<b>Применить этот конфиг?</b>",
        reply_markup=xray_import_confirm_kb(),
    )


@router.message(XrayImportFSM.waiting_file)
async def xray_import_not_a_file(message: Message, state: FSMContext):
    if _is_command(message):
        await state.clear()
        return
    await message.answer("Отправьте конфиг файлом (документом) или /cancel.")


@router.callback_query(F.data == "xray:import_confirm")
async def cb_xray_import_confirm(cq: CallbackQuery, state: FSMContext, config: Config):
    data = await state.get_data()
    new_cfg = data.get("xray_new_config")
    if not new_cfg:
        await cq.answer("Импорт неактуален — начните заново (Xray → 📥 Импорт)", show_alert=True)
        return
    await state.clear()

    client, _srv = get_client(config)
    if await _api_call(cq, client.save_xray_config, new_cfg) is None:
        return

    await _safe_edit(
        cq,
        "✅ Конфиг применён, Xray перезапускается",
        back_kb("menu:xray"),
    )


@router.callback_query(F.data == "xray:import_cancel")
async def cb_xray_import_cancel(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await _safe_edit(cq, "❌ Импорт отменён", xray_kb())


# ─── Alerts ───────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "menu:alerts")
async def cb_alerts(cq: CallbackQuery, config: Config):
    uid = cq.from_user.id
    srv = config.servers[0]

    states = {}
    for atype in ALERT_TYPES:
        states[atype] = await get_alert(uid, srv.name, atype)

    text = format_alerts(states)
    await _safe_edit(cq, text, alerts_kb(states))


@router.callback_query(F.data.startswith("alert:toggle:"))
async def cb_alert_toggle(cq: CallbackQuery, config: Config):
    atype = cq.data[len("alert:toggle:"):]
    uid = cq.from_user.id
    srv = config.servers[0]

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
    srv = config.servers[0]

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
    name = config.servers[0].name

    users, _inbound = await _api_call(cq, _fetch_users, config)
    if users is None:
        return

    csv_bytes = users_to_csv(users, name)
    doc = BufferedInputFile(csv_bytes, filename=f"wdtt_users_{name}.csv")
    await cq.message.answer_document(doc, caption=f"📄 CSV — {len(users)} пользователей")
    await cq.answer()


@router.callback_query(F.data == "users:export:xlsx")
async def cb_export_xlsx(cq: CallbackQuery, config: Config):
    name = config.servers[0].name

    users, _inbound = await _api_call(cq, _fetch_users, config)
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
    _client, srv = get_client(config)

    deltas = await db.get_all_users_traffic_delta(srv.name, days)
    if not deltas:
        await cq.answer("Нет данных за этот период", show_alert=True)
        return

    text = f"<b>📊 Отчёт по трафику — {days}д</b>\n\n"
    for d in deltas[:10]:
        text += f"• <code>{html.escape(d['user_password'][:12])}</code>: {fmt_bytes(d['delta_bytes'])}\n"

    await _safe_edit(cq, text, traffic_report_kb())


@router.callback_query(F.data.startswith("traffic_report_chart:"))
async def cb_traffic_report_chart(cq: CallbackQuery, config: Config):
    days = int(cq.data.split(":")[1])
    _client, srv = get_client(config)

    deltas = await db.get_all_users_traffic_delta(srv.name, days)
    if not deltas:
        await cq.answer("Истории трафика пока нет — точки собираются каждые 15 минут", show_alert=True)
        return
    buf = charts.render_traffic_report(deltas, days, srv.name)
    if buf is None:
        if not charts.HAS_MPL:
            await cq.answer("matplotlib не установлен — графики недоступны", show_alert=True)
        else:
            await cq.answer("Не удалось построить график — подробности в логах", show_alert=True)
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
