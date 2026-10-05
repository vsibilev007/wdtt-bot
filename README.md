# WDTT Manager Bot

Telegram-бот для управления [WDTT VPN Panel](https://github.com/ildarmaga/wdtt) через Panel API.

**Совместимость:** WDTT Panel (API v1+)

## Возможности

- **Управление пользователями** — создание, редактирование, удаление, вкл/выкл, сброс трафика
- **Dashboard** — статус WDTT/Xray, IP, количество пользователей, статистика
- **Онлайн-сессии** — просмотр активных подключений (кто, IP, режим)
- **Inbound** — просмотр и редактирование настроек подключения (порты, DNS, max_users)
- **Сервисы** — перезапуск WDTT и Xray с подтверждением
- **Xray** — просмотр конфига, список доступных версий
- **Алерты** — уведомления при недоступности сервисов и приближении лимита пользователей
- **Трафик** — история трафика с графиками, отчёты по периодам
- **Экспорт** — выгрузка пользователей в CSV и Excel
- **Кластер HA** — список пользователей объединяется со всех узлов, write-операции на все узлы параллельно
- **Мультисервер** — переключение между серверами и кластерами прямо из меню

---

## Требования

| Компонент | Версия | Обязательно |
|-----------|--------|-------------|
| Python | 3.11+ | Да |
| WDTT Panel | с HTTP API | Да |
| matplotlib | — | Для графиков |

---

## Быстрый старт через Docker

### 1. Создать `.env`

```bash
cp .env.example .env
nano .env          # заполни BOT_TOKEN, ALLOWED_USERS, SERVER_URL
```

### 2. Запустить через Docker Compose

```bash
docker compose up -d
```

### Образ из GHCR

Образ автоматически публикуется в GitHub Container Registry при пуше в `main` или создании тега `v*`.

```bash
# Последняя версия с main
docker pull ghcr.io/vsibilev007/wdtt-bot:main

# Конкретная версия
docker pull ghcr.io/vsibilev007/wdtt-bot:1.0.0
```

Используй в `docker-compose.yml`:

```yaml
services:
  wdtt-bot:
    image: ghcr.io/vsibilev007/wdtt-bot:main
    # убери строку build: .
```

### Локальная сборка

```bash
docker build -t wdtt-bot .
docker run -d --env-file .env -v wdtt-data:/app/data --name wdtt-bot wdtt-bot
```

### Данные и том

База данных хранится в named volume `wdtt-data` (путь внутри контейнера — `/app/data`). **Не используй bind-mount** (`./data:/app/data`) — это перетирает права `appuser` и вызывает `unable to open database file`.

```bash
# Посмотреть данные
docker volume inspect wdtt-data

# Бэкап тома
docker run --rm -v wdtt-data:/data -v $(pwd):/backup alpine \
  tar czf /backup/wdtt-data.tar.gz -C /data .
```

### Параметры безопасности (включены в compose)

- `read_only: true` — файловая система контейнера только для чтения
- `cap_drop: ALL` — сброс всех Linux capabilities
- `no-new-privileges: true` — запрет эскалации привилегий
- `mem_limit: 256m` — лимит памяти
- Non-root пользователь `appuser` (UID 10001)

---

## Установка на свежей системе

### 1. Установить Python и зависимости

**Ubuntu / Debian:**

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
```

**CentOS / RHEL / AlmaLinux:**

```bash
sudo dnf install -y python3 python3-pip git
```

**Alpine:**

```bash
sudo apk add python3 py3-pip git
```

### 2. Клонировать репозиторий

```bash
git clone git@github.com:vsibilev007/wdtt-bot.git
cd wdtt-bot
```

### 3. Создать виртуальное окружение и установить зависимости

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Настроить конфигурацию

```bash
cp .env.example .env
nano .env
```

Заполни обязательные параметры:

```env
BOT_TOKEN=1234567890:AABBCCDDEEFFaabbccddeeff
ALLOWED_USERS=123456789
SERVER_URL=https://your-server:2860/wdtt
SERVER_NAME=My WDTT
SERVER_USERNAME=admin
SERVER_PASSWORD=wdtt
```

> `BOT_TOKEN` — получить у [@BotFather](https://t.me/BotFather).
> `ALLOWED_USERS` — Telegram user_id через запятую. Узнать: [@userinfobot](https://t.me/userinfobot).
> `SERVER_URL` — адрес панели WDTT (по умолчанию `https://IP:2860/wdtt`).
> `SERVER_USERNAME` / `SERVER_PASSWORD` — логин/пароль панели WDTT.

### 5. Запустить

```bash
source venv/bin/activate
python bot.py
```

Бот готов. Открой его в Telegram и нажми `/start`.

---

## Установка как systemd-сервис

Создай файл `/etc/systemd/system/wdtt-bot.service`:

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

> Замени `/opt/wdtt-bot` на реальный путь к проекту.

```bash
systemctl daemon-reload
systemctl enable --now wdtt-bot
journalctl -u wdtt-bot -f
```

---

## Конфигурация (`.env`)

### Обязательные

| Переменная | Описание |
|------------|----------|
| `BOT_TOKEN` | Токен бота от @BotFather |
| `ALLOWED_USERS` | Telegram user_id через запятую |

### Серверы

**Один сервер:**

```env
SERVER_URL=https://127.0.0.1:2860/wdtt
SERVER_NAME=WDTT
SERVER_USERNAME=admin
SERVER_PASSWORD=wdtt
```

**Несколько серверов:**

```env
SERVER_1_URL=https://10.0.0.1:2860/wdtt
SERVER_1_NAME=Main
SERVER_1_USERNAME=admin
SERVER_1_PASSWORD=wdtt

SERVER_2_URL=https://10.0.0.2:2860/wdtt
SERVER_2_NAME=Backup
SERVER_2_USERNAME=admin
SERVER_2_PASSWORD=wdtt
```

**Кластер HA** — серверы с одинаковым `GROUP`:

```env
SERVER_1_URL=https://10.0.0.1:2860/wdtt
SERVER_1_NAME=HA_A
SERVER_1_USERNAME=admin
SERVER_1_PASSWORD=wdtt
SERVER_1_GROUP=cluster_ha

SERVER_2_URL=https://10.0.0.2:2860/wdtt
SERVER_2_NAME=HA_B
SERVER_2_USERNAME=admin
SERVER_2_PASSWORD=wdtt
SERVER_2_GROUP=cluster_ha
```

### Прочие параметры

| Переменная | Описание | По умолчанию |
|------------|----------|--------------|
| `TZ` | Часовой пояс | системный |
| `LOG_LEVEL` | Уровень логов | `INFO` |
| `LOG_FILE` | Файл логов | — (stdout) |
| `LOG_MAX_MB` | Макс. размер файла | `10` |
| `LOG_BACKUPS` | Кол-во бэкапов | `3` |
| `NO_COLOR` | Отключить ANSI | — |
| `TELEGRAM_PROXY_URL` | Прокси для Telegram API | — |
| `WDTT_BOT_DB_PATH` | Путь к базе данных | `./wdtt_bot.db` |

### Пороги алертов

```env
ALERT_WDTT_DOWN=true         # алерт при недоступности WDTT
ALERT_XRAY_DOWN=true         # алерт при недоступности Xray
ALERT_USERS_LIMIT_PCT=80     # порог заполненности пользователей, %
```

### Прокси для Telegram API

```env
TELEGRAM_PROXY_URL=socks5://user:password@host:port
TELEGRAM_PROXY_URL=http://host:port
```

Поддержка SOCKS5, SOCKS4, HTTP.

---

## Команды

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие и главное меню |
| `/menu` | Главное меню (Dashboard) |
| `/find запрос` | Поиск пользователя по паролю или комментарию |
| `/cancel` | Отменить текущее действие (мастер создания и т.п.) |
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

При наличии нескольких серверов — внизу меню переключатель серверов. Внутри Dashboard — кнопка «🟢 Онлайн» (активные сессии).

---

## Алерты

| Тип | Событие | Cooldown |
|-----|---------|----------|
| `wdtt_down` | WDTT сервис недоступен или панель не отвечает | 5 мин |
| `xray_down` | Xray сервис недоступен | 5 мин |
| `users_limit` | Количество пользователей ≥ порога от max_users | 5 мин |

Настройка через меню «🚨 Алерты» — кнопка переключения для каждого типа. История последних 20 алертов — кнопка «📋 История алертов».

---

## Управление пользователями

### Создание (FSM-мастер)

Через кнопку «➕ Новый клиент» или `user:add`:

1. **Комментарий** — имя/описание (или `/skip`)
2. **Пароль** — вручную или `/gen` для автогенерации
3. **Срок** — дней до истечения (0 = бессрочно, `/skip` = бессрочно)
4. **Трафик** — лимит в GB (0 = без лимита)
5. **Устройства** — макс. количество
6. **Max Down** — лимит скорости загрузки (Mbps)
7. **Max Up** — лимит скорости отдачи (Mbps)
8. **VK Hash** — обязательно, один или несколько хешей (до 4) через запятую
9. **Подтверждение** — `/confirm` для создания, `/cancel` для отмены

### Редактирование

Из карточки пользователя → «✏️ Редактировать» → выбор поля:
- Комментарий, пароль, срок, трафик, устройства, скорости

### Кластерные операции

На кластере список пользователей объединяется со всех узлов (онлайн-статус показывается по каждому узлу), а операции создания/удаления/переключения/сброса трафика выполняются параллельно на всех узлах. Результат по каждому узлу:

```
✅ HA_A
✅ HA_B
```

---

## WDTT API

Бот использует WDTT Panel API с cookie-сессионной авторизацией:

1. `POST /login` — получает cookie `wdtt-panel`
2. Все запросы — с этой cookie
3. При 401 — автоматический re-login

### Используемые эндпоинты

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
| POST | `/panel/api/password/main` | Сменить главный пароль |
| GET | `/panel/api/xray/config` | Xray конфиг |
| POST | `/panel/api/xray/config` | Сохранить Xray конфиг |
| GET | `/panel/api/xray/versions` | Версии Xray |
| POST | `/panel/api/xray/install/{tag}` | Установить версию Xray |

---

## Ссылки `wdtt://`

Бот показывает в карточке пользователя готовые ссылки формата:

```
wdtt://host:dtls_port:wg_port:local_port:password:vk_hash[#имя]
```

| Клиент | local_port | vk_hash | #имя |
|--------|------------|---------|------|
| iOS — VK Turn Proxy | `0` | первый хеш | нет |
| Android — WDTT | клиентский порт из inbound | все (до 4) | нет |
| PWDTT — Desktop | `0` | все (до 4) | да |
| WDTT — Windows | `0` | все (до 4) | да |

Хост берётся из `default_link_host` inbound (фолбэк — `server_host`).

Бот также показывает базовую ссылку подключения в карточке пользователя.

---

## Структура проекта

```
wdtt-bot/
├── bot.py              # Точка входа (Dispatcher, Router, polling)
├── config.py           # Конфигурация из .env
├── api_client.py       # WdtClient + кластерные операции
├── database.py         # SQLite: трафик, алерты, сессии
├── handlers.py         # Обработчики команд и callback
├── keyboards.py        # Inline-клавиатуры
├── formatters.py       # HTML-форматирование ответов API
├── scheduler.py        # Фоновые задачи: трафик, health, cleanup, heartbeat
├── middlewares.py       # AuthMiddleware (allowlist по user_id)
├── states.py           # FSM состояния (aiogram)
├── session.py          # Выбор сервера (сохраняется в БД)
├── charts.py           # Графики трафика (matplotlib)
├── export_utils.py     # Экспорт CSV/Excel
├── logging_setup.py    # Цветной вывод, ротация файла логов
├── tz.py               # Часовые пояса
├── requirements.txt    # Зависимости
├── Dockerfile          # Multi-stage Docker образ
├── docker-compose.yml  # Docker Compose конфиг
├── .env.example        # Шаблон конфигурации
├── .gitignore
├── .dockerignore
└── .github/
    └── workflows/
        └── docker.yml  # CI/CD: сборка и публикация в GHCR
```

---

## Зависимости

```
aiogram==3.27.0          # Telegram бот фреймворк
aiohttp==3.13.5          # Async HTTP клиент
aiosqlite==0.22.1        # Async SQLite
APScheduler==3.11.2       # Фоновые задачи
openpyxl==3.1.5           # Excel экспорт
matplotlib==3.10.3        # Графики
python-dotenv==1.2.2      # Загрузка .env
```

---

## Лицензия

MIT
