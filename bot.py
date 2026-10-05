#!/usr/bin/env python3
"""
WDTT Manager Bot — Telegram bot for WDTT VPN Panel
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BotCommand,
    BotCommandScopeDefault,
    MenuButtonCommands,
)

from config import load_config
from handlers import router
from logging_setup import setup_logging
from middlewares import AuthMiddleware
import database as db
import scheduler as sched

setup_logging()
logger = logging.getLogger(__name__)

_menu_task: asyncio.Task | None = None


async def _install_bot_menu(bot: Bot):
    """Ставит меню команд в фоне с ретраями: недоступность api.telegram.org
    при старте не должна ронять бот — polling сам дождётся сети."""
    commands = [
        BotCommand(command="menu",    description="Главное меню"),
        BotCommand(command="find",    description="Поиск пользователя"),
        BotCommand(command="cancel",  description="Отменить текущее действие"),
        BotCommand(command="id",      description="Ваш Telegram ID"),
        BotCommand(command="help",    description="Справка по командам"),
    ]
    delay = 5
    while True:
        try:
            await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
            await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
            logger.info("Меню бота установлено (%d команд)", len(commands))
            return
        except Exception as e:
            logger.warning("Меню команд не установлено (%s: %s) — повтор через %dс", type(e).__name__, e, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


async def setup_bot_menu(bot: Bot):
    global _menu_task
    _menu_task = asyncio.create_task(_install_bot_menu(bot))


async def main():
    config = load_config()

    await db.init_db()

    bot_kwargs = dict(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    if config.tg_proxy_url:
        from aiogram.client.session.aiohttp import AiohttpSession
        bot_kwargs["session"] = AiohttpSession(proxy=config.tg_proxy_url)
        logger.info("Telegram прокси: %s", config.tg_proxy_url.split("@")[-1])

    bot = Bot(**bot_kwargs)
    dp = Dispatcher(storage=MemoryStorage())
    dp["config"] = config

    dp.message.middleware(AuthMiddleware(config.allowed_users))
    dp.callback_query.middleware(AuthMiddleware(config.allowed_users))

    dp.include_router(router)

    await setup_bot_menu(bot)
    sched.setup(bot, config)

    logger.info(
        "Бот запущен | серверов: %d | юзеров: %d",
        len(config.servers),
        len(config.allowed_users),
    )

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        sched.stop()
        if _menu_task and not _menu_task.done():
            _menu_task.cancel()
        await bot.session.close()
        logger.info("Бот остановлен")


if __name__ == "__main__":
    asyncio.run(main())
