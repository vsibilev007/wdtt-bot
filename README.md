# WDTT Manager Bot

Telegram-бот для управления [WDTT VPN Panel](https://github.com/ildarmaga/wdtt).

## Возможности

- **Dashboard** — статус WDTT/Xray, IP, количество пользователей
- **Пользователи** — список, создание, редактирование, удаление, вкл/выкл, сброс трафика
- **Inbound** — просмотр и редактирование (порты, DNS, max_users)
- **Сервисы** — перезапуск WDTT и Xray
- **Xray** — просмотр конфига, список версий
- **Алерты** — уведомления при недоступности сервисов и приближении лимита пользователей
- **Трафик** — история, графики, отчёты по трафику
- **QR-коды** для `wdtt://` ссылок
- **Экспорт** — CSV и Excel
- **Мульти-сервер** — кластерная поддержка (write на все узлы, read с первого доступного)

## Быстрый старт

### 1. Установка

```bash
git clone git@github.com:vsibilev007/wdtt-bot.git
cd wdtt-bot
pip install -r requirements.txt
```

### 2. Конфигурация

```bash
cp .env.example .env
```

Отредактируйте `.env`:

| Переменная | Описание | Обязательна |
|------------|----------|-------------|
| `BOT_TOKEN` | Токен Telegram бота | Да |
| `ALLOWED_USERS` | Telegram ID через запятую | Да |
| `SERVER_URL` | URL панели WDTT | Да |
| `SERVER_USERNAME` | Логин панели | Нет (admin) |
| `SERVER_PASSWORD` | Пароль панели | Нет (wdtt) |
| `SERVER_NAME` | Имя сервера | Нет (WDTT) |
| `TZ` | Временная зона | Нет |
| `LOG_LEVEL` | Уровень логов | Нет (INFO) |
| `TELEGRAM_PROXY_URL` | Прокси для Telegram API | Нет |

### 3. Запуск

```bash
python bot.py
```

### Docker

```bash
docker compose up -d
```

## Мульти-сервер

Для кластера из нескольких серверов:

```env
SERVER_1_URL=https://10.0.0.1:2860/wdtt
SERVER_1_NAME=Primary
SERVER_1_USERNAME=admin
SERVER_1_PASSWORD=wdtt
SERVER_1_GROUP=HA

SERVER_2_URL=https://10.0.0.2:2860/wdtt
SERVER_2_NAME=Secondary
SERVER_2_USERNAME=admin
SERVER_2_PASSWORD=wdtt
SERVER_2_GROUP=HA
```

Серверы с одинаковым `GROUP` работают как кластер:
- Write-операции (создание/удаление пользователей) идут параллельно на все узлы
- Read-операции идут на первый доступный узел

## Алерты

| Тип | Описание | Cooldown |
|-----|----------|----------|
| `wdtt_down` | WDTT сервис недоступен | 5 мин |
| `xray_down` | Xray сервис недоступен | 5 мин |
| `users_limit` | Приближение лимита пользователей | 5 мин |

Настройка через меню бота: кнопка переключения для каждого типа алерта.

## Архитектура

```
bot.py              → Entry point (Dispatcher, Router, polling)
config.py           → Dataclass-ы + загрузка из .env
api_client.py       → WdtClient (cookie auth, auto-relogin)
database.py         → aiosqlite (трафик, алерты, сессии)
handlers.py         → Все обработчики команд и callback
keyboards.py        → InlineKeyboardBuilder функции
formatters.py       → HTML-форматтеры ответов API
scheduler.py        → APScheduler (сбор трафика, health check, cleanup)
middlewares.py       → AuthMiddleware (allowlist по user_id)
states.py           → FSM состояния (aiogram)
session.py          → Выбор сервера (сохраняется в БД)
charts.py           → matplotlib графики трафика
qr_utils.py         → QR-коды для wdtt:// ссылок
export_utils.py     → CSV + Excel экспорт
```

## Зависимости

- aiogram 3.27 — Telegram бот фреймворк
- aiohttp — async HTTP клиент
- aiosqlite — async SQLite
- APScheduler — фоновые задачи
- matplotlib — графики
- openpyxl — Excel экспорт
- qrcode — QR-коды

## Правила

- **Никогда не пушить в git без явного одобрения.** Сначала тест на сервере, потом push.
- Авторизация через cookie (`POST /login` → `wdtt-panel` cookie)
- Пользователи идентифицируются по `password` (уникальный в WDTT)

## Лицензия

MIT
