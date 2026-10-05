# WDTT Manager Bot

Telegram-бот для управления [WDTT VPN Panel](https://github.com/ildarmaga/wdtt) через Panel API.

**Возможности:** управление пользователями и клиентами, онлайн-сессии, трафик с графиками, алерты, сервисы, Xray, экспорт.

---

## Возможности

- **Управление пользователями** — создание (пошаговый мастер), редактирование, вкл/выкл, удаление, сброс трафика
- **Dashboard** — статус WDTT/Xray, IP, интерфейс, статистика
- **Онлайн-сессии** — активные подключения: кто, с какого IP, режим, устройств
- **Трафик** — автоматический сбор истории (каждые 15 минут), графики по пользователю и отчёты по всем
- **Inbound** — просмотр и редактирование настроек подключения (порты, DNS, max_users)
- **Сервисы** — перезапуск WDTT и Xray с подтверждением
- **Xray** — просмотр конфига, список доступных версий
- **Алерты** — уведомления о недоступности сервисов и приближении лимита пользователей
- **Ссылки подключения** — `csqtt://` (WRAP CSQTT-WRAP-v1 + VKQUIC) и `wdtt://` (colon-формат) для всех клиентов
- **Экспорт** — выгрузка пользователей в CSV и Excel
- **Безопасность** — allowlist Telegram ID, non-root контейнер, read-only ФС, без capabilities

---

## Требования

| Компонент | Версия | Примечание |
|-----------|--------|------------|
| Python | 3.11+ | только для установки без Docker |
| WDTT Panel | с HTTP API | [github.com/ildarmaga/wdtt](https://github.com/ildarmaga/wdtt) |
| Docker | 20+ | опционально, для контейнерного запуска |

Все Python-зависимости ставятся одной командой из `requirements.txt` (см. [Зависимости](#зависимости)). Для графиков нужен matplotlib — он в списке; без него бот работает, но кнопки графиков сообщат, что отрисовка недоступна.

---

## Быстрый старт (Docker)

### 1. Создать `.env`

```bash
git clone https://github.com/vsibilev007/wdtt-bot.git
cd wdtt-bot
cp .env.example .env
nano .env          # заполни BOT_TOKEN, ALLOWED_USERS, SERVER_URL и доступ к панели
```

Минимальный `.env`:

```env
BOT_TOKEN=1234567890:AABBCCDDEEFFaabbccddeeff
ALLOWED_USERS=123456789
SERVER_URL=https://your-server:2860/wdtt
SERVER_NAME=My WDTT
SERVER_USERNAME=admin
SERVER_PASSWORD=wdtt
```

### 2. Запустить

```bash
docker compose up -d
docker compose logs -f
```

Открой бота в Telegram, отправь `/start` — готово.

### Образ из GHCR

Образ автоматически собирается и публикуется в GitHub Container Registry при пуше в `main` или теге `v*`:

```bash
# Последняя версия с main
docker pull ghcr.io/vsibilev007/wdtt-bot:main

# Конкретная версия (по тегу v1.2.3)
docker pull ghcr.io/vsibilev007/wdtt-bot:1.2.3
```

Для GHCR-образа без локальной сборки используй в `docker-compose.yml`:

```yaml
services:
  wdtt-bot:
    image: ghcr.io/vsibilev007/wdtt-bot:main
    # строку build: . убрать
```

### Данные и том

База данных SQLite хранится в named volume `wdtt-data` (внутри контейнера — `/app/data/wdtt_bot.db`, путь прописан в `docker-compose.yml`). Файловая система контейнера read-only, поэтому БД обязана лежать на томе.

```bash
# Посмотреть том
docker volume inspect wdtt-data

# Бэкап
docker run --rm -v wdtt-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/wdtt-data.tar.gz -C /data .
```

> **Не используй bind-mount** (`./data:/app/data`) — права `appuser` (UID 10001) на хостовой директории не совпадут, и SQLite упадёт с `unable to open database file`.

### Параметры безопасности (уже включены в compose)

| Параметр | Значение |
|----------|----------|
| `read_only` | файловая система контейнера только для чтения |
| `cap_drop` | `ALL` — сброс всех Linux capabilities |
| `no-new-privileges` | запрет эскалации привилегий |
| `mem_limit` | 256 MB |
| `tmpfs` | `/tmp` (10 MB) — для heartbeat-файла healthcheck |
| Пользователь | non-root `appuser` (UID 10001) |

Healthcheck контейнера раз в 30с проверяет файл `/tmp/healthy` — его обновляет внутренний планировщик бота каждые 20 секунд (путь меняется переменной `WDTT_BOT_HEARTBEAT_PATH`).

---

## Установка без Docker

### 1. Python и зависимости

**Ubuntu / Debian:**

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
```

**CentOS / RHEL / AlmaLinux:**

```bash
sudo dnf install -y python3 python3-pip git
```

### 2. Клонировать и установить

```bash
git clone https://github.com/vsibilev007/wdtt-bot.git
cd wdtt-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> В системном Python (без venv) `pip install` на свежих Debian/Ubuntu/Fedora упадёт с ошибкой PEP 668 («externally managed environment») — используй venv.

### 3. Настроить конфигурацию

```bash
cp .env.example .env
nano .env
```

Обязательные параметры:

| Переменная | Откуда взять |
|------------|--------------|
| `BOT_TOKEN` | у [@BotFather](https://t.me/BotFather) |
| `ALLOWED_USERS` | Telegram user_id через запятую; узнать — [@userinfobot](https://t.me/userinfobot) |
| `SERVER_URL` | адрес панели WDTT, обычно `https://IP:2860/wdtt` |
| `SERVER_USERNAME` / `SERVER_PASSWORD` | логин/пароль панели WDTT |

### 4. Запустить

```bash
source venv/bin/activate
python bot.py
```

---

## Запуск как systemd-сервис

Создай `/etc/systemd/system/wdtt-bot.service` (пути подставь свои):

```ini
[Unit]
Description=WDTT Manager Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/wdtt-bot
EnvironmentFile=/opt/wdtt-bot/.env
ExecStart=/opt/wdtt-bot/venv/bin/python bot.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now wdtt-bot
journalctl -u wdtt-bot -f
```

> `User=root` работает из коробки; для повышения безопасности заведи отдельного пользователя и выдай ему права на каталог бота.

### Обновление

```bash
# установка из git
cd /opt/wdtt-bot && git pull
source venv/bin/activate && pip install -r requirements.txt
systemctl restart wdtt-bot

# Docker
docker compose pull && docker compose up -d
```

---

## Конфигурация (`.env`)

### Обязательные

| Переменная | Описание |
|------------|----------|
| `BOT_TOKEN` | токен бота от @BotFather |
| `ALLOWED_USERS` | Telegram user_id через запятую; всем остальным бот отвечает «Доступ запрещён» |

### Серверы WDTT

```env
SERVER_URL=https://127.0.0.1:2860/wdtt
SERVER_NAME=WDTT
SERVER_USERNAME=admin
SERVER_PASSWORD=wdtt
```

### Прочие переменные

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `CSQTT_PORT` | UDP-порт CSQTT-сервера для ссылок `csqtt://` | `46000` |
| `TZ` | часовой пояс, например `Europe/Moscow` | системный |
| `LOG_LEVEL` | `DEBUG` / `INFO` / `WARNING` / `ERROR` | `INFO` |
| `LOG_FILE` | файл логов (без значения — только stdout) | — |
| `LOG_MAX_MB` | макс. размер файла лога | `10` |
| `LOG_BACKUPS` | количество ротаций | `3` |
| `NO_COLOR` | отключить ANSI-цвета в консоли | — |
| `TELEGRAM_PROXY_URL` | прокси для Telegram API (`socks5://`, `http://`) | — |
| `WDTT_BOT_DB_PATH` | путь к базе SQLite | `./wdtt_bot.db` |
| `WDTT_BOT_HEARTBEAT_PATH` | файл healthcheck, обновляется каждые 20с | `/tmp/healthy` |

> Цвета в консоли включаются только в интерактивном терминале; в systemd/journald логи автоматически без ANSI.

### Пороги алертов

```env
ALERT_WDTT_DOWN=true         # алерт при недоступности WDTT
ALERT_XRAY_DOWN=true         # алерт при недоступности Xray
ALERT_USERS_LIMIT_PCT=80     # порог заполнения max_users, %
```

### Прокси для Telegram API

```env
TELEGRAM_PROXY_URL=socks5://user:password@host:port
TELEGRAM_PROXY_URL=http://host:port
```

Поддержка SOCKS5, SOCKS4, HTTP. Актуально, если сервер запущен в регионе с ограниченным доступом к api.telegram.org.

---

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие, главное меню |
| `/menu` | Главное меню (Dashboard текущего сервера) |
| `/find запрос` | Поиск пользователя по полному паролю или комментарию |
| `/cancel` | Отменить текущее действие (мастер создания, редактирование) |
| `/id` | Ваш Telegram ID |
| `/help` | Справка по командам |

---

## Меню бота

```
[📊 Dashboard]    [👥 Пользователи]
[🔧 Inbound]      [🔄 Сервисы]
[📡 Xray]         [🚨 Алерты]
[➕ Новый клиент]  [📤 Экспорт]
```

Вложенные экраны (карточка пользователя, пагинация, подтверждения) — inline-кнопки в сообщениях; в Dashboard есть кнопка «🟢 Онлайн» (активные сессии).

---

## Пользователи

### Создание (пошаговый мастер)

Кнопка «➕ Новый клиент»:

1. **Комментарий** — имя/описание (или `/skip`)
2. **Пароль** — вручную или `/gen` для автогенерации
3. **Срок** — дней до истечения (0 или `/skip` = бессрочно)
4. **Трафик** — лимит в GB (0 = без лимита)
5. **Устройства** — максимальное количество
6. **Max Down** — лимит скорости загрузки (Mbps, 0 = без лимита)
7. **Max Up** — лимит скорости отдачи (Mbps, 0 = без лимита)
8. **VK Hash** — обязательно, один или несколько хешей (до 4) через запятую или с новой строки
9. **Подтверждение** — `/confirm` создать, `/cancel` отменить

### Редактирование

Карточка пользователя → «✏️ Редактировать» → выбор поля: комментарий, пароль, срок (timestamp или дата `YYYY-MM-DD`), лимит трафика, устройства, скорости. При ошибке ввода бот попросит повторить — мастер не сбрасывается.

### Поиск

`/find` или кнопка «🔍 Поиск»: регистронезависимый поиск по **полному паролю** (`password_key`) или комментарию. Маскированный пароль из панели (`bhv****`) для поиска не используется.

---

## Трафик и графики

- История собирается автоматически каждые **15 минут** по всем серверам и хранится в SQLite 30 дней
- «📊 Трафик» в карточке пользователя — дельты и графики за 1/3/7/30 дней
- «📊 Отчёт» в меню трафика — топ пользователей по потреблению + общий график
- Сразу после запуска истории ещё нет — первые точки появятся через 15–30 минут, полный график за неделю наберётся за неделю

---

## Ссылки `csqtt://` и `wdtt://`

Бот показывает в карточке пользователя ссылки двух типов (кнопка «🔗 Ссылка»).

**CSQTT** (WRAP CSQTT-WRAP-v1 + VKQUIC) — для iOS VK Turn Proxy, CSQTT Android/Desktop:

```
csqtt://password@host:csqtt_port
```

Порт — UDP-порт CSQTT-сервера (по умолчанию **46000**, задаётся `CSQTT_PORT`); он не совпадает с портами WDTT (`dtls_port`/`wg_port`). В csqtt-ссылке нет ни VK-хеша, ни device_id — клиент авторизуется в VK сам. Пароль percent-кодируется (клиент декодирует сам).

**WDTT** — colon-формат:

```
wdtt://host:dtls_port:wg_port:local_port:password:vk_hash[#имя]
```

| Клиент | local_port | vk_hash | #имя |
|--------|------------|---------|------|
| iOS — VK Turn Proxy | `0` | первый хеш | нет |
| Android — WDTT | клиентский порт из inbound | все (до 4) | нет |
| PWDTT — Desktop | `0` | все (до 4) | да |
| WDTT — Windows | `0` | все (до 4) | да |

Хост берётся из `default_link_host` inbound; фолбэки — `server_host`, затем IP из base64-ссылки панели.

---

## WDTT API

Бот использует Panel API с cookie-сессией:

1. `POST /login` — получает cookie `wdtt-panel` (+ `wdtt-csrf` для write-запросов)
2. Все запросы идут с этой cookie; при 401 или HTML-ответе — автоматический re-login

| Метод | Путь | Описание |
|-------|------|----------|
| POST | `/login` | Авторизация |
| GET | `/panel/api/status` | Статус сервисов |
| GET | `/panel/api/inbound` | Настройки inbound |
| POST | `/panel/api/inbound/save` | Сохранить inbound |
| GET | `/panel/api/users` | Список пользователей |
| POST | `/panel/api/users/add` | Создать пользователя |
| POST | `/panel/api/users/update` | Обновить пользователя |
| POST | `/panel/api/users/delete` | Удалить пользователя |
| POST | `/panel/api/users/reset-traffic` | Сбросить трафик |
| POST | `/panel/api/server/restartWdttService` | Перезапуск WDTT |
| POST | `/panel/api/server/restartXrayService` | Перезапуск Xray |
| GET | `/panel/api/xray/config` | Xray конфиг |
| GET | `/panel/api/xray/versions` | Версии Xray |

---

## Структура проекта

```
wdtt-bot/
├── bot.py              # Точка входа (Dispatcher, polling, меню команд)
├── config.py           # Конфигурация из .env (серверы, пороги алертов)
├── api_client.py       # WdtClient (cookie-сессия, auto re-login)
├── database.py         # SQLite: история трафика, алерты
├── handlers.py         # Обработчики команд, reply-меню и callback
├── keyboards.py        # Reply-меню и inline-клавиатуры
├── formatters.py       # HTML-форматирование ответов API, ссылки csqtt/wdtt
├── scheduler.py        # Фоновые задачи: трафик (15м), health (2м), очистка, heartbeat
├── middlewares.py      # AuthMiddleware (allowlist по user_id)
├── states.py           # Состояния пошаговых мастеров (aiogram)
├── session.py          # Кеш WdtClient (cookie живёт между запросами)
├── charts.py           # Графики трафика (matplotlib, тёмная тема)
├── export_utils.py     # Экспорт CSV/Excel
├── logging_setup.py    # Цветной вывод, ротация файла логов
├── tz.py               # Часовые пояса
├── requirements.txt    # Зависимости
├── Dockerfile          # Образ (python:3.13-slim, non-root, healthcheck)
├── docker-compose.yml  # Compose с hardening и named volume
├── .env.example        # Шаблон конфигурации
└── .github/workflows/docker.yml   # CI: сборка и публикация в GHCR
```

---

## Зависимости

```
aiogram==3.27.0          # Telegram бот фреймворк
aiohttp==3.13.5          # Async HTTP клиент
aiohttp-socks==0.12.0    # SOCKS/HTTP прокси для Telegram API
aiosqlite==0.22.1        # Async SQLite
APScheduler==3.11.2      # Фоновые задачи
openpyxl==3.1.5          # Excel экспорт
matplotlib==3.10.3       # Графики
python-dotenv==1.2.2     # Загрузка .env
```

> На Python 3.14 в matplotlib 3.10.x есть баг рекурсии при отрисовке — бот содержит встроенную заплатку и работает на 3.14; в Docker-образе используется Python 3.13.

---

## Диагностика

| Симптом | Причина / решение |
|---------|-------------------|
| `unable to open database file` | БД лежит на read-only ФС. В Docker путь `/app/data/wdtt_bot.db` уже задан в compose; проверь, что том примонтирован |
| «Истории трафика пока нет» | Данные копятся с момента старта бота, точка каждые 15 минут |
| «Доступ запрещён» | Telegram ID не в `ALLOWED_USERS` |
| Бот не отвечает, контейнер unhealthy | Проверь `BOT_TOKEN` и доступность api.telegram.org (при нужде — `TELEGRAM_PROXY_URL`); логи: `docker compose logs -f` |
| `[auth_failed]` в логах | Неверные `SERVER_USERNAME`/`SERVER_PASSWORD` или панель отдаёт HTML — бот сам перелогинивается, проверь доступность `SERVER_URL` |

Логи: stdout (цветные) и опционально файл (`LOG_FILE`). В Docker — `docker compose logs -f`, в systemd — `journalctl -u wdtt-bot -f`.

---

## Лицензия

MIT
