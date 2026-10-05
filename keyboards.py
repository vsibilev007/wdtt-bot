"""
Клавиатуры и inline-кнопки
"""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ─── Главное меню ─────────────────────────────────────────────────────────────

def main_menu_kb(
    servers: list,
    current: int = 0,
    config=None,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    kb.button(text="📊 Dashboard",     callback_data="menu:dashboard")
    kb.button(text="👥 Пользователи",  callback_data="menu:users")
    kb.button(text="🔧 Inbound",       callback_data="menu:inbound")
    kb.button(text="🔄 Сервисы",       callback_data="menu:services")
    kb.button(text="📡 Xray",          callback_data="menu:xray")
    kb.button(text="🚨 Алерты",        callback_data="menu:alerts")
    kb.button(text="➕ Новый клиент",   callback_data="user:add")
    kb.button(text="📤 Экспорт",       callback_data="menu:export")
    schema_base = [2, 2, 2, 2]

    # Переключатель серверов
    menu_servers = config.get_menu_servers() if config else servers
    if len(menu_servers) > 1:
        for i, srv in enumerate(menu_servers):
            is_current = False
            if config:
                cur_srv = servers[current] if current < len(servers) else None
                if cur_srv:
                    if srv.group and cur_srv.group == srv.group:
                        is_current = True
                    elif not srv.group and srv.name == cur_srv.name:
                        is_current = True
            mark = "✅ " if is_current else ""
            cluster_icon = "⚙️ " if config and config.is_cluster(srv) else ""
            display_name = srv.group if (config and config.is_cluster(srv)) else srv.name
            real_idx = servers.index(srv) if srv in servers else i
            kb.button(text=f"{mark}{cluster_icon}{display_name}", callback_data=f"server:select:{real_idx}")
        kb.adjust(*schema_base, len(menu_servers))
    else:
        kb.adjust(*schema_base)

    return kb.as_markup()


# ─── Dashboard ────────────────────────────────────────────────────────────────

def dashboard_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🟢 Онлайн", callback_data="menu:online")
    kb.button(text="🔄 Обновить", callback_data="dashboard:refresh")
    kb.button(text="◀️ Меню",     callback_data="menu:main")
    kb.adjust(2, 1)
    return kb.as_markup()


# ─── Пользователи ─────────────────────────────────────────────────────────────

def users_list_kb(
    users: list,
    page: int = 0,
    per_page: int = 10,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    total = len(users)
    start = page * per_page
    end = min(start + per_page, total)
    page_users = users[start:end]

    for u in page_users:
        comment = u.get("comment", "")
        pwd = u.get("password_key", "") or u.get("password", "")
        online = "🟢" if u.get("online") else ("⚪" if u.get("active") else "🔴")
        label = f"{online} {comment}" if comment else f"{online} {pwd[:12]}"
        kb.button(text=label[:40], callback_data=f"user:view:{pwd}")

    # Навигация
    nav = []
    if page > 0:
        nav.append(("◀️ Назад", f"users:page:{page - 1}"))
    if end < total:
        nav.append(("Вперёд ▶️", f"users:page:{page + 1}"))
    for text, cb in nav:
        kb.button(text=text, callback_data=cb)

    # Дополнительные кнопки
    kb.button(text="🔍 Поиск", callback_data="users:search")
    kb.button(text="➕ Добавить", callback_data="user:add")
    kb.button(text="◀️ Меню", callback_data="menu:main")

    # Схема: по кнопке на юзера, навигация, доп. кнопки
    layout = [1] * len(page_users)
    if nav:
        layout.append(len(nav))
    layout.extend([2, 1])
    kb.adjust(*layout)

    return kb.as_markup()


def user_detail_kb(password: str, active: bool = True) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    toggle_text = "🔴 Выключить" if active else "🟢 Включить"
    kb.button(text=toggle_text, callback_data=f"user:toggle:{password}")
    kb.button(text="✏️ Редактировать", callback_data=f"user:edit:{password}")

    kb.button(text="🔗 Ссылка", callback_data=f"user:link:{password}")

    kb.button(text="📊 Трафик", callback_data=f"user:traffic:{password}")
    kb.button(text="🔄 Сброс трафика", callback_data=f"user:reset_traffic:{password}")

    kb.button(text="🗑 Удалить", callback_data=f"user:delete:{password}")
    kb.button(text="◀️ Назад", callback_data="menu:users")

    kb.adjust(2, 1, 2, 2)
    return kb.as_markup()


def user_edit_kb(password: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    fields = [
        ("💬 Комментарий", "comment"),
        ("🔑 Пароль", "password"),
        ("⏰ Срок", "expires_at"),
        ("📦 Трафик лимит", "total_gb"),
        ("📱 Устройства", "max_devices"),
        ("⬇️ Max Down", "max_down_mbps"),
        ("⬆️ Max Up", "max_up_mbps"),
    ]
    for label, field in fields:
        kb.button(text=label, callback_data=f"user:editfield:{password}:{field}")

    kb.button(text="◀️ Назад", callback_data=f"user:view:{password}")
    kb.adjust(2, 2, 2, 1, 1)
    return kb.as_markup()


def user_delete_confirm_kb(password: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, удалить", callback_data=f"user:delete_confirm:{password}")
    kb.button(text="❌ Отмена", callback_data=f"user:view:{password}")
    kb.adjust(2)
    return kb.as_markup()


def user_traffic_kb(password: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for days in [1, 3, 7, 30]:
        kb.button(text=f"{days}д", callback_data=f"user:traffic_period:{password}:{days}")
    kb.button(text="📈 График 7д", callback_data=f"user:traffic_chart:{password}:7")
    kb.button(text="📈 График 30д", callback_data=f"user:traffic_chart:{password}:30")
    kb.button(text="◀️ Назад", callback_data=f"user:view:{password}")
    kb.adjust(4, 2, 1)
    return kb.as_markup()


# ─── Inbound ──────────────────────────────────────────────────────────────────

def inbound_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Редактировать", callback_data="inbound:edit")
    kb.button(text="◀️ Меню", callback_data="menu:main")
    kb.adjust(2)
    return kb.as_markup()


# ─── Services ─────────────────────────────────────────────────────────────────

def services_kb(status: dict = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()

    wdtt_ok = status.get("wdtt_active", False) if status else False
    xray_ok = status.get("xray_active", False) if status else False

    wdtt_icon = "🟢" if wdtt_ok else "🔴"
    xray_icon = "🟢" if xray_ok else "🔴"

    kb.button(text=f"{wdtt_icon} Перезапустить WDTT", callback_data="service:restart_wdtt")
    kb.button(text=f"{xray_icon} Перезапустить Xray", callback_data="service:restart_xray")
    kb.button(text="🔄 Обновить", callback_data="menu:services")
    kb.button(text="◀️ Меню", callback_data="menu:main")
    kb.adjust(2, 2)
    return kb.as_markup()


def service_confirm_kb(service: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да, перезапустить", callback_data=f"service:restart_{service}_confirm")
    kb.button(text="❌ Отмена", callback_data="menu:services")
    kb.adjust(2)
    return kb.as_markup()


# ─── Xray ─────────────────────────────────────────────────────────────────────

def xray_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Конфиг", callback_data="xray:config")
    kb.button(text="📦 Версии", callback_data="xray:versions")
    kb.button(text="🔄 Обновить", callback_data="menu:xray")
    kb.button(text="◀️ Меню", callback_data="menu:main")
    kb.adjust(2, 2)
    return kb.as_markup()


# ─── Alerts ───────────────────────────────────────────────────────────────────

def alerts_kb(alert_states: dict[str, bool]) -> InlineKeyboardMarkup:
    from formatters import ALERT_TYPES

    kb = InlineKeyboardBuilder()
    for atype, desc in ALERT_TYPES.items():
        state = alert_states.get(atype, False)
        icon = "🔔" if state else "🔕"
        kb.button(text=f"{icon} {desc}", callback_data=f"alert:toggle:{atype}")
    kb.button(text="📋 История алертов", callback_data="alert:log")
    kb.button(text="◀️ Меню", callback_data="menu:main")
    kb.adjust(1, 1, 1, 2)
    return kb.as_markup()


# ─── Export ───────────────────────────────────────────────────────────────────

def export_menu_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 CSV", callback_data="users:export:csv")
    kb.button(text="📊 Excel", callback_data="users:export:xlsx")
    kb.button(text="◀️ Меню", callback_data="menu:main")
    kb.adjust(2, 1)
    return kb.as_markup()


# ─── Traffic report ───────────────────────────────────────────────────────────

def traffic_report_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for days in [1, 3, 7, 30]:
        kb.button(text=f"{days}д", callback_data=f"traffic_report:{days}")
    kb.button(text="📈 График 7д", callback_data=f"traffic_report_chart:7")
    kb.button(text="📈 График 30д", callback_data=f"traffic_report_chart:30")
    kb.button(text="◀️ Меню", callback_data="menu:main")
    kb.adjust(4, 2, 1)
    return kb.as_markup()


# ─── Generic ──────────────────────────────────────────────────────────────────

def back_kb(callback_data: str = "menu:main") -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="◀️ Меню", callback_data=callback_data)
    return kb.as_markup()
